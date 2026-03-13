"""
main.py -- FastAPI application for Jenkins Performance Analyzer.
"""
from __future__ import annotations
import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Optional

import yaml
import httpx
from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ai_service import ai_complete, get_ai_provider, AIServiceError
from config import get_config, reload_config, AppConfig
from github_service import (
    fetch_all_repo_contexts,
    correlate_methods_with_log,
    build_source_context_summary,
    build_error_source_context,
    fetch_full_method_bodies,
    GitHubClient,
)
from log_parser import (
    JenkinsLogParser,
    ParseResult,
    serialize_call_tree,
    build_analysis_prompt,
    build_failure_analysis_prompt,
    build_focused_prompt,
    split_into_batches,
    build_batch_prompt,
    build_synthesis_prompt,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Jenkins Performance Analyzer API",
    description="AI-powered Jenkins build log performance analysis",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# -- Private-only mode guard ---------------------------------------------------
#
# private_only_mode blocks public cloud services only.
# The following are NEVER blocked (on-prem / private network):
#   - GitHub Enterprise  (github.type: "private" with a custom base URL)
#   - Private AI         (provider: ollama or provider: private)
#   - Jenkins server     (on-prem, no outbound calls to it yet)
#   - Fine-tuning jobs   (future feature, private endpoint)
#
# The following ARE blocked when private_only_mode is enabled:
#   - Anthropic API      (api.anthropic.com)
#   - Public GitHub      (github.com -- github.type: "public")

def _provider_is_public_cloud(provider: str) -> bool:
    """Anthropic is the only provider that calls the public internet."""
    return provider == "anthropic"


def _github_is_public(github_type: str) -> bool:
    """
    github.type == "public"  -> github.com (public internet).
    github.type == "private" -> GitHub Enterprise on a private/on-prem URL.
                                This is ALLOWED in private_only_mode.
    """
    return github_type == "public"


def check_private_only(action: str) -> None:
    """
    Raise HTTP 403 if private_only_mode is on and the action calls a
    public cloud service. Private and on-prem resources are never affected.
    """
    cfg = get_config()
    if cfg.network.private_only_mode:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Blocked by private_only_mode: '{action}' calls a public cloud "
                f"service. Switch to a local/private provider (ollama or private). "
                f"GitHub Enterprise and private AI endpoints are unaffected."
            ),
        )


# -- Request / Response models -------------------------------------------------

class AnalyzeRequest(BaseModel):
    log_text: str
    pipeline_tags: Optional[list[str]] = None
    ai_provider: Optional[str] = None
    include_source: bool = True
    # Focus tells the AI what to concentrate on.
    # "auto"          -- pick best prompt based on what was detected (default)
    # "errors"        -- root cause analysis of detected errors
    # "performance"   -- bottleneck and slow method analysis
    # "full"          -- errors + performance combined
    # "stage:<name>"  -- deep-dive a specific stage
    # "custom:<text>" -- answer a specific user question about the log
    focus: str = "auto"


class AnalyzeResponse(BaseModel):
    stages: list[dict]
    timing_stats: list[dict]
    call_tree: list[dict]
    detected_tags: list[str]
    total_duration: float
    log_lines: int
    warnings: list[str]
    source_methods_matched: int
    ai_report: str
    # Error / failure analysis
    build_failed: bool = False
    errors: list[dict] = []
    failed_methods: list[str] = []
    failure_report: str = ""    # AI failure analysis (separate from perf report)


class ConfigUpdateRequest(BaseModel):
    config: dict


class RepoTestRequest(BaseModel):
    url: str
    branch: str = "main"
    paths: list[str] = ["src/"]
    extensions: list[str] = [".groovy", ".java"]
    # Live UI values -- if provided, override saved config for this test only
    token: str = ""
    api_url: str = ""
    verify_ssl: bool = True
    github_type: str = ""  # "public" | "private" -- live UI value for private_only check


class HealthResponse(BaseModel):
    status: str
    ai_provider: str
    gpu_enabled: bool
    github_type: str
    repos_enabled: int
    pipeline_tags: list[str]
    private_only_mode: bool
    batch_mode: str
    batch_threshold_lines: int


# -- Helpers -------------------------------------------------------------------

def build_parser(override_tags: Optional[list[str]] = None) -> JenkinsLogParser:
    cfg = get_config()
    # Use override only when it's a non-empty list; empty list means "use config"
    if override_tags is not None and len(override_tags) > 0:
        # Strip whitespace from each tag in case of sloppy input
        tags = [t.strip() for t in override_tags if t.strip()]
    else:
        tags = cfg.pipeline.static_tags
    logger.info(f"Parser tags: {tags} (override={override_tags!r})")
    return JenkinsLogParser(
        static_tags=tags,
        method_start_pattern=cfg.pipeline.method_start_pattern,
        timing_pattern=cfg.pipeline.timing_pattern,
        stage_pattern=cfg.pipeline.stage_pattern,
        timestamp_pattern=cfg.pipeline.timestamp_pattern,
        slow_percentile=cfg.analysis.slow_method_percentile,
    )


def result_to_dict(result: ParseResult) -> dict:
    return {
        "stages": [
            {
                "name": s.name,
                "start_line": s.start_line,
                "end_line": s.end_line,
                "timestamp": s.timestamp,
                "total_time": round(s.total_time, 3),
                "methods": s.methods,
            }
            for s in result.stages
        ],
        "timing_stats": [
            {
                "name": s.name,
                "service_tags": s.service_tags,
                "total": s.total,
                "avg": s.avg,
                "calls": s.calls,
                "max": s.max,
                "min": s.min,
                "p95": s.p95,
                "is_slow": s.is_slow,
            }
            for s in result.timing_stats
        ],
        "call_tree": serialize_call_tree(result.call_tree),
        "detected_tags": result.detected_tags,
        "total_duration": result.total_duration,
        "log_lines": result.log_lines,
        "warnings": result.warnings,
        "build_failed": result.build_failed,
        "failed_methods": result.failed_methods,
        "errors": [
            {
                "error_type": e.error_type,
                "message": e.message,
                "line_number": e.line_number,
                "stage": e.stage,
                "failed_method": e.failed_method,
                "context_lines": e.context_lines,
                "stack_trace": e.stack_trace,
                "exit_code": e.exit_code,
            }
            for e in result.errors
        ],
    }


# -- Routes --------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
async def health():
    cfg = get_config()
    return HealthResponse(
        status="ok",
        ai_provider=cfg.ai.provider,
        gpu_enabled=cfg.ai.gpu_enabled,
        github_type=cfg.github.type,
        repos_enabled=sum(1 for r in cfg.github.repos if r.enabled),
        pipeline_tags=cfg.pipeline.static_tags,
        private_only_mode=cfg.network.private_only_mode,
        batch_mode=cfg.analysis.batch_mode,
        batch_threshold_lines=cfg.analysis.batch_threshold_lines,
    )


@app.get("/api/ollama/health")
async def ollama_health():
    """Check whether Ollama is reachable and the configured model is available."""
    cfg = get_config()
    if cfg.ai.provider != "ollama":
        return {"status": "not_configured", "provider": cfg.ai.provider}
    base_url = cfg.ai.ollama.base_url
    model = cfg.ai.ollama.model
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{base_url.rstrip('/')}/api/tags")
            resp.raise_for_status()
            tags = resp.json()
            available = [m["name"] for m in tags.get("models", [])]
            model_ready = any(m == model or m.startswith(model.split(":")[0]) for m in available)
            return {
                "status": "ok" if model_ready else "model_missing",
                "base_url": base_url,
                "model": model,
                "model_ready": model_ready,
                "available_models": available,
                "fix": None if model_ready else f"docker exec jenkins-analyzer-ollama ollama pull {model}",
            }
    except httpx.ConnectError:
        return {
            "status": "unreachable",
            "base_url": base_url,
            "model": model,
            "model_ready": False,
            "fix": "Ollama container not running or OLLAMA_BASE_URL wrong. Check: docker ps | grep ollama",
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.post("/api/parse")
async def parse_log(req: AnalyzeRequest):
    """Parse only -- no AI or GitHub calls. Always safe."""
    if not req.log_text.strip():
        raise HTTPException(400, "log_text is empty")
    parser = build_parser(req.pipeline_tags)
    result = parser.parse(req.log_text)
    return result_to_dict(result)


def _should_batch(log_text: str, cfg) -> bool:
    """Decide whether to use batch mode for this log."""
    mode = cfg.analysis.batch_mode
    if mode == "always":
        return True
    if mode == "never":
        return False
    # auto: batch if line count exceeds threshold
    line_count = log_text.count("\n")
    return line_count >= cfg.analysis.batch_threshold_lines


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze_log(req: AnalyzeRequest):
    """Full analysis: parse + optional source correlation + AI report."""
    if not req.log_text.strip():
        raise HTTPException(400, "log_text is empty")

    cfg = get_config()

    # Fail fast before any outbound call
    effective_provider = req.ai_provider or cfg.ai.provider
    if _provider_is_public_cloud(effective_provider):
        check_private_only(f"AI analysis via {effective_provider}")

    if req.include_source and any(r.enabled for r in cfg.github.repos):
        if _github_is_public(cfg.github.type):
            check_private_only("source fetch via public github.com")
        # github.type == "private" (GitHub Enterprise) passes through fine

    parser = build_parser(req.pipeline_tags)
    result = parser.parse(req.log_text)
    data = result_to_dict(result)

    # Source correlation -- two tiers:
    # 1. Standard perf correlation (all timed methods)
    # 2. Deep error correlation (full body for failed methods)
    source_context = ""
    error_source_context = ""
    source_matched = 0
    repo_contexts = []

    if req.include_source and any(r.enabled for r in cfg.github.repos):
        try:
            repo_contexts = await fetch_all_repo_contexts()
            all_method_names = [s["name"] for s in data["timing_stats"]]
            correlated = correlate_methods_with_log(all_method_names, repo_contexts)
            source_context = build_source_context_summary(
                correlated, max_chars=cfg.analysis.max_source_chars_for_ai
            )
            source_matched = len(correlated)
            logger.info(f"Source correlation: {source_matched} methods matched")

            # Deep fetch for failed methods (full body, not just 5-line snippet)
            if result.failed_methods:
                logger.info(f"Fetching full source for {len(result.failed_methods)} failed methods")
                full_bodies = await fetch_full_method_bodies(result.failed_methods, repo_contexts)
                all_for_error = correlate_methods_with_log(
                    all_method_names + result.failed_methods, repo_contexts
                )
                error_source_context = build_error_source_context(
                    all_for_error,
                    result.failed_methods,
                    full_bodies,
                    max_chars=cfg.analysis.max_source_chars_for_ai * 2,
                )
        except Exception as e:
            logger.warning(f"Source correlation failed: {e}")

    # Route to focused prompt based on req.focus
    focus = req.focus or "auto"
    combined_source = error_source_context or source_context

    try:
        sys_p, usr_p = build_focused_prompt(
            result, req.log_text,
            focus=focus,
            source_context=combined_source,
        )
        ai_report = await ai_complete(sys_p, usr_p, provider=req.ai_provider)
    except AIServiceError as e:
        ai_report = f"**AI analysis unavailable:** {e}"
    except Exception as e:
        logger.error(f"AI analysis error (focus={focus}): {e}", exc_info=True)
        ai_report = f"**AI analysis error:** {e}"

    return AnalyzeResponse(
        **data,
        source_methods_matched=source_matched,
        ai_report=ai_report,
        failure_report="",
    )


@app.post("/api/analyze/stream")
async def analyze_stream(req: AnalyzeRequest):
    """Streaming AI analysis -- returns SSE stream."""
    if not req.log_text.strip():
        raise HTTPException(400, "log_text is empty")

    cfg = get_config()

    effective_provider = req.ai_provider or cfg.ai.provider
    if _provider_is_public_cloud(effective_provider):
        check_private_only(f"AI stream via {effective_provider}")

    if req.include_source and any(r.enabled for r in cfg.github.repos):
        if _github_is_public(cfg.github.type):
            check_private_only("source fetch via public github.com")

    parser = build_parser(req.pipeline_tags)
    result = parser.parse(req.log_text)

    source_context = ""
    if req.include_source and any(r.enabled for r in cfg.github.repos):
        try:
            repo_contexts = await fetch_all_repo_contexts()
            method_names = [s.name for s in result.timing_stats]
            correlated = correlate_methods_with_log(method_names, repo_contexts)
            source_context = build_source_context_summary(correlated)
        except Exception as e:
            logger.warning(f"Source correlation: {e}")

    system_prompt, user_prompt = build_analysis_prompt(
        result, req.log_text, source_context=source_context,
        max_log_chars=cfg.analysis.max_log_chars_for_ai,
    )

    async def event_gen():
        try:
            provider = get_ai_provider(req.ai_provider)
            async for chunk in provider.stream(system_prompt, user_prompt):
                yield f"data: {chunk}\n\n"
        except Exception as e:
            yield f"data: [ERROR] {e}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.post("/api/analyze/batch")
async def analyze_batch(req: AnalyzeRequest):
    """
    Batch analysis for large logs -- streams SSE progress events.

    SSE event types:
      {"type": "start",    "total_batches": N, "log_lines": N}
      {"type": "progress", "batch": N, "total": N, "label": "Stage Build (2/5)"}
      {"type": "batch_done","batch": N, "partial_report": "..."}
      {"type": "synthesis","message": "Synthesizing N segment reports..."}
      {"type": "done",     "final_report": "...", "source_matched": N}
      {"type": "error",    "message": "..."}
    """
    if not req.log_text.strip():
        raise HTTPException(400, "log_text is empty")

    cfg = get_config()

    effective_provider = req.ai_provider or cfg.ai.provider
    if _provider_is_public_cloud(effective_provider):
        check_private_only(f"batch AI analysis via {effective_provider}")
    if req.include_source and any(r.enabled for r in cfg.github.repos):
        if _github_is_public(cfg.github.type):
            check_private_only("source fetch via public github.com")

    async def batch_gen():
        import json as _json

        def sse(obj: dict) -> str:
            return f"data: {_json.dumps(obj)}\n\n"

        try:
            parser = build_parser(req.pipeline_tags)
            result = parser.parse(req.log_text)

            # Source correlation (once, before batching)
            source_context = ""
            source_matched = 0
            if req.include_source and any(r.enabled for r in cfg.github.repos):
                try:
                    repo_contexts = await fetch_all_repo_contexts()
                    method_names = [s.name for s in result.timing_stats]
                    correlated = correlate_methods_with_log(method_names, repo_contexts)
                    source_context = build_source_context_summary(
                        correlated, max_chars=cfg.analysis.max_source_chars_for_ai
                    )
                    source_matched = len(correlated)
                except Exception as e:
                    logger.warning(f"Source correlation: {e}")

            batches = split_into_batches(
                result,
                req.log_text,
                max_stages_per_batch=cfg.analysis.batch_max_stages,
                max_log_chars_per_batch=cfg.analysis.batch_max_log_chars,
            )

            total = len(batches)
            yield sse({"type": "start", "total_batches": total, "log_lines": result.log_lines})

            provider = get_ai_provider(req.ai_provider)
            batch_reports: list[str] = []
            rolling_context = ""  # key findings passed forward to each next batch

            for b in batches:
                # Attach rolling context from all previous batches
                b.prior_context = rolling_context

                stage_names = ", ".join(s.name for s in b.stages) or f"lines {b.line_start}-{b.line_end}"
                label = f"{stage_names} ({b.batch_index + 1}/{total})"
                yield sse({"type": "progress", "batch": b.batch_index + 1, "total": total, "label": label})

                sys_p, usr_p = build_batch_prompt(b)
                # Append source context to first batch only (avoid repeating it)
                if b.batch_index == 0 and source_context:
                    usr_p += f"\n\n## Source Code Context\n{source_context}"

                try:
                    report = await provider.complete(sys_p, usr_p, timeout=cfg.ai.ollama.timeout if cfg.ai.provider == "ollama" else None)
                except Exception as e:
                    report = f"[Batch {b.batch_index + 1} error: {e}]"

                batch_reports.append(report)
                yield sse({"type": "batch_done", "batch": b.batch_index + 1, "partial_report": report})

                # Build rolling context: extract errors and key observations from this report
                # Keep it compact -- only the most causality-relevant lines (max 400 chars)
                rolling_lines = []
                for line in report.split("\n"):
                    l = line.strip()
                    if any(kw in l.lower() for kw in ("error", "fail", "exception", "timeout",
                                                       "credential", "null", "not found",
                                                       "key observation", "health", "anomal")):
                        rolling_lines.append(l)
                segment_label = f"Segment {b.batch_index + 1} ({stage_names})"
                summary = "\n".join(rolling_lines[:6])[:400]
                if summary:
                    rolling_context += f"\n### {segment_label}\n{summary}\n"

            # Synthesis pass
            yield sse({"type": "synthesis", "message": f"Synthesising {total} segment reports..."})
            if total == 1:
                # No need for a second call when there was only one batch
                final_report = batch_reports[0]
            else:
                global_summary = batches[0].global_summary if batches else ""
                focus = req.focus or "auto"
                sys_s, usr_s = build_synthesis_prompt(batch_reports, result, global_summary, focus=focus)
                synthesis_timeout = cfg.ai.ollama.synthesis_timeout if cfg.ai.provider == "ollama" else 600
                logger.info(f"Synthesis: {len(batch_reports)} segments, prompt={len(usr_s)} chars, timeout={synthesis_timeout}s")
                try:
                    # Run synthesis and send SSE keepalive pings every 20s so nginx
                    # proxy_read_timeout doesn't kill the connection during long Ollama calls
                    synthesis_task = asyncio.create_task(
                        provider.complete(sys_s, usr_s, timeout=synthesis_timeout)
                    )
                    ping_interval = 20
                    elapsed = 0
                    while not synthesis_task.done():
                        try:
                            final_report = await asyncio.wait_for(
                                asyncio.shield(synthesis_task), timeout=ping_interval
                            )
                            break
                        except asyncio.TimeoutError:
                            elapsed += ping_interval
                            yield sse({"type": "ping", "message": f"Synthesising... ({elapsed}s)"})
                except Exception as e:
                    logger.error(f"Synthesis failed: {e}")
                    # Fall back to concatenating reports with a header
                    final_report = "## Build AI Analysis (segments)\n\n" + "\n\n---\n\n".join(batch_reports)

            yield sse({"type": "done", "final_report": final_report, "source_matched": source_matched})

        except HTTPException as e:
            yield sse({"type": "error", "message": e.detail})
        except Exception as e:
            logger.error(f"Batch analysis error: {e}", exc_info=True)
            yield sse({"type": "error", "message": str(e)})

    return StreamingResponse(batch_gen(), media_type="text/event-stream")


@app.post("/api/upload")
async def upload_log(file: UploadFile = File(...)):
    """Upload a .txt log file and return its content."""
    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    return {"filename": file.filename, "size": len(content), "content": text}


# -- Config endpoints ----------------------------------------------------------

@app.get("/api/config")
async def get_cfg():
    """Return current configuration (sensitive keys masked)."""
    cfg = get_config()
    d = cfg.model_dump()
    if d["ai"]["anthropic"]["api_key"]:
        d["ai"]["anthropic"]["api_key"] = "***"
    if d["ai"]["private"]["api_key"]:
        d["ai"]["private"]["api_key"] = "***"
    if d["github"]["token"]:
        d["github"]["token"] = "***"
    return d


@app.put("/api/config")
async def update_cfg(req: ConfigUpdateRequest):
    """Update config -- writes to writable volume, read-only mount is never touched."""
    # Always write to the writable volume
    write_path = Path("/app/config_rw/config.yaml")
    if not write_path.parent.exists():
        # Fallback for local dev outside Docker
        write_path = Path("config/config.yaml")

    # Read existing saved config (from rw volume if present) as base
    existing: dict = {}
    if write_path.exists():
        with open(write_path) as f:
            existing = yaml.safe_load(f) or {}
    else:
        # First save -- seed from the read-only defaults so we don't lose them
        for ro_path in [Path("config/config.yaml"), Path("../config/config.yaml"), Path("/app/config/config.yaml")]:
            if ro_path.exists():
                with open(ro_path) as f:
                    existing = yaml.safe_load(f) or {}
                break

    def deep_merge(base: dict, override: dict) -> dict:
        result = dict(base)
        for k, v in override.items():
            if isinstance(v, dict) and isinstance(result.get(k), dict):
                result[k] = deep_merge(result[k], v)
            elif v not in ("***", None):
                result[k] = v
        return result

    merged = deep_merge(existing, req.config)

    write_path.parent.mkdir(parents=True, exist_ok=True)
    with open(write_path, "w") as f:
        yaml.dump(merged, f, default_flow_style=False, sort_keys=False)

    reload_config()
    return {"status": "saved", "path": str(write_path)}


@app.post("/api/config/test-ai")
async def test_ai(body: dict = None):
    """Test current AI provider connectivity."""
    cfg = get_config()
    provider_override = (body or {}).get("provider")
    effective_provider = provider_override or cfg.ai.provider
    if _provider_is_public_cloud(effective_provider):
        check_private_only(f"AI connectivity test via {effective_provider}")
    try:
        response = await ai_complete(
            "You are a test assistant.",
            "Reply with exactly: OK",
            provider=provider_override,
        )
        return {"status": "ok", "response": response[:100]}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/api/config/test-github")
async def test_github(req: RepoTestRequest):
    """Test GitHub repo access using live UI values (not requiring a prior save)."""
    cfg = get_config()
    # Use live type from UI if provided, fall back to saved config
    effective_type = req.github_type if req.github_type else cfg.github.type
    # Only block public github.com -- GitHub Enterprise is always allowed
    if _github_is_public(effective_type):
        check_private_only("connectivity test via public github.com")

    # Use live values from UI if provided, fall back to saved config
    effective_token = req.token if req.token and req.token != "***" else cfg.github.token
    effective_api_url = req.api_url if req.api_url else cfg.github.api_url
    effective_verify_ssl = req.verify_ssl if req.verify_ssl is not None else cfg.github.verify_ssl

    client = GitHubClient(
        token=effective_token,
        timeout=30,
        verify_ssl=effective_verify_ssl,
        api_url=effective_api_url,
    )
    try:
        import re as _re
        m = _re.search(r"https?://[^/]+/([^/]+)/([^/\s]+?)(?:\.git)?$", req.url.strip())
        if not m:
            return {"status": "error", "error": f"Cannot parse owner/repo from URL: {req.url}"}
        owner, repo = m.group(1), m.group(2)
        api_base = (effective_api_url.rstrip("/") if effective_api_url else "https://api.github.com")
        files = await client.list_tree(owner, repo, req.branch)
        matching = [
            f["path"] for f in files
            if any(f["path"].endswith(ext) for ext in req.extensions)
        ]
        return {
            "status": "ok",
            "total_files": len(files),
            "matching_files": len(matching),
            "sample": matching[:10],
            "api_base": api_base,
        }
    except Exception as e:
        api_base = (effective_api_url.rstrip("/") if effective_api_url else "https://api.github.com")
        return {"status": "error", "error": str(e), "api_base": api_base, "hint": "Check api_url, token, and verify_ssl settings"}
    finally:
        await client.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)

"""
github_service.py -- Fetches source code from GitHub repos and extracts
method/function signatures for log correlation.
"""
from __future__ import annotations
import asyncio
import base64
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx

from config import RepoConfig, get_config

logger = logging.getLogger(__name__)


@dataclass
class MethodSignature:
    name: str
    file: str
    repo: str
    line_number: int
    language: str
    source_snippet: str = ""      # a few lines around the method
    parameters: list[str] = field(default_factory=list)
    return_type: str = ""


@dataclass
class RepoSourceContext:
    repo_url: str
    branch: str
    methods: list[MethodSignature] = field(default_factory=list)
    files_scanned: int = 0
    error: str = ""


# -- Language parsers ----------------------------------------------------------

class GroovyParser:
    """Extract method definitions from Groovy/Jenkinsfile source."""
    METHOD_RE = re.compile(
        r"^(?:def|void|String|int|boolean|List|Map)\s+([\w_]+)\s*\(([^)]*)\)",
        re.MULTILINE,
    )
    STEP_RE = re.compile(r"^def\s+([\w_]+)\s*\(", re.MULTILINE)  # pipeline steps

    @classmethod
    def extract(cls, source: str, filename: str, repo: str) -> list[MethodSignature]:
        results = []
        lines = source.split("\n")
        for m in cls.METHOD_RE.finditer(source):
            line_no = source[: m.start()].count("\n") + 1
            params = [p.strip() for p in m.group(2).split(",") if p.strip()]
            snippet = "\n".join(lines[max(0, line_no - 1): line_no + 5])
            results.append(
                MethodSignature(
                    name=m.group(1),
                    file=filename,
                    repo=repo,
                    line_number=line_no,
                    language="groovy",
                    parameters=params,
                    source_snippet=snippet,
                )
            )
        return results


class JavaParser:
    METHOD_RE = re.compile(
        r"(?:public|private|protected|static|\s)+[\w<>\[\]]+\s+([\w_]+)\s*\(([^)]*)\)\s*(?:throws\s+[\w,\s]+)?\s*\{",
        re.MULTILINE,
    )

    @classmethod
    def extract(cls, source: str, filename: str, repo: str) -> list[MethodSignature]:
        results = []
        lines = source.split("\n")
        for m in cls.METHOD_RE.finditer(source):
            name = m.group(1)
            if name in ("if", "while", "for", "switch", "catch"):
                continue
            line_no = source[: m.start()].count("\n") + 1
            params = [p.strip().split()[-1] for p in m.group(2).split(",") if p.strip()]
            snippet = "\n".join(lines[max(0, line_no - 1): line_no + 5])
            results.append(
                MethodSignature(
                    name=name, file=filename, repo=repo,
                    line_number=line_no, language="java",
                    parameters=params, source_snippet=snippet,
                )
            )
        return results


class PythonParser:
    METHOD_RE = re.compile(r"^\s*(?:async\s+)?def\s+([\w_]+)\s*\(([^)]*)\)", re.MULTILINE)

    @classmethod
    def extract(cls, source: str, filename: str, repo: str) -> list[MethodSignature]:
        results = []
        lines = source.split("\n")
        for m in cls.METHOD_RE.finditer(source):
            line_no = source[: m.start()].count("\n") + 1
            params = [p.strip().split(":")[0].split("=")[0].strip()
                      for p in m.group(2).split(",") if p.strip() and p.strip() != "self"]
            snippet = "\n".join(lines[max(0, line_no - 1): line_no + 5])
            results.append(
                MethodSignature(
                    name=m.group(1), file=filename, repo=repo,
                    line_number=line_no, language="python",
                    parameters=params, source_snippet=snippet,
                )
            )
        return results


class JavaScriptParser:
    METHOD_RE = re.compile(
        r"(?:function\s+([\w_]+)\s*\(|(?:const|let|var)\s+([\w_]+)\s*=\s*(?:async\s*)?\()",
        re.MULTILINE,
    )

    @classmethod
    def extract(cls, source: str, filename: str, repo: str) -> list[MethodSignature]:
        results = []
        lines = source.split("\n")
        for m in cls.METHOD_RE.finditer(source):
            name = m.group(1) or m.group(2)
            line_no = source[: m.start()].count("\n") + 1
            snippet = "\n".join(lines[max(0, line_no - 1): line_no + 4])
            results.append(
                MethodSignature(
                    name=name, file=filename, repo=repo,
                    line_number=line_no, language="javascript",
                    source_snippet=snippet,
                )
            )
        return results


PARSERS = {
    ".groovy": GroovyParser,
    ".java": JavaParser,
    ".py": PythonParser,
    ".js": JavaScriptParser,
    ".ts": JavaScriptParser,
}


# -- GitHub API client ---------------------------------------------------------

class GitHubClient:
    API_BASE = "https://api.github.com"

    def __init__(self, token: str = "", timeout: int = 30):
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(
            base_url=self.API_BASE,
            headers=headers,
            timeout=timeout,
        )

    async def close(self):
        await self._client.aclose()

    def _parse_repo(self, url: str) -> tuple[str, str]:
        """Extract owner/repo from github.com URL."""
        m = re.search(r"github\.com[/:]([^/]+)/([^/\.]+)", url)
        if not m:
            raise ValueError(f"Cannot parse GitHub URL: {url}")
        return m.group(1), m.group(2)

    async def list_tree(self, owner: str, repo: str, branch: str, path: str = "") -> list[dict]:
        """List files in a directory recursively."""
        url = f"/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
        resp = await self._client.get(url)
        resp.raise_for_status()
        tree = resp.json().get("tree", [])
        return [
            item for item in tree
            if item["type"] == "blob" and (not path or item["path"].startswith(path))
        ]

    async def get_file(self, owner: str, repo: str, path: str, branch: str) -> str:
        """Fetch file content decoded from base64."""
        resp = await self._client.get(f"/repos/{owner}/{repo}/contents/{path}?ref={branch}")
        resp.raise_for_status()
        data = resp.json()
        content = data.get("content", "")
        return base64.b64decode(content.replace("\n", "")).decode("utf-8", errors="replace")

    async def fetch_repo_context(self, repo_cfg: RepoConfig) -> RepoSourceContext:
        owner, repo_name = self._parse_repo(repo_cfg.url)
        ctx = RepoSourceContext(repo_url=repo_cfg.url, branch=repo_cfg.branch)

        try:
            all_files = await self.list_tree(owner, repo_name, repo_cfg.branch)
        except Exception as e:
            ctx.error = str(e)
            logger.error(f"Failed to list tree for {repo_cfg.url}: {e}")
            return ctx

        # Filter by configured paths and extensions
        target_files = [
            f for f in all_files
            if any(f["path"].startswith(p.lstrip("/")) for p in repo_cfg.paths)
            and any(f["path"].endswith(ext) for ext in repo_cfg.extensions)
        ]

        tasks = [
            self._fetch_and_parse(owner, repo_name, f["path"], repo_cfg.branch, repo_cfg.url)
            for f in target_files[:50]  # cap at 50 files to avoid rate limits
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                ctx.methods.extend(result)
            elif isinstance(result, Exception):
                logger.warning(f"File parse error: {result}")

        ctx.files_scanned = len(target_files)
        logger.info(f"Repo {repo_cfg.url}: {len(ctx.methods)} methods from {ctx.files_scanned} files")
        return ctx

    async def _fetch_and_parse(
        self, owner: str, repo: str, path: str, branch: str, repo_url: str
    ) -> list[MethodSignature]:
        ext = "." + path.rsplit(".", 1)[-1] if "." in path else ""
        parser_cls = PARSERS.get(ext)
        if not parser_cls:
            return []
        try:
            source = await self.get_file(owner, repo, path, branch)
            return parser_cls.extract(source, path, repo_url)
        except Exception as e:
            logger.debug(f"Skip {path}: {e}")
            return []


# -- Source correlation --------------------------------------------------------

def correlate_methods_with_log(
    log_method_names: list[str],
    repo_contexts: list[RepoSourceContext],
) -> dict[str, list[MethodSignature]]:
    """
    Match log method names to source code signatures.
    Returns dict: method_name -> list of matching signatures.
    """
    # Build lookup: lowercase name -> signatures
    lookup: dict[str, list[MethodSignature]] = {}
    for ctx in repo_contexts:
        for sig in ctx.methods:
            key = sig.name.lower()
            lookup.setdefault(key, []).append(sig)

    result: dict[str, list[MethodSignature]] = {}
    for name in log_method_names:
        matches = lookup.get(name.lower(), [])
        # also try partial match (e.g. "build_image" matches "buildImage")
        if not matches:
            for key, sigs in lookup.items():
                if name.lower().replace("_", "") == key.replace("_", ""):
                    matches = sigs
                    break
        if matches:
            result[name] = matches

    return result


def build_source_context_summary(
    correlated: dict[str, list[MethodSignature]],
    max_chars: int = 3000,
) -> str:
    """Build a compact source context string for AI prompts."""
    lines = []
    total_chars = 0

    for method_name, sigs in correlated.items():
        for sig in sigs[:2]:  # max 2 implementations per method
            block = (
                f"\n## Method: {sig.name} [{sig.language}] -- {sig.file} (line {sig.line_number})\n"
                f"Repo: {sig.repo}\n"
                f"```{sig.language}\n{sig.source_snippet}\n```\n"
            )
            if total_chars + len(block) > max_chars:
                break
            lines.append(block)
            total_chars += len(block)

    return "\n".join(lines) if lines else ""


def build_error_source_context(
    correlated: dict[str, list["MethodSignature"]],
    failed_method_names: list[str],
    full_bodies: dict[str, str],
    max_chars: int = 5000,
) -> str:
    """
    Build a source context string specifically for failure analysis.
    Prioritises failed methods and includes their full bodies where available.
    """
    lines = []
    total_chars = 0
    seen: set[str] = set()

    # Failed methods first (with full bodies if available)
    priority_order = failed_method_names + [
        name for name in correlated if name not in failed_method_names
    ]

    for method_name in priority_order:
        sigs = correlated.get(method_name, [])
        if not sigs:
            continue

        for sig in sigs[:2]:
            key = f"{method_name}:{sig.file}"
            if key in seen:
                continue
            seen.add(key)

            body = full_bodies.get(method_name) or sig.source_snippet
            is_failed = method_name in failed_method_names
            label = " *** IMPLICATED IN FAILURE ***" if is_failed else ""

            block = (
                f"\n## {'[FAILED] ' if is_failed else ''}Method: {sig.name} [{sig.language}]"
                f"{label}\n"
                f"File: {sig.file}  |  Line: {sig.line_number}  |  Repo: {sig.repo}\n"
                f"```{sig.language}\n{body}\n```\n"
            )
            if total_chars + len(block) > max_chars:
                break
            lines.append(block)
            total_chars += len(block)

    return "\n".join(lines) if lines else ""


async def fetch_full_method_bodies(
    method_names: list[str],
    repo_contexts: list["RepoSourceContext"],
    max_lines: int = 60,
) -> dict[str, str]:
    """
    For a list of method names (typically failed ones), re-fetch the file and
    extract a fuller method body (up to max_lines) rather than just the
    5-line signature snippet captured during initial indexing.
    """
    if not method_names or not repo_contexts:
        return {}

    cfg = get_config()
    client = GitHubClient(token=cfg.github.token, timeout=cfg.github.timeout)

    # Build: method_name -> MethodSignature (first match)
    sig_lookup: dict[str, "MethodSignature"] = {}
    for ctx in repo_contexts:
        for sig in ctx.methods:
            if sig.name in method_names and sig.name not in sig_lookup:
                sig_lookup[sig.name] = sig

    if not sig_lookup:
        await client.close()
        return {}

    async def fetch_body(method_name: str, sig: "MethodSignature") -> tuple[str, str]:
        try:
            # Parse owner/repo from sig.repo URL
            import re as _re
            m = _re.search(r"github\.com[/:]([^/]+)/([^/.]+)", sig.repo)
            if not m:
                return method_name, sig.source_snippet
            owner, repo_name = m.group(1), m.group(2)
            # Use the branch from the first matching repo config
            branch = next(
                (r.branch for r in cfg.github.repos if r.url == sig.repo),
                "main",
            )
            source = await client.get_file(owner, repo_name, sig.file, branch)
            lines = source.split("\n")
            start = max(0, sig.line_number - 1)
            end = min(len(lines), start + max_lines)
            return method_name, "\n".join(lines[start:end])
        except Exception as e:
            logger.debug(f"Full body fetch failed for {method_name}: {e}")
            return method_name, sig.source_snippet

    try:
        tasks = [fetch_body(name, sig) for name, sig in sig_lookup.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        bodies = {}
        for r in results:
            if isinstance(r, tuple):
                bodies[r[0]] = r[1]
        return bodies
    finally:
        await client.close()


# -- Singleton fetch -----------------------------------------------------------


async def fetch_all_repo_contexts() -> list[RepoSourceContext]:
    cfg = get_config().github
    enabled_repos = [r for r in cfg.repos if r.enabled]
    if not enabled_repos:
        return []

    client = GitHubClient(token=cfg.token, timeout=cfg.timeout)
    try:
        tasks = [client.fetch_repo_context(r) for r in enabled_repos]
        return await asyncio.gather(*tasks)
    finally:
        await client.close()

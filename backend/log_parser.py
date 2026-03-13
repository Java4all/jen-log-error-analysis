"""
log_parser.py -- Jenkins log parser with dynamic source correlation.
Supports configurable pipeline tags and pattern-based method extraction.
"""
from __future__ import annotations
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# -- Data models ---------------------------------------------------------------

@dataclass
class MethodCall:
    name: str
    service_tag: str
    elapsed: Optional[float]
    indent: int
    line_number: int
    children: list["MethodCall"] = field(default_factory=list)
    log_lines: list[str] = field(default_factory=list)
    timestamp: Optional[str] = None


@dataclass
class Stage:
    name: str
    start_line: int
    end_line: int
    timestamp: Optional[str]
    methods: list[dict]         # flat list of {name, elapsed, service_tag}
    total_time: float = 0.0


@dataclass
class TimingStat:
    name: str
    service_tags: list[str]
    total: float
    avg: float
    calls: int
    max: float
    min: float
    p95: float
    all_values: list[float]
    is_slow: bool = False       # flagged by percentile analysis


@dataclass
class ParseResult:
    stages: list[Stage]
    timing_stats: list[TimingStat]
    call_tree: list[MethodCall]
    detected_tags: list[str]            # tags auto-detected from log
    total_duration: float
    log_lines: int
    warnings: list[str] = field(default_factory=list)
    errors: list["ErrorEvent"] = field(default_factory=list)
    build_failed: bool = False          # True if any hard failure detected
    failed_methods: list[str] = field(default_factory=list)  # methods implicated in failures


# -- Error detection models ---------------------------------------------------

@dataclass
class ErrorEvent:
    """A detected failure or error in the build log."""
    error_type: str            # EXCEPTION | BUILD_FAILED | TIMEOUT | EXIT_CODE | GENERIC
    message: str               # primary error message
    line_number: int
    stage: Optional[str]       # stage name if determinable
    failed_method: Optional[str]   # method that was executing when error occurred
    context_lines: list[str] = field(default_factory=list)  # +-5 lines around the error
    stack_trace: list[str] = field(default_factory=list)    # captured stack trace
    exit_code: Optional[int] = None




# -- Standard Jenkins pattern auto-detection -----------------------------------
# Cover real-world Jenkins/Pipeline/Maven/Gradle log formats so the tool works
# out-of-the-box without requiring custom pattern configuration.

# [Pipeline] { (Build)
_PIPELINE_STAGE_RE = re.compile(r"\[Pipeline\]\s+\{\s+\((.+?)\)")
# Maven plugin phase: [INFO] --- maven-surefire-plugin:2.22.2:test ...
_MAVEN_PHASE_RE = re.compile(r"\[INFO\]\s+---\s+[\w.\-]+:([\w\-]+)\s+")
# Time elapsed: 1.234 s
_MAVEN_ELAPSED_RE = re.compile(r"Time elapsed:\s*([\d.]+)\s*s", re.IGNORECASE)
# [INFO] Total time:  01:23 min  OR  5.678 s
_MAVEN_TOTAL_RE = re.compile(r"\[INFO\]\s+Total time:\s+(?:(\d+):(\d+)\s+min|([\d.]+)\s*s)")
# > Task :compileJava
_GRADLE_TASK_RE = re.compile(r"^>\s+Task\s+:([\w:]+)")
# BUILD SUCCESSFUL in 1m 23s
_GRADLE_TOTAL_RE = re.compile(r"BUILD (?:SUCCESSFUL|FAILED) in (?:(\d+)m\s+)?(\d+)s")
# Finished Stage 'X' in 1.2s
_FINISHED_STAGE_RE = re.compile(
    r"Finished\s+(?:Stage\s+)?['\"]?(.+?)['\"]?\s+in\s+([\d.]+)\s*s", re.IGNORECASE
)
# [stage:Build] or stage('Build')
_STAGE_LABEL_RE = re.compile(r"(?:\[stage:([^\]]+)\]|stage\(['\"]([^'\"]+)['\"]\))")


def _detect_log_format(lines: list[str], static_tags: list[str] | None = None) -> str:
    """Sniff the log to determine format.

    Custom format is detected when:
      - any configured static tag appears as '<tag>: <method>' anywhere in a line
        (handles timestamp/log-level prefixes: '[2024-01-15T10:00:30z] service-abc: method_1')
      - or 'time-elapsed-seconds' / 'StageName:' appears anywhere in the log.
    Full log is scanned (not just first 200 lines) since builds can have long preambles.
    """
    tag_res = [re.compile(rf"{re.escape(t)}:\s+\w") for t in (static_tags or [])]

    for line in lines:
        stripped = line.strip()
        if "time-elapsed-seconds" in stripped or "StageName:" in stripped:
            return "custom"
        for pat in tag_res:
            if pat.search(stripped):
                return "custom"

    # Fall back to standard format detection on first 200 lines
    sample = lines[:200]
    pipeline_hits = sum(1 for l in sample if "[Pipeline]" in l)
    maven_hits    = sum(1 for l in sample if "[INFO]" in l or "[ERROR]" in l)
    gradle_hits   = sum(1 for l in sample if l.strip().startswith("> Task") or "BUILD SUCCESSFUL" in l)
    if pipeline_hits >= 3:
        return "pipeline"
    if maven_hits >= 5:
        return "maven"
    if gradle_hits >= 2:
        return "gradle"
    return "unknown"


def _parse_standard(lines: list[str], fmt: str, slow_percentile: int) -> tuple:
    """
    Parse standard Jenkins/Maven/Gradle logs.
    Returns (stages, method_timings, method_tags, detected_format_label, warnings)
    """
    stages: list = []
    method_timings: dict = {}
    method_tags: dict = {}
    warnings: list[str] = []
    current_stage_name: Optional[str] = None
    current_stage_start = 0
    current_stage_time = 0.0
    pending_phase: Optional[str] = None  # Maven: open phase name waiting for timing

    for i, line in enumerate(lines):
        trimmed = line.strip()
        if not trimmed:
            continue

        # --- Stage detection ---
        stage_name = None
        if fmt == "pipeline":
            m = _PIPELINE_STAGE_RE.search(line)
            if m:
                stage_name = m.group(1).strip()
        elif fmt in ("maven", "gradle", "unknown"):
            m = _STAGE_LABEL_RE.search(line)
            if m:
                stage_name = (m.group(1) or m.group(2) or "").strip()
            fs = _FINISHED_STAGE_RE.search(line)
            if fs:
                # Treat as a completed stage with timing
                sname = fs.group(1).strip()
                elapsed = float(fs.group(2))
                method_timings.setdefault(sname, []).append(elapsed)
                method_tags.setdefault(sname, set()).add("stage")
                if not stages or stages[-1].name != sname:
                    stages.append(Stage(name=sname, start_line=i, end_line=i,
                                        methods=[{"name": sname, "elapsed": elapsed, "service_tag": "stage"}],
                                        total_time=elapsed))
                continue

        if stage_name:
            if current_stage_name:
                stages.append(Stage(
                    name=current_stage_name, start_line=current_stage_start,
                    end_line=i - 1, total_time=current_stage_time, methods=[],
                ))
            current_stage_name = stage_name
            current_stage_start = i
            current_stage_time = 0.0
            pending_phase = None
            continue

        # --- Timing detection ---
        elapsed = None
        method_name = None

        if fmt == "maven":
            pm = _MAVEN_PHASE_RE.search(line)
            if pm:
                pending_phase = pm.group(1)

            em = _MAVEN_ELAPSED_RE.search(line)
            if em:
                elapsed = float(em.group(1))
                method_name = pending_phase or current_stage_name or "test"
                pending_phase = None

            tm = _MAVEN_TOTAL_RE.search(line)
            if tm:
                if tm.group(1) and tm.group(2):
                    elapsed = int(tm.group(1)) * 60 + int(tm.group(2))
                else:
                    elapsed = float(tm.group(3))
                method_name = "total-build"

        elif fmt in ("gradle", "unknown"):
            gt = _GRADLE_TOTAL_RE.search(line)
            if gt:
                mins = int(gt.group(1)) if gt.group(1) else 0
                secs = int(gt.group(2))
                elapsed = mins * 60 + secs
                method_name = "total-build"

            tk = _GRADLE_TASK_RE.match(trimmed)
            if tk:
                # Track task as a method with unknown elapsed; pair with next timing if any
                pending_phase = tk.group(1).replace(":", "_")

            em = _MAVEN_ELAPSED_RE.search(line)
            if em:
                elapsed = float(em.group(1))
                method_name = pending_phase or current_stage_name or "task"
                pending_phase = None

        elif fmt == "pipeline":
            em = _MAVEN_ELAPSED_RE.search(line)
            if em:
                elapsed = float(em.group(1))
                method_name = pending_phase or current_stage_name or "step"
            gt = _GRADLE_TOTAL_RE.search(line)
            if gt:
                mins = int(gt.group(1)) if gt.group(1) else 0
                elapsed = mins * 60 + int(gt.group(2))
                method_name = "total-build"

        if elapsed is not None and method_name:
            method_timings.setdefault(method_name, []).append(elapsed)
            method_tags.setdefault(method_name, set()).add(fmt)
            current_stage_time += elapsed

    if current_stage_name:
        stages.append(Stage(
            name=current_stage_name, start_line=current_stage_start,
            end_line=len(lines) - 1, total_time=current_stage_time, methods=[],
        ))

    if not method_timings:
        warnings.append(
            f"No timing data found in {fmt} format log. "
            "For Maven logs ensure surefire outputs 'Time elapsed:' lines. "
            "For Gradle use '--profile'. "
            "For custom logs set timing_pattern in config."
        )
    if not stages:
        warnings.append(
            f"No pipeline stages detected in {fmt} format log. "
            "For Declarative Pipeline ensure stage('Name') blocks are present."
        )

    return stages, method_timings, method_tags, fmt, warnings


# -- Parser --------------------------------------------------------------------


class JenkinsLogParser:
    def __init__(
        self,
        static_tags: list[str],
        method_start_pattern: str = r"{tag}:\s*([\w_]+)",
        timing_pattern: str = r"([\w_]+)\s*\(?\)?:time-elapsed-seconds:([\d.]+)",
        stage_pattern: str = r"StageName:\s*(.+)",
        timestamp_pattern: str = r"\[(\d{4}-\d{2}-\d{2}T[\d:.]+z?)\]",
        slow_percentile: int = 80,
    ):
        self.static_tags = static_tags
        self.timing_re = re.compile(timing_pattern)
        self.stage_re = re.compile(stage_pattern)
        self.ts_re = re.compile(timestamp_pattern, re.IGNORECASE)
        self.slow_percentile = slow_percentile

        # Build combined method-start pattern for all configured tags
        # Also auto-detects unknown tags with a generic pattern
        self._tag_patterns: dict[str, re.Pattern] = {}
        for tag in static_tags:
            escaped = re.escape(tag)
            pat = method_start_pattern.replace("{tag}", escaped)
            self._tag_patterns[tag] = re.compile(pat)

        # Generic fallback: captures <word-word>: <method> style lines
        self._generic_method_re = re.compile(
            r"^([\w][\w-]*[\w]):\s+([\w_]+)\s*$"
        )

    def parse(self, raw_log: str) -> ParseResult:
        lines = raw_log.split("\n")

        # Auto-detect format; fall through to custom parser if patterns are configured
        fmt = _detect_log_format(lines, self.static_tags)
        if fmt != "custom":
            std_stages, std_timings, std_tags, fmt_label, std_warnings = \
                _parse_standard(lines, fmt, self.slow_percentile)
            # Only return standard results if timings were found.
            # If stages exist but no timings, fall through to the custom parser --
            # the log likely has [Pipeline] markers AND custom service-tag markers.
            if std_timings:
                return self._build_result(
                    lines, std_stages, std_timings, std_tags, [], set(),
                    std_warnings + ([f"Auto-detected log format: {fmt_label}"] if fmt_label != "unknown" else []),
                )
            extra_warn = []
        else:
            extra_warn = []

        stages: list[Stage] = []
        method_timings: dict[str, list[float]] = {}
        method_tags: dict[str, set[str]] = {}
        call_tree: list[MethodCall] = []
        stack: list[MethodCall] = []
        current_stage: Optional[Stage] = None
        detected_tags: set[str] = set()
        warnings: list[str] = extra_warn
        global_duration = 0.0

        for line_idx, line in enumerate(lines):
            trimmed = line.strip()
            if not trimmed:
                continue

            indent = len(line) - len(line.lstrip())
            ts_match = self.ts_re.search(line)
            ts = ts_match.group(1) if ts_match else None

            # -- Stage detection ----------------------------------------------
            # Use search() not match() so patterns find their target anywhere
            # in the line -- timestamps, log prefixes etc. are ignored naturally.
            stage_m = self.stage_re.search(trimmed)
            if stage_m:
                if current_stage:
                    current_stage.end_line = line_idx - 1
                    stages.append(current_stage)
                current_stage = Stage(
                    name=stage_m.group(1).strip(),
                    start_line=line_idx,
                    end_line=line_idx,
                    timestamp=ts,
                    methods=[],
                )
                stack.clear()
                continue

            # -- Timing line --------------------------------------------------
            timing_m = self.timing_re.search(trimmed)
            if timing_m:
                method_name = timing_m.group(1)
                elapsed = float(timing_m.group(2))

                method_timings.setdefault(method_name, []).append(elapsed)
                global_duration = max(global_duration, elapsed)

                # Match to open stack node
                for node in reversed(stack):
                    if node.name == method_name:
                        node.elapsed = elapsed
                        break

                if current_stage:
                    current_stage.methods.append({
                        "name": method_name,
                        "elapsed": elapsed,
                        "service_tag": "",
                    })
                    current_stage.total_time += elapsed
                continue

            # -- Method start detection ---------------------------------------
            matched_tag, matched_method = None, None

            # Try configured tags first
            for tag, pattern in self._tag_patterns.items():
                m = pattern.search(trimmed)
                if m:
                    matched_tag = tag
                    matched_method = m.group(1)
                    detected_tags.add(tag)
                    break

            # Auto-detect unknown tags (generic pattern)
            if matched_method is None:
                gm = self._generic_method_re.search(trimmed)
                if gm:
                    auto_tag = gm.group(1)
                    auto_method = gm.group(2)
                    if auto_tag not in ("method", "stage", "log", "info", "error", "warn"):
                        matched_tag = auto_tag
                        matched_method = auto_method
                        detected_tags.add(auto_tag)

            if matched_tag and matched_method:
                # Track which tags emitted this method
                method_tags.setdefault(matched_method, set()).add(matched_tag)

                node = MethodCall(
                    name=matched_method,
                    service_tag=matched_tag,
                    elapsed=None,
                    indent=indent,
                    line_number=line_idx,
                    timestamp=ts,
                )

                # Hierarchy: pop stack items at same or deeper indent
                while stack and stack[-1].indent >= indent:
                    popped = stack.pop()
                    if stack:
                        stack[-1].children.append(popped)
                    else:
                        call_tree.append(popped)

                stack.append(node)

        # Flush remaining stack
        while stack:
            node = stack.pop()
            if stack:
                stack[-1].children.append(node)
            else:
                call_tree.append(node)

        if current_stage:
            current_stage.end_line = len(lines) - 1
            stages.append(current_stage)

        if not method_timings:
            # Scan log for any lines that look like '<word>: <method>' to suggest real tags
            candidate_re = re.compile(r"(?:^|[\]\s])([a-zA-Z][\w-]{2,}[\w]):\s+[\w_]+\s*$")
            seen_candidates: set[str] = set()
            for raw_line in lines[:500]:
                cm = candidate_re.search(raw_line.strip())
                if cm:
                    cand = cm.group(1)
                    if cand.lower() not in {"info", "warn", "error", "debug", "trace",
                                            "stage", "method", "step", "pipeline"}:
                        seen_candidates.add(cand)
            hint = (
                f" Possible tag names seen in log: {sorted(seen_candidates)}."
                f" Update static_tags in config.yaml to match."
                if seen_candidates else ""
            )
            warnings.append(
                f"No timing markers found. "
                f"Searched for configured tags: {self.static_tags}.{hint} "
                "Expected start: '[timestamp] <tag>: method_name' and "
                "end: 'method_name():time-elapsed-seconds:1.23'."
            )
        if not stages:
            warnings.append("No stages detected.")
        if detected_tags - set(self.static_tags):
            auto = detected_tags - set(self.static_tags)
            warnings.append(f"Auto-detected pipeline tags not in config: {', '.join(sorted(auto))}")

        return self._build_result(
            lines, stages, method_timings, method_tags, call_tree, detected_tags, warnings,
            global_duration=global_duration,
        )

    # -- Shared result builder -------------------------------------------------

    def _build_result(
        self,
        lines: list[str],
        stages: list,
        method_timings: dict,
        method_tags: dict,
        call_tree: list,
        detected_tags: set,
        warnings: list[str],
        global_duration: float = 0.0,
    ) -> "ParseResult":
        all_totals = [sum(v) for v in method_timings.values()]
        slow_threshold = (
            float(sorted(all_totals)[int(len(all_totals) * self.slow_percentile / 100)])
            if all_totals else 0.0
        )

        timing_stats: list[TimingStat] = []
        for name, values in method_timings.items():
            total = sum(values)
            avg = statistics.mean(values)
            p95 = sorted(values)[int(len(values) * 0.95)] if len(values) > 1 else values[0]
            timing_stats.append(TimingStat(
                name=name,
                service_tags=list(method_tags.get(name, set())),
                total=round(total, 3),
                avg=round(avg, 3),
                calls=len(values),
                max=round(max(values), 3),
                min=round(min(values), 3),
                p95=round(p95, 3),
                all_values=values,
                is_slow=total >= slow_threshold,
            ))
        timing_stats.sort(key=lambda s: s.total, reverse=True)

        errors, failed_methods, build_failed = self._detect_errors(lines, stages)

        return ParseResult(
            stages=stages,
            timing_stats=timing_stats,
            call_tree=call_tree,
            detected_tags=sorted(detected_tags),
            total_duration=round(
                sum(s.total_time for s in stages) or global_duration, 3
            ),
            log_lines=len(lines),
            warnings=warnings,
            errors=errors,
            build_failed=build_failed,
            failed_methods=failed_methods,
        )

    # -- Error detection -------------------------------------------------------

    # Patterns that indicate a hard failure or error
    _ERROR_PATTERNS = [
        # Java / Groovy exceptions
        (re.compile(r"(?:java\.|org\.|com\.|net\.)[\w.]*Exception[:\s](.*)"), "EXCEPTION"),
        (re.compile(r"Exception in thread .+?: (.+)"), "EXCEPTION"),
        (re.compile(r"Caused by:\s*([\w.]+(?:Exception|Error)[:\s].*)"), "EXCEPTION"),
        # General error markers
        (re.compile(r"^(?:ERROR|FATAL|FAILURE):\s+(.+)", re.IGNORECASE), "GENERIC"),
        (re.compile(r"Build FAILED(?:\s*:\s*(.*))?", re.IGNORECASE), "BUILD_FAILED"),
        (re.compile(r"BUILD FAILURE", re.IGNORECASE), "BUILD_FAILED"),
        # Exit codes
        (re.compile(r"(?:exit(?:ed)?\s+(?:with\s+)?(?:code\s+)?|Process exited\s+)(-?\d+)"), "EXIT_CODE"),
        (re.compile(r"script returned exit code (-?\d+)"), "EXIT_CODE"),
        # Timeouts
        (re.compile(r"(?:Timeout|timed?\s*out)[:\s](.+)", re.IGNORECASE), "TIMEOUT"),
        # NPE / NullPointerException shorthand
        (re.compile(r"NullPointerException"), "EXCEPTION"),
        # Ansible / shell failures
        (re.compile(r"TASK \[.+\] \*+\s*$"), None),  # precursor -- not an error itself
        (re.compile(r"fatal:\s+\[.+\]:\s+FAILED!\s+=>\s+(.+)", re.IGNORECASE), "GENERIC"),
    ]
    _STACK_TRACE_RE = re.compile(r"^\s+at [\w$.]+\([\w.]+:\d+\)")
    _METHOD_ON_STACK_RE = re.compile(r"at [\w$.]*([\w_]+)\([\w.]+:\d+\)")

    def _detect_errors(
        self, lines: list[str], stages: list[Stage]
    ) -> tuple[list[ErrorEvent], list[str], bool]:
        """
        Single-pass error detection.
        Returns (errors, failed_method_names, build_failed).
        """
        errors: list[ErrorEvent] = []
        failed_method_names: set[str] = set()
        build_failed = False

        # Build line -> stage lookup for context
        def stage_at(ln: int) -> Optional[str]:
            for s in reversed(stages):
                if s.start_line <= ln <= s.end_line:
                    return s.name
            return None

        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            matched_type = None
            matched_msg = ""
            exit_code = None

            for pattern, etype in self._ERROR_PATTERNS:
                if etype is None:
                    i += 1
                    continue
                m = pattern.search(stripped)
                if m:
                    matched_type = etype
                    matched_msg = m.group(1).strip() if m.lastindex else stripped
                    if etype == "EXIT_CODE":
                        try:
                            code = int(m.group(1))
                            if code == 0:
                                matched_type = None  # exit 0 is not an error
                            else:
                                exit_code = code
                        except (ValueError, IndexError):
                            matched_type = None
                    if matched_type == "BUILD_FAILED":
                        build_failed = True
                    break

            if matched_type:
                # Collect context lines (+-5)
                ctx_start = max(0, i - 5)
                ctx_end = min(len(lines), i + 6)
                context = lines[ctx_start:ctx_end]

                # Collect stack trace immediately following
                stack_trace = []
                j = i + 1
                while j < len(lines) and j < i + 30:
                    if self._STACK_TRACE_RE.match(lines[j]):
                        stack_trace.append(lines[j].strip())
                        # Extract method name from first stack frame
                        sm = self._METHOD_ON_STACK_RE.search(lines[j])
                        if sm:
                            failed_method_names.add(sm.group(1))
                        j += 1
                    elif lines[j].strip().startswith("Caused by:"):
                        stack_trace.append(lines[j].strip())
                        j += 1
                    else:
                        break

                errors.append(ErrorEvent(
                    error_type=matched_type,
                    message=matched_msg[:500],
                    line_number=i,
                    stage=stage_at(i),
                    failed_method=None,  # will be enriched below
                    context_lines=context,
                    stack_trace=stack_trace,
                    exit_code=exit_code,
                ))

            i += 1

        # Deduplicate failed methods from stack traces
        return errors, sorted(failed_method_names), build_failed


# -- Call-tree serializer (for JSON API) ---------------------------------------

def serialize_call_tree(nodes: list[MethodCall]) -> list[dict]:
    def to_dict(node: MethodCall) -> dict:
        return {
            "name": node.name,
            "service_tag": node.service_tag,
            "elapsed": node.elapsed,
            "indent": node.indent,
            "line_number": node.line_number,
            "timestamp": node.timestamp,
            "children": [to_dict(c) for c in node.children],
        }
    return [to_dict(n) for n in nodes]


# -- Prompt builder -------------------------------------------------------------

def build_analysis_prompt(
    result: ParseResult,
    log_excerpt: str,
    source_context: str = "",
    max_log_chars: int = 4000,
) -> tuple[str, str]:
    """Returns (system_prompt, user_prompt) for AI analysis."""

    system = """You are an expert Jenkins CI/CD performance engineer with deep knowledge of Groovy, Jenkins Shared Libraries, Maven, Gradle, Docker, and npm build tooling.
You analyze build pipeline performance data from log files and optionally Groovy source code.
Your job: identify bottlenecks, explain WHY each is slow (I/O wait, sequential calls that could be parallel, unnecessary steps, large artifact transfers), and give specific actionable fixes.
Always reference exact method names and measured times. If Groovy source code is provided, reference specific lines.
Format in markdown with clear sections."""

    timing_table = "\n".join(
        f"  {s.name}: total={s.total}s avg={s.avg}s calls={s.calls} max={s.max}s p95={s.p95}s"
        + (" [SLOW]" if s.is_slow else "")
        for s in result.timing_stats[:25]
    )

    stages_table = "\n".join(
        f"  Stage '{s.name}': {s.total_time:.2f}s total, {len(s.methods)} method calls"
        for s in result.stages
    )

    detected_tags = ", ".join(result.detected_tags) if result.detected_tags else "none"

    source_section = ""
    if source_context:
        source_section = f"""
## Source Code Context (matched methods)
{source_context}
"""

    user = f"""Analyze this Jenkins build performance data:

## Pipeline Tags Detected
{detected_tags}

## Stages
{stages_table}

## Method Timing Data (sorted by total time, [SLOW] = above {result.slow_percentile if hasattr(result, 'slow_percentile') else 80}th percentile)
{timing_table}

## Total Pipeline Duration
{result.total_duration}s across {len(result.stages)} stages, {result.log_lines} log lines
{source_section}
## Log Excerpt (first {max_log_chars} chars)
```
{log_excerpt[:max_log_chars]}
```

## Warnings from Parser
{chr(10).join(f'  - {w}' for w in result.warnings) or '  None'}

Please provide:
1. **Executive Summary** -- 2-3 sentence overview of pipeline health
2. **Critical Bottlenecks** -- Top 3-5 slowest methods with root cause analysis{' using source code context where available' if source_context else ''}
3. **Call Hierarchy Analysis** -- Patterns in nesting depth, sequential vs parallel opportunities
4. **Stage Breakdown** -- Which stages are healthy/problematic and why
5. **Optimization Roadmap** -- Ranked action items with estimated impact
6. **Stability Risks** -- Methods with high variance (max >> avg) that indicate flakiness
"""
    return system, user


# -- Failure analysis prompt --------------------------------------------------

def build_failure_analysis_prompt(
    result: "ParseResult",
    raw_log: str,
    error_source_context: str = "",
    max_context_chars: int = 4000,
) -> tuple[str, str]:
    """
    Build (system, user) prompt specifically for failure / error analysis.
    error_source_context contains the full source bodies of failed methods.
    """
    system = """You are an expert DevOps and software engineer specialising in CI/CD failure diagnosis.
You will receive a Jenkins build log with detected errors, stack traces, and optionally the source code of failed methods.
Your goal is to explain exactly WHY the build failed, WHERE it failed (file, method, line if visible), and HOW to fix it.
Be precise: quote log lines and source code line numbers. Format in markdown."""

    errors_text = ""
    for idx, err in enumerate(result.errors[:10], 1):
        ctx = "\n".join(err.context_lines)
        trace = "\n".join(err.stack_trace[:15]) if err.stack_trace else "(no stack trace)"
        errors_text += f"""
### Error {idx}: {err.error_type}
- **Message**: {err.message}
- **Line**: {err.line_number}  |  **Stage**: {err.stage or "unknown"}
- **Exit code**: {err.exit_code if err.exit_code is not None else "n/a"}
**Context (surrounding log lines)**:
```
{ctx}
```
**Stack trace** (first 15 frames):
```
{trace}
```
"""

    failed_methods_text = (
        "  " + ", ".join(result.failed_methods[:20])
        if result.failed_methods else "  (none identified from stack traces)"
    )

    source_section = ""
    if error_source_context:
        source_section = f"""
## Source Code of Failed / Implicated Methods
{error_source_context}
"""

    # Include a log window around the first error
    first_error_line = result.errors[0].line_number if result.errors else 0
    log_lines = raw_log.split("\n")
    window_start = max(0, first_error_line - 20)
    window_end = min(len(log_lines), first_error_line + 40)
    log_window = "\n".join(log_lines[window_start:window_end])[:max_context_chars]

    user = f"""Diagnose this Jenkins build failure:

## Build Status
- **Failed**: {'YES' if result.build_failed else 'possible (errors detected)'}
- **Total duration**: {result.total_duration}s
- **Stages**: {len(result.stages)}
- **Methods in stack traces**: {failed_methods_text}

## Detected Errors ({len(result.errors)} total)
{errors_text}
{source_section}
## Log Window Around First Error (lines {window_start}-{window_end})
```
{log_window}
```

Please provide:
1. **Root Cause** -- One sentence: exact cause (e.g. "NullPointerException in docker() at Deploy.groovy:47 because imageTag was not set")
2. **Failure Chain** -- Step-by-step call trace correlating stack frames with Groovy source where available
3. **Source Code Analysis** -- Quote the specific Groovy lines that caused the failure and explain why{'' if not error_source_context else '. Map each stack frame to its source line.'}
4. **Fix** -- Exact change needed (Groovy snippet, config value, or environment variable)
5. **Is this transient?** -- Network/Docker/credentials flakiness vs real bug? What would confirm it?
6. **Prevention** -- One specific test or check to catch this earlier
"""
    return system, user


# -- Batch analysis support ----------------------------------------------------


@dataclass
class LogBatch:
    """One chunk of a large log suitable for a single AI call."""
    batch_index: int          # 0-based
    total_batches: int
    stages: list[Stage]
    timing_stats: list[TimingStat]
    log_lines_excerpt: str    # raw log lines for this batch only
    line_start: int
    line_end: int
    # Full-file summary passed as context so each batch call has global view
    global_summary: str


def split_into_batches(
    result: ParseResult,
    raw_log: str,
    max_stages_per_batch: int = 3,
    max_log_chars_per_batch: int = 3000,
) -> list[LogBatch]:
    """
    Split a ParseResult into batches suitable for local AI models.

    Strategy:
    - Group stages in windows of max_stages_per_batch
    - Attach timing stats that belong to each stage group
    - Include the raw log lines for those stages only
    - Prepend a global summary so every batch has cross-file context

    If there are no stages, fall back to line-based splitting.
    """
    log_lines = raw_log.split("\n")
    total_duration = result.total_duration
    total_methods = len(result.timing_stats)
    total_stages = len(result.stages)

    # Build global summary (compact -- sent with every batch)
    global_summary = (
        f"Full build: {total_duration}s total, {total_stages} stages, "
        f"{total_methods} unique methods, {result.log_lines} log lines.\n"
        f"Top slow methods overall: "
        + ", ".join(
            f"{s.name}({s.total}s)" for s in result.timing_stats[:8] if s.is_slow
        ) or "none flagged"
    )

    if not result.stages:
        # No stage markers -- fall back to line-based batches
        lines_per_batch = max(200, len(log_lines) // 5)
        batches = []
        for i in range(0, len(log_lines), lines_per_batch):
            chunk_lines = log_lines[i:i + lines_per_batch]
            excerpt = "\n".join(chunk_lines)[:max_log_chars_per_batch]
            idx = len(batches)
            # Estimate total; will fix after loop
            batches.append(LogBatch(
                batch_index=idx,
                total_batches=0,  # placeholder
                stages=[],
                timing_stats=result.timing_stats,
                log_lines_excerpt=excerpt,
                line_start=i,
                line_end=min(i + lines_per_batch, len(log_lines)) - 1,
                global_summary=global_summary,
            ))
        for b in batches:
            b.total_batches = len(batches)
        return batches

    # Stage-based batching
    stage_groups = [
        result.stages[i:i + max_stages_per_batch]
        for i in range(0, len(result.stages), max_stages_per_batch)
    ]

    # Build a name -> TimingStat lookup
    stat_by_name = {s.name: s for s in result.timing_stats}

    batches = []
    for group_idx, stage_group in enumerate(stage_groups):
        line_start = stage_group[0].start_line
        line_end = stage_group[-1].end_line
        chunk_lines = log_lines[line_start:line_end + 1]
        excerpt = "\n".join(chunk_lines)[:max_log_chars_per_batch]

        # Collect timing stats whose methods appear in these stages
        method_names_in_group: set[str] = set()
        for stage in stage_group:
            for m in stage.methods:
                method_names_in_group.add(m["name"])
        batch_stats = [stat_by_name[n] for n in method_names_in_group if n in stat_by_name]
        batch_stats.sort(key=lambda s: s.total, reverse=True)

        batches.append(LogBatch(
            batch_index=group_idx,
            total_batches=len(stage_groups),
            stages=stage_group,
            timing_stats=batch_stats,
            log_lines_excerpt=excerpt,
            line_start=line_start,
            line_end=line_end,
            global_summary=global_summary,
        ))

    return batches


def build_batch_prompt(batch: LogBatch) -> tuple[str, str]:
    """Build (system, user) prompt for a single batch."""
    system = """You are an expert Jenkins CI/CD performance engineer with knowledge of Groovy and Jenkins Shared Libraries.
You are analysing one segment of a large build log. Your output will be merged with analyses of other segments.
Be concise and factual: exact method names, times, error messages. Do NOT repeat the raw numbers already in the data.
Look for: slow methods, errors/exceptions, retry loops, Docker pull delays, credential issues, network timeouts.
If log lines contain Groovy stack traces, identify the failing method and likely cause.
Format in markdown, short."""

    timing_table = "\n".join(
        f"  {s.name}: total={s.total}s avg={s.avg}s calls={s.calls} max={s.max}s"
        + (" [SLOW]" if s.is_slow else "")
        for s in batch.timing_stats[:20]
    ) or "  (no timing data in this segment)"

    stages_table = "\n".join(
        f"  Stage '{s.name}': {s.total_time:.2f}s, {len(s.methods)} method calls"
        for s in batch.stages
    ) or "  (no stage markers in this segment)"

    user = f"""Analyze segment {batch.batch_index + 1} of {batch.total_batches}:

## Global Build Context
{batch.global_summary}

## This Segment: Stages {batch.batch_index * 3 + 1} to {batch.batch_index * 3 + len(batch.stages)}
{stages_table}

## Timing Data for This Segment
{timing_table}

## Raw Log Lines {batch.line_start}-{batch.line_end}
```
{batch.log_lines_excerpt}
```

Provide for THIS SEGMENT ONLY (be brief, max 300 words):
1. **Health** -- one sentence verdict (OK / SLOW / ERRORS)
2. **Bottlenecks** -- top 1-3 slowest methods, why they might be slow
3. **Errors/Anomalies** -- any exceptions, failures, retries, or suspicious patterns with the log line quoted
4. **Key Observation** -- one most important thing about this segment
"""
    return system, user


def build_synthesis_prompt(
    batch_reports: list[str],
    result: ParseResult,
    global_summary: str,
    max_chars_per_segment: int = 800,
) -> tuple[str, str]:
    """Build (system, user) prompt for the final synthesis pass."""
    system = """You are an expert Jenkins CI/CD performance engineer.
You have received segment-by-segment analyses of a large build log.
Synthesize them into a single, unified, executive-quality report.
Avoid repeating raw data already in the segment reports -- focus on cross-segment patterns,
the most critical bottlenecks overall, and a clear prioritized action plan.
Format in markdown with clear sections."""

    # Truncate each segment to keep total prompt size manageable.
    # A full segment report can be 1000+ chars; with 8 batches that's 8k+ chars
    # which causes timeouts on small local models.
    def truncate(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n... [truncated, {len(text)-limit} chars omitted]"

    segments_text = "\n\n---\n\n".join(
        f"### Segment {i + 1}\n{truncate(r, max_chars_per_segment)}"
        for i, r in enumerate(batch_reports)
    )

    top_methods = "\n".join(
        f"  {s.name}: {s.total}s total, {s.calls} calls" + (" [SLOW]" if s.is_slow else "")
        for s in result.timing_stats[:15]
    )

    user = f"""Synthesize these {len(batch_reports)} segment analyses into a unified build report.

## Overall Build Stats
{global_summary}

## Top Methods Across Entire Build
{top_methods}

## Segment Analyses
{segments_text}

Provide:
1. **Executive Summary** -- 2-3 sentences on overall build health and outcome
2. **Root Cause Chain** -- If errors occurred: trace failure back across segments. Did something in an early segment cause a later failure? Explicit cross-segment causality.
3. **Critical Bottlenecks** -- Top 5 slowest/most impactful issues ranked by severity
4. **Cross-Segment Patterns** -- Systemic issues repeating across multiple segments (slow Docker pulls, repeated auth failures, consistent method variance)
5. **Stage Health Overview** -- Per stage: name, duration, verdict (OK/SLOW/ERROR), one-line note
6. **Optimization Roadmap** -- Numbered action items prioritised by ROI
7. **Stability Risks** -- High-variance methods or patterns suggesting flakiness
"""
    return system, user

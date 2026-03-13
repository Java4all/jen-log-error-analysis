"""
config.py -- Loads and validates config.yaml, merges with env vars.
"""
from __future__ import annotations
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


# -- Pydantic models ------------------------------------------------------------

class AnthropicConfig(BaseModel):
    api_key: str = ""
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 2000


class OllamaConfig(BaseModel):
    base_url: str = "http://localhost:11434"
    model: str = "codellama:13b"
    timeout: int = 300          # per-batch / single-call timeout (seconds)
    synthesis_timeout: int = 600 # synthesis call timeout -- larger prompt needs more time


class PrivateAIConfig(BaseModel):
    base_url: str = "http://localhost:8080/v1"
    api_key: str = ""
    model: str = "codellama-13b-instruct"
    timeout: int = 120
    verify_ssl: bool = True


class AIConfig(BaseModel):
    provider: str = "anthropic"  # anthropic | ollama | private
    gpu_enabled: bool = False
    gpu_layers: int = 35
    anthropic: AnthropicConfig = Field(default_factory=AnthropicConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    private: PrivateAIConfig = Field(default_factory=PrivateAIConfig)


class RepoConfig(BaseModel):
    url: str
    branch: str = "main"
    paths: list[str] = ["src/", "vars/"]
    extensions: list[str] = [".groovy", ".java", ".py"]
    enabled: bool = True


class GitHubConfig(BaseModel):
    type: str = "public"  # public | private
    token: str = ""
    timeout: int = 30
    verify_ssl: bool = True  # set False for self-signed / custom CA certs (GitHub Enterprise)
    # GitHub Enterprise API base URL.
    # Public GitHub: leave empty (uses https://api.github.com)
    # GitHub Enterprise: set to https://github.mycompany.com/api/v3
    api_url: str = ""
    repos: list[RepoConfig] = []


class PipelineConfig(BaseModel):
    static_tags: list[str] = ["service-abc"]
    method_start_pattern: str = r"{tag}:\s*([\w_]+)"
    timing_pattern: str = r"^([\w_]+):time-elapsed-seconds:([\d.]+)"
    stage_pattern: str = r"StageName:\s*(.+)"
    timestamp_pattern: str = r"\[(\d{4}-\d{2}-\d{2}T[\d:.]+z?)\]"


class AnalysisConfig(BaseModel):
    max_log_chars_for_ai: int = 4000
    max_source_chars_for_ai: int = 3000
    include_call_tree: bool = True
    slow_method_percentile: int = 80
    # Batch processing for large logs
    # auto: batch when log exceeds batch_threshold_lines
    # always: always batch (good for small-context local models)
    # never: single-shot regardless of size
    batch_mode: str = "auto"
    batch_threshold_lines: int = 500    # lines above which auto-batching kicks in
    batch_max_stages: int = 3           # stages per batch
    batch_max_log_chars: int = 3000     # raw log chars sent per batch


class NetworkConfig(BaseModel):
    # Set True to skip SSL certificate verification globally.
    # Useful for corporate environments with self-signed / custom CA certs.
    # Can be overridden per-service via github.verify_ssl and ai.private.verify_ssl.
    verify_ssl: bool = True

    # When True, blocks calls to public cloud services only:
    #   - Anthropic API (api.anthropic.com)
    #   - Public GitHub (github.com)
    #
    # The following remain fully accessible (private/on-prem):
    #   - GitHub Enterprise / on-prem  (any custom URL in github config)
    #   - Private AI endpoints         (provider: ollama or provider: private)
    #   - Jenkins server, internal APIs (on-prem, no internet needed)
    #   - Future fine-tuning endpoints  (private, unaffected)
    #
    # Activate: set network.private_only_mode: true in config.yaml
    #        or set env var PRIVATE_ONLY_MODE=true
    private_only_mode: bool = False


class AppConfig(BaseModel):
    ai: AIConfig = Field(default_factory=AIConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)


# -- Loader ---------------------------------------------------------------------

_CONFIG_PATHS = [
    Path("/app/config_rw/config.yaml"),  # writable volume -- UI saves here
    Path("config/config.yaml"),
    Path("../config/config.yaml"),
    Path("/app/config/config.yaml"),     # read-only mount -- shipped defaults
]


def _resolve_env(value: Any) -> Any:
    """Replace env:VAR_NAME tokens with actual env values."""
    if isinstance(value, str):
        m = re.match(r"^env:(\w+)$", value.strip())
        if m:
            return os.environ.get(m.group(1), "")
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env(v) for v in value]
    return value


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    raw: dict = {}
    for path in _CONFIG_PATHS:
        if path.exists():
            with open(path) as f:
                raw = yaml.safe_load(f) or {}
            break

    resolved = _resolve_env(raw)

    # ---------------------------------------------------------------------------
    # Override config.yaml values with environment variables.
    # Priority: env var > config.yaml > built-in default
    # All vars are passed into the container via docker-compose.yml environment:
    # ---------------------------------------------------------------------------

    def _env(key: str, default: str = "") -> str:
        return os.environ.get(key, default).strip()

    def _bool_env(key: str) -> bool:
        return _env(key).lower() in ("1", "true", "yes")

    # -- AI provider --
    if _env("AI_PROVIDER"):
        resolved.setdefault("ai", {})["provider"] = _env("AI_PROVIDER")

    # -- Anthropic --
    if _env("ANTHROPIC_API_KEY"):
        resolved.setdefault("ai", {}).setdefault("anthropic", {})["api_key"] = _env("ANTHROPIC_API_KEY")

    # -- Ollama --
    if _env("OLLAMA_BASE_URL"):
        resolved.setdefault("ai", {}).setdefault("ollama", {})["base_url"] = _env("OLLAMA_BASE_URL")
    if _env("OLLAMA_MODEL"):
        resolved.setdefault("ai", {}).setdefault("ollama", {})["model"] = _env("OLLAMA_MODEL")
    if _env("OLLAMA_TIMEOUT"):
        resolved.setdefault("ai", {}).setdefault("ollama", {})["timeout"] = int(_env("OLLAMA_TIMEOUT", "120"))

    # -- Private AI endpoint --
    if _env("PRIVATE_AI_BASE_URL"):
        resolved.setdefault("ai", {}).setdefault("private", {})["base_url"] = _env("PRIVATE_AI_BASE_URL")
    if _env("PRIVATE_AI_API_KEY"):
        resolved.setdefault("ai", {}).setdefault("private", {})["api_key"] = _env("PRIVATE_AI_API_KEY")
    if _env("PRIVATE_AI_MODEL"):
        resolved.setdefault("ai", {}).setdefault("private", {})["model"] = _env("PRIVATE_AI_MODEL")

    # -- GitHub --
    if _env("GITHUB_TOKEN"):
        resolved.setdefault("github", {})["token"] = _env("GITHUB_TOKEN")
    if _env("GITHUB_TYPE"):
        resolved.setdefault("github", {})["type"] = _env("GITHUB_TYPE")
    # GitHub Enterprise API URL: https://github.mycompany.com/api/v3
    if _env("GITHUB_API_URL"):
        resolved.setdefault("github", {})["api_url"] = _env("GITHUB_API_URL")
    if _env("GITHUB_ENTERPRISE_URL"):
        repos = resolved.setdefault("github", {}).setdefault("repos", [])
        if repos:
            repos[0]["url"] = _env("GITHUB_ENTERPRISE_URL")
        # Auto-derive api_url from enterprise URL if not explicitly set
        if not _env("GITHUB_API_URL"):
            parts = _env("GITHUB_ENTERPRISE_URL").split("/")
            base = "/".join(parts[:3])  # https://github.mycompany.com
            resolved["github"]["api_url"] = base.rstrip("/") + "/api/v3"

    # -- Network / isolation --
    # Support PRIVATE_ONLY_MODE and legacy ISOLATED_MODE
    if _bool_env("PRIVATE_ONLY_MODE") or _bool_env("ISOLATED_MODE"):
        resolved.setdefault("network", {})["private_only_mode"] = True

    # -- SSL verification --
    # VERIFY_SSL=false disables SSL verification globally (custom CA / self-signed certs)
    # GITHUB_VERIFY_SSL=false disables it only for GitHub calls
    if _env("VERIFY_SSL"):
        val = not _env("VERIFY_SSL").lower() in ("0", "false", "no")
        resolved.setdefault("network", {})["verify_ssl"] = val
        resolved.setdefault("github", {})["verify_ssl"] = val
        resolved.setdefault("ai", {}).setdefault("private", {})["verify_ssl"] = val
    if _env("GITHUB_VERIFY_SSL"):
        val = not _env("GITHUB_VERIFY_SSL").lower() in ("0", "false", "no")
        resolved.setdefault("github", {})["verify_ssl"] = val

    return AppConfig(**resolved)


def reload_config() -> AppConfig:
    get_config.cache_clear()
    return get_config()

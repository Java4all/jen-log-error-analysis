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
    timeout: int = 120


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
    Path("config/config.yaml"),
    Path("../config/config.yaml"),
    Path("/app/config/config.yaml"),
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

    # Override with env vars (convenience shortcuts)
    if "ANTHROPIC_API_KEY" in os.environ:
        resolved.setdefault("ai", {}).setdefault("anthropic", {})["api_key"] = os.environ["ANTHROPIC_API_KEY"]
    if "GITHUB_TOKEN" in os.environ:
        resolved.setdefault("github", {})["token"] = os.environ["GITHUB_TOKEN"]
    if "AI_PROVIDER" in os.environ:
        resolved.setdefault("ai", {})["provider"] = os.environ["AI_PROVIDER"]
    # Support both PRIVATE_ONLY_MODE and legacy ISOLATED_MODE env vars
    if os.environ.get("PRIVATE_ONLY_MODE", "").lower() in ("1", "true", "yes"):
        resolved.setdefault("network", {})["private_only_mode"] = True
    if os.environ.get("ISOLATED_MODE", "").lower() in ("1", "true", "yes"):
        resolved.setdefault("network", {})["private_only_mode"] = True

    return AppConfig(**resolved)


def reload_config() -> AppConfig:
    get_config.cache_clear()
    return get_config()

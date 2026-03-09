from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import ValidationError

from github_issue_analyzer.models import AppRuntimeSettings, FileConfig
from github_issue_analyzer.paths import AppPaths


def _load_dotenv_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue

        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        if not key or key in os.environ:
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def load_file_config(config_path: Path) -> FileConfig:
    data = {}
    if config_path.exists():
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
    return FileConfig.model_validate(data)


def load_runtime_settings() -> AppRuntimeSettings:
    raw = {
        "github_app_id": os.getenv("GIA_GITHUB_APP_ID"),
        "github_app_private_key_path": os.getenv("GIA_GITHUB_APP_PRIVATE_KEY_PATH"),
        "github_api_base_url": os.getenv("GIA_GITHUB_API_BASE_URL", "https://api.github.com"),
        "github_project_token": os.getenv("GIA_GITHUB_PROJECT_TOKEN"),
        "clarification_debounce_seconds": os.getenv("GIA_CLARIFICATION_DEBOUNCE_SECONDS", "10"),
        "active_clarification_polling_seconds": os.getenv(
            "GIA_ACTIVE_CLARIFICATION_POLLING_SECONDS", "10"
        ),
        "clarification_timeout_seconds": os.getenv("GIA_CLARIFICATION_TIMEOUT_SECONDS", "300"),
        "estimate_timeout_seconds": os.getenv("GIA_ESTIMATE_TIMEOUT_SECONDS", "1800"),
        "default_agent_backend": os.getenv("GIA_DEFAULT_AGENT_BACKEND", "codex"),
        "default_agent_model": os.getenv("GIA_DEFAULT_AGENT_MODEL"),
        "default_agent_reasoning_effort": os.getenv("GIA_DEFAULT_AGENT_REASONING_EFFORT"),
        "default_agent_role": os.getenv("GIA_DEFAULT_AGENT_ROLE", "Android developer"),
        "default_agent_language": os.getenv("GIA_DEFAULT_AGENT_LANGUAGE"),
        "log_level": os.getenv("GIA_LOG_LEVEL", "INFO"),
    }
    try:
        return AppRuntimeSettings.model_validate(raw)
    except ValidationError as exc:
        raise RuntimeError(
            "Missing or invalid environment variables for GitHub App settings"
        ) from exc


def load_configuration(project_root: Path, config_path: Path) -> tuple[FileConfig, AppRuntimeSettings, AppPaths]:
    _load_dotenv_file(project_root / ".env")
    file_config = load_file_config(config_path)
    runtime = load_runtime_settings()
    paths = AppPaths.from_environment(project_root=project_root, config_file=config_path)
    return file_config, runtime, paths

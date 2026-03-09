from pathlib import Path

import pytest

from github_issue_analyzer.config import load_configuration, load_file_config
from github_issue_analyzer.models import RepoConfig


def test_load_file_config_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "repos.toml"
    config_path.write_text(
        """
[defaults]
trigger_label = "ai:analyze"
clarification_reminder_days = 7
polling_interval_seconds = 30

[[repos]]
owner_repo = "helpingstar/example"
""".strip(),
        encoding="utf-8",
    )

    config = load_file_config(config_path)

    assert config.defaults.trigger_label == "ai:analyze"
    assert config.repos[0].owner_repo == "helpingstar/example"


def test_load_file_config_reads_project_v2_settings(tmp_path: Path) -> None:
    config_path = tmp_path / "repos.toml"
    config_path.write_text(
        """
[[repos]]
owner_repo = "helpingstar/example"
project_v2_title = "Issue Prioritization"
project_v2_impact_field_name = "Total Impact"
project_v2_create_if_missing = true
""".strip(),
        encoding="utf-8",
    )

    config = load_file_config(config_path)

    assert config.repos[0].project_v2_title == "Issue Prioritization"
    assert config.repos[0].project_v2_impact_field_name == "Total Impact"
    assert config.repos[0].project_v2_create_if_missing is True


def test_load_file_config_derives_project_v2_title_from_repo_name(tmp_path: Path) -> None:
    config_path = tmp_path / "repos.toml"
    config_path.write_text(
        """
[[repos]]
owner_repo = "helpingstar/example"
project_v2_impact_field_name = "Total Impact"
project_v2_create_if_missing = true
""".strip(),
        encoding="utf-8",
    )

    config = load_file_config(config_path)

    assert config.repos[0].project_v2_title is None
    assert config.repos[0].resolved_project_v2_title == "example_project_issue_prioritization"
    assert config.repos[0].project_v2_enabled is True


def test_load_file_config_reads_agent_model_override(tmp_path: Path) -> None:
    config_path = tmp_path / "repos.toml"
    config_path.write_text(
        """
[[repos]]
owner_repo = "helpingstar/example"
agent_model_override = "gpt-5.4"
""".strip(),
        encoding="utf-8",
    )

    config = load_file_config(config_path)

    assert config.repos[0].agent_model_override == "gpt-5.4"


def test_load_file_config_reads_agent_role_override(tmp_path: Path) -> None:
    config_path = tmp_path / "repos.toml"
    config_path.write_text(
        """
[[repos]]
owner_repo = "helpingstar/example"
agent_role_override = "iOS developer"
""".strip(),
        encoding="utf-8",
    )

    config = load_file_config(config_path)

    assert config.repos[0].agent_role_override == "iOS developer"


def test_load_file_config_reads_agent_language_override(tmp_path: Path) -> None:
    config_path = tmp_path / "repos.toml"
    config_path.write_text(
        """
[[repos]]
owner_repo = "helpingstar/example"
agent_language_override = "Korean"
""".strip(),
        encoding="utf-8",
    )

    config = load_file_config(config_path)

    assert config.repos[0].agent_language_override == "Korean"


def test_repo_config_requires_complete_project_v2_settings() -> None:
    with pytest.raises(ValueError):
        RepoConfig(
            owner_repo="helpingstar/example",
            project_v2_title="Issue Prioritization",
        )


def test_repo_config_rejects_create_if_missing_with_project_url() -> None:
    with pytest.raises(ValueError):
        RepoConfig(
            owner_repo="helpingstar/example",
            project_v2_url="https://github.com/users/helpingstar/projects/7",
            project_v2_impact_field_name="Total Impact",
            project_v2_create_if_missing=True,
        )


def test_load_configuration_reads_project_dotenv(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config_path = project_root / "repos.toml"
    config_path.write_text("", encoding="utf-8")
    (project_root / ".env").write_text(
        "\n".join(
            [
                "GIA_GITHUB_APP_ID=123456",
                "GIA_GITHUB_APP_PRIVATE_KEY_PATH=/tmp/test-app.pem",
                "GIA_GITHUB_PROJECT_TOKEN=ghp_test123",
                "GIA_DEFAULT_AGENT_MODEL=gpt-5.4",
                "GIA_DEFAULT_AGENT_REASONING_EFFORT=medium",
                "GIA_DEFAULT_AGENT_ROLE=Android developer",
                "GIA_DEFAULT_AGENT_LANGUAGE=Korean",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("GIA_GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GIA_GITHUB_APP_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("GIA_DEFAULT_AGENT_MODEL", raising=False)
    monkeypatch.delenv("GIA_DEFAULT_AGENT_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("GIA_DEFAULT_AGENT_ROLE", raising=False)
    monkeypatch.delenv("GIA_DEFAULT_AGENT_LANGUAGE", raising=False)

    _, runtime, _ = load_configuration(project_root, config_path)

    assert runtime.github_app_id == 123456
    assert runtime.github_app_private_key_path == Path("/tmp/test-app.pem")
    assert runtime.github_project_token == "ghp_test123"
    assert runtime.default_agent_model == "gpt-5.4"
    assert runtime.default_agent_reasoning_effort == "medium"
    assert runtime.default_agent_role == "Android developer"
    assert runtime.default_agent_language == "Korean"


def test_load_configuration_defaults_agent_role_to_android_developer(
    tmp_path: Path, monkeypatch
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config_path = project_root / "repos.toml"
    config_path.write_text("", encoding="utf-8")
    (project_root / ".env").write_text(
        "\n".join(
            [
                "GIA_GITHUB_APP_ID=123456",
                "GIA_GITHUB_APP_PRIVATE_KEY_PATH=/tmp/test-app.pem",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("GIA_GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GIA_GITHUB_APP_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("GIA_DEFAULT_AGENT_ROLE", raising=False)

    _, runtime, _ = load_configuration(project_root, config_path)

    assert runtime.default_agent_role == "Android developer"


def test_load_configuration_does_not_override_existing_env(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    config_path = project_root / "repos.toml"
    config_path.write_text("", encoding="utf-8")
    (project_root / ".env").write_text(
        "\n".join(
            [
                "GIA_GITHUB_APP_ID=123456",
                "GIA_GITHUB_APP_PRIVATE_KEY_PATH=/tmp/from-dotenv.pem",
                "GIA_DEFAULT_AGENT_MODEL=gpt-5.4",
                "GIA_DEFAULT_AGENT_REASONING_EFFORT=medium",
                "GIA_DEFAULT_AGENT_ROLE=Android developer",
                "GIA_DEFAULT_AGENT_LANGUAGE=Korean",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GIA_GITHUB_APP_ID", "654321")
    monkeypatch.setenv("GIA_GITHUB_APP_PRIVATE_KEY_PATH", "/tmp/from-shell.pem")
    monkeypatch.setenv("GIA_DEFAULT_AGENT_MODEL", "o3")
    monkeypatch.setenv("GIA_DEFAULT_AGENT_REASONING_EFFORT", "high")
    monkeypatch.setenv("GIA_DEFAULT_AGENT_ROLE", "Web developer")
    monkeypatch.setenv("GIA_DEFAULT_AGENT_LANGUAGE", "English")

    _, runtime, _ = load_configuration(project_root, config_path)

    assert runtime.github_app_id == 654321
    assert runtime.github_app_private_key_path == Path("/tmp/from-shell.pem")
    assert runtime.default_agent_model == "o3"
    assert runtime.default_agent_reasoning_effort == "high"
    assert runtime.default_agent_role == "Web developer"
    assert runtime.default_agent_language == "English"

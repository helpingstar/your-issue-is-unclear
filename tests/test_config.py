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


def test_repo_config_requires_complete_project_v2_settings() -> None:
    with pytest.raises(ValueError):
        RepoConfig(
            owner_repo="helpingstar/example",
            project_v2_title="Issue Prioritization",
        )


def test_repo_config_rejects_create_if_missing_without_title() -> None:
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
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("GIA_GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GIA_GITHUB_APP_PRIVATE_KEY_PATH", raising=False)

    _, runtime, _ = load_configuration(project_root, config_path)

    assert runtime.github_app_id == 123456
    assert runtime.github_app_private_key_path == Path("/tmp/test-app.pem")
    assert runtime.github_project_token == "ghp_test123"


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
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GIA_GITHUB_APP_ID", "654321")
    monkeypatch.setenv("GIA_GITHUB_APP_PRIVATE_KEY_PATH", "/tmp/from-shell.pem")

    _, runtime, _ = load_configuration(project_root, config_path)

    assert runtime.github_app_id == 654321
    assert runtime.github_app_private_key_path == Path("/tmp/from-shell.pem")

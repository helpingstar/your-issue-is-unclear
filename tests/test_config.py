from pathlib import Path

from github_issue_analyzer.config import load_configuration, load_file_config


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
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("GIA_GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GIA_GITHUB_APP_PRIVATE_KEY_PATH", raising=False)

    _, runtime, _ = load_configuration(project_root, config_path)

    assert runtime.github_app_id == 123456
    assert runtime.github_app_private_key_path == Path("/tmp/test-app.pem")


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

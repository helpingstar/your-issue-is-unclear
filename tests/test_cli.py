from pathlib import Path

from typer.testing import CliRunner

from github_issue_analyzer import cli


runner = CliRunner()


def write_config(path: Path, body: str) -> Path:
    path.write_text(body.strip(), encoding="utf-8")
    return path


def test_root_help_lists_ui_and_existing_commands() -> None:
    result = runner.invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "bootstrap" in result.stdout
    assert "worker" in result.stdout
    assert "refresh" in result.stdout
    assert "ui" in result.stdout


def test_ui_help_describes_interactive_launcher() -> None:
    result = runner.invoke(cli.app, ["ui", "--help"])

    assert result.exit_code == 0
    assert "interactive terminal UI" in result.stdout
    assert "--config" in result.stdout


def test_ui_bootstrap_all_enabled_dispatches_runner(monkeypatch, tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path / "repos.toml",
        """
[[repos]]
owner_repo = "helpingstar/example"
enabled = true
""",
    )
    choices = iter([0, 0])
    calls: list[tuple[str | None, Path]] = []
    selections: list[cli.UiSelection] = []

    monkeypatch.setattr(cli, "_choose_index", lambda *args, **kwargs: next(choices))
    monkeypatch.setattr(
        cli,
        "_confirm_selection",
        lambda _console, selection: selections.append(selection) or True,
    )
    monkeypatch.setattr(cli, "_run_bootstrap_sync", lambda owner_repo, config: calls.append((owner_repo, config)))

    result = runner.invoke(cli.app, ["ui", "--config", str(config_path)])

    assert result.exit_code == 0
    assert calls == [(None, config_path)]
    assert selections[0].owner_repo is None
    assert "Target: all enabled repositories" in selections[0].summary_lines


def test_ui_bootstrap_single_repo_dispatches_runner(monkeypatch, tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path / "repos.toml",
        """
[[repos]]
owner_repo = "helpingstar/example"
enabled = true

[[repos]]
owner_repo = "helpingstar/another"
enabled = true
""",
    )
    choices = iter([0, 2])
    calls: list[tuple[str | None, Path]] = []
    selections: list[cli.UiSelection] = []

    monkeypatch.setattr(cli, "_choose_index", lambda *args, **kwargs: next(choices))
    monkeypatch.setattr(
        cli,
        "_confirm_selection",
        lambda _console, selection: selections.append(selection) or True,
    )
    monkeypatch.setattr(cli, "_run_bootstrap_sync", lambda owner_repo, config: calls.append((owner_repo, config)))

    result = runner.invoke(cli.app, ["ui", "--config", str(config_path)])

    assert result.exit_code == 0
    assert calls == [("helpingstar/another", config_path)]
    assert selections[0].owner_repo == "helpingstar/another"
    assert "Target: helpingstar/another" in selections[0].summary_lines


def test_ui_worker_dispatches_once_and_continuous_modes(monkeypatch, tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path / "repos.toml",
        """
[[repos]]
owner_repo = "helpingstar/example"
enabled = true
""",
    )
    calls: list[tuple[bool, Path]] = []

    def fake_run_worker(once: bool, config: Path) -> None:
        calls.append((once, config))

    monkeypatch.setattr(cli, "_confirm_selection", lambda *args, **kwargs: True)
    monkeypatch.setattr(cli, "_run_worker_sync", fake_run_worker)

    once_choices = iter([1, 0])
    monkeypatch.setattr(cli, "_choose_index", lambda *args, **kwargs: next(once_choices))
    once_result = runner.invoke(cli.app, ["ui", "--config", str(config_path)])

    continuous_choices = iter([1, 1])
    monkeypatch.setattr(cli, "_choose_index", lambda *args, **kwargs: next(continuous_choices))
    continuous_result = runner.invoke(cli.app, ["ui", "--config", str(config_path)])

    assert once_result.exit_code == 0
    assert continuous_result.exit_code == 0
    assert calls == [(True, config_path), (False, config_path)]


def test_ui_refresh_dispatches_runner(monkeypatch, tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path / "repos.toml",
        """
[[repos]]
owner_repo = "helpingstar/example"
enabled = true
""",
    )
    choices = iter([2, 0])
    calls: list[tuple[str, int, Path]] = []
    selections: list[cli.UiSelection] = []

    monkeypatch.setattr(cli, "_choose_index", lambda *args, **kwargs: next(choices))
    monkeypatch.setattr(cli, "_prompt_positive_int", lambda *args, **kwargs: 42)
    monkeypatch.setattr(
        cli,
        "_confirm_selection",
        lambda _console, selection: selections.append(selection) or True,
    )
    monkeypatch.setattr(
        cli,
        "_run_refresh_sync",
        lambda owner_repo, issue_number, config: calls.append((owner_repo, issue_number, config)),
    )

    result = runner.invoke(cli.app, ["ui", "--config", str(config_path)])

    assert result.exit_code == 0
    assert calls == [("helpingstar/example", 42, config_path)]
    assert selections[0].owner_repo == "helpingstar/example"
    assert selections[0].issue_number == 42
    assert "Issue number: 42" in selections[0].summary_lines


def test_ui_confirmation_rejection_skips_execution(monkeypatch, tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path / "repos.toml",
        """
[[repos]]
owner_repo = "helpingstar/example"
enabled = true
""",
    )
    choices = iter([1, 0])
    calls: list[tuple[bool, Path]] = []

    monkeypatch.setattr(cli, "_choose_index", lambda *args, **kwargs: next(choices))
    monkeypatch.setattr(cli, "_confirm_selection", lambda *args, **kwargs: False)
    monkeypatch.setattr(cli, "_run_worker_sync", lambda once, config: calls.append((once, config)))

    result = runner.invoke(cli.app, ["ui", "--config", str(config_path)])

    assert result.exit_code == 0
    assert calls == []
    assert "Command canceled." in result.stdout


def test_ui_bootstrap_without_enabled_repos_shows_message(monkeypatch, tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path / "repos.toml",
        """
[[repos]]
owner_repo = "helpingstar/example"
enabled = false
""",
    )
    choices = iter([0])
    calls: list[tuple[str | None, Path]] = []

    monkeypatch.setattr(cli, "_choose_index", lambda *args, **kwargs: next(choices))
    monkeypatch.setattr(cli, "_run_bootstrap_sync", lambda owner_repo, config: calls.append((owner_repo, config)))

    result = runner.invoke(cli.app, ["ui", "--config", str(config_path)])

    assert result.exit_code == 1
    assert calls == []
    assert "No enabled repos found in config." in result.stdout

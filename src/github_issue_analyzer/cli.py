from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import typer
from rich.console import Console
from rich.prompt import Confirm, IntPrompt

from github_issue_analyzer.agent.factory import build_agent_adapter
from github_issue_analyzer.branding import BOT_NAME
from github_issue_analyzer.config import load_configuration, load_file_config
from github_issue_analyzer.db import StateStore
from github_issue_analyzer.github.auth import GitHubAppAuth
from github_issue_analyzer.github.client import GitHubClient
from github_issue_analyzer.github.personal_project_client import PersonalProjectClient
from github_issue_analyzer.logging import configure_logging
from github_issue_analyzer.paths import APP_NAME, AppPaths
from github_issue_analyzer.services.bootstrap import BootstrapService
from github_issue_analyzer.services.checkout import CheckoutManager
from github_issue_analyzer.services.project_metadata import ProjectMetadataService
from github_issue_analyzer.services.refresh import RefreshService
from github_issue_analyzer.services.worker import WorkerService
from github_issue_analyzer.workflow.service import WorkflowService


app = typer.Typer(help=BOT_NAME)


UiCommandName = Literal["bootstrap", "worker", "refresh"]


@dataclass(frozen=True)
class UiSelection:
    command: UiCommandName
    command_line: str
    summary_lines: tuple[str, ...]
    owner_repo: str | None = None
    once: bool | None = None
    issue_number: int | None = None


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_config_path() -> Path:
    return _project_root() / "config" / "repos.toml"


def _build_dependencies(config_path: Path):
    project_root = _project_root()
    file_config, runtime, paths = load_configuration(project_root, config_path)
    configure_logging(runtime.log_level)

    state_store = StateStore(paths.db_path)
    state_store.create_all()

    auth = GitHubAppAuth(
        app_id=runtime.github_app_id,
        private_key_path=runtime.github_app_private_key_path,
        api_base_url=runtime.github_api_base_url,
    )
    github_client = GitHubClient(auth=auth, api_base_url=runtime.github_api_base_url)
    personal_project_client = None
    if runtime.github_project_token:
        personal_project_client = PersonalProjectClient(
            token=runtime.github_project_token,
            api_base_url=runtime.github_api_base_url,
        )
    checkout_manager = CheckoutManager()
    project_metadata_service = ProjectMetadataService(github_client, personal_project_client)
    workflow_service = WorkflowService(
        github_client=github_client,
        state_store=state_store,
        checkout_manager=checkout_manager,
        file_config=file_config,
        paths=paths,
        runtime_settings=runtime,
        agent_factory=build_agent_adapter,
        project_metadata_service=project_metadata_service,
    )
    return (
        file_config,
        runtime,
        paths,
        state_store,
        auth,
        github_client,
        personal_project_client,
        workflow_service,
        project_metadata_service,
    )


async def _close_clients(
    auth: GitHubAppAuth,
    github_client: GitHubClient,
    personal_project_client: PersonalProjectClient | None,
) -> None:
    if personal_project_client is not None:
        await personal_project_client.close()
    await github_client.close()
    await auth.close()


def _resolve_refresh_repo(file_config, owner_repo: str):
    repo = next((item for item in file_config.repos if item.owner_repo == owner_repo), None)
    if repo is None:
        raise typer.BadParameter(f"Repo not found in config: {owner_repo}")
    return repo


async def _run_bootstrap(owner_repo: str | None, config_path: Path) -> None:
    (
        file_config,
        _,
        paths,
        state_store,
        auth,
        github_client,
        personal_project_client,
        _,
        project_metadata_service,
    ) = _build_dependencies(config_path)
    service = BootstrapService(
        github_client=github_client,
        state_store=state_store,
        checkout_manager=CheckoutManager(),
        file_config=file_config,
        paths=paths,
        project_metadata_service=project_metadata_service,
    )
    try:
        await service.run(owner_repo=owner_repo)
    finally:
        await _close_clients(auth, github_client, personal_project_client)


async def _run_worker(once: bool, config_path: Path) -> None:
    (
        file_config,
        _,
        _,
        state_store,
        auth,
        github_client,
        personal_project_client,
        workflow_service,
        _,
    ) = _build_dependencies(config_path)
    service = WorkerService(
        state_store=state_store,
        file_config=file_config,
        workflow_service=workflow_service,
    )
    try:
        await service.run(once=once)
    finally:
        await _close_clients(auth, github_client, personal_project_client)


async def _run_refresh(owner_repo: str, issue_number: int, config_path: Path) -> None:
    (
        file_config,
        _,
        _,
        _,
        auth,
        github_client,
        personal_project_client,
        workflow_service,
        _,
    ) = _build_dependencies(config_path)
    repo = _resolve_refresh_repo(file_config, owner_repo)
    service = RefreshService(workflow_service)
    try:
        await service.run(repo, issue_number)
    finally:
        await _close_clients(auth, github_client, personal_project_client)


def _run_bootstrap_sync(owner_repo: str | None, config_path: Path) -> None:
    asyncio.run(_run_bootstrap(owner_repo, config_path))


def _run_worker_sync(once: bool, config_path: Path) -> None:
    asyncio.run(_run_worker(once, config_path))


def _run_refresh_sync(owner_repo: str, issue_number: int, config_path: Path) -> None:
    asyncio.run(_run_refresh(owner_repo, issue_number, config_path))


def _choose_index(console: Console, title: str, options: list[str], *, prompt: str = "Select an option") -> int:
    console.print(title)
    for index, option in enumerate(options, start=1):
        console.print(f"  {index}. {option}")

    while True:
        selected = IntPrompt.ask(prompt, console=console)
        if 1 <= selected <= len(options):
            return selected - 1
        console.print(f"Please enter a number between 1 and {len(options)}.")


def _prompt_positive_int(console: Console, prompt: str) -> int:
    while True:
        issue_number = IntPrompt.ask(prompt, console=console)
        if issue_number > 0:
            return issue_number
        console.print("Please enter a positive integer.")


def _confirm_selection(console: Console, selection: UiSelection) -> bool:
    console.print("\nReady to run:")
    for line in selection.summary_lines:
        console.print(f"  {line}")
    console.print(f"  CLI: {selection.command_line}")
    return Confirm.ask("Run this command?", console=console, default=True)


def _build_command_line(command: str, *args: str) -> str:
    parts = [APP_NAME, command, *args]
    return " ".join(shlex.quote(part) for part in parts)


def _build_bootstrap_selection(console: Console, config_path: Path, file_config) -> UiSelection:
    enabled_repos = [repo.owner_repo for repo in file_config.repos if repo.enabled]
    if not enabled_repos:
        console.print("No enabled repos found in config.")
        raise typer.Exit(code=1)

    options = ["All enabled repositories", *enabled_repos]
    selected_index = _choose_index(console, "\nBootstrap target:", options)
    owner_repo = None if selected_index == 0 else enabled_repos[selected_index - 1]
    target_label = "all enabled repositories" if owner_repo is None else owner_repo
    args = []
    if owner_repo is not None:
        args.extend(["--owner-repo", owner_repo])
    args.extend(["--config", str(config_path)])
    return UiSelection(
        command="bootstrap",
        owner_repo=owner_repo,
        command_line=_build_command_line("bootstrap", *args),
        summary_lines=(
            "Command: bootstrap",
            f"Target: {target_label}",
            f"Config: {config_path}",
        ),
    )


def _build_worker_selection(console: Console, config_path: Path) -> UiSelection:
    options = ["Run a single iteration (--once)", "Run continuously (--no-once)"]
    selected_index = _choose_index(console, "\nWorker mode:", options)
    once = selected_index == 0
    args = ["--once" if once else "--no-once", "--config", str(config_path)]
    return UiSelection(
        command="worker",
        once=once,
        command_line=_build_command_line("worker", *args),
        summary_lines=(
            "Command: worker",
            f"Mode: {'single iteration' if once else 'continuous polling'}",
            f"Config: {config_path}",
        ),
    )


def _build_refresh_selection(console: Console, config_path: Path, file_config) -> UiSelection:
    repo_options = [repo.owner_repo for repo in file_config.repos]
    if not repo_options:
        console.print("No repositories found in config.")
        raise typer.Exit(code=1)

    selected_index = _choose_index(console, "\nRefresh target repository:", repo_options)
    owner_repo = repo_options[selected_index]
    issue_number = _prompt_positive_int(console, "Issue number")
    args = [owner_repo, str(issue_number), "--config", str(config_path)]
    return UiSelection(
        command="refresh",
        owner_repo=owner_repo,
        issue_number=issue_number,
        command_line=_build_command_line("refresh", *args),
        summary_lines=(
            "Command: refresh",
            f"Repository: {owner_repo}",
            f"Issue number: {issue_number}",
            f"Config: {config_path}",
        ),
    )


def _build_ui_selection(console: Console, config_path: Path) -> UiSelection | None:
    file_config = load_file_config(config_path)
    options = ["bootstrap", "worker", "refresh", "quit"]
    selected_index = _choose_index(console, "Interactive command launcher:", options)
    selected_command = options[selected_index]

    if selected_command == "quit":
        console.print("Exited without running a command.")
        return None
    if selected_command == "bootstrap":
        return _build_bootstrap_selection(console, config_path, file_config)
    if selected_command == "worker":
        return _build_worker_selection(console, config_path)
    return _build_refresh_selection(console, config_path, file_config)


def _dispatch_ui_selection(selection: UiSelection, config_path: Path) -> None:
    if selection.command == "bootstrap":
        _run_bootstrap_sync(selection.owner_repo, config_path)
        return
    if selection.command == "worker":
        _run_worker_sync(selection.once is True, config_path)
        return
    if selection.owner_repo is None or selection.issue_number is None:
        raise RuntimeError("Refresh selection is incomplete.")
    _run_refresh_sync(selection.owner_repo, selection.issue_number, config_path)


@app.command()
def bootstrap(
    owner_repo: str | None = typer.Option(None, help="Optional owner/repo filter"),
    config: Path = typer.Option(_default_config_path(), exists=False, help="Path to repos.toml"),
) -> None:
    _run_bootstrap_sync(owner_repo, config)


@app.command()
def worker(
    once: bool = typer.Option(False, help="Run a single polling iteration"),
    config: Path = typer.Option(_default_config_path(), exists=False, help="Path to repos.toml"),
) -> None:
    _run_worker_sync(once, config)


@app.command()
def refresh(
    owner_repo: str = typer.Argument(..., help="owner/repo"),
    issue_number: int = typer.Argument(..., help="Issue number"),
    config: Path = typer.Option(_default_config_path(), exists=False, help="Path to repos.toml"),
) -> None:
    _run_refresh_sync(owner_repo, issue_number, config)


@app.command(help="Open an interactive terminal UI for command selection.")
def ui(
    config: Path = typer.Option(_default_config_path(), exists=False, help="Path to repos.toml"),
) -> None:
    console = Console()
    selection = _build_ui_selection(console, config)
    if selection is None:
        return
    if not _confirm_selection(console, selection):
        console.print("Command canceled.")
        return
    _dispatch_ui_selection(selection, config)


if __name__ == "__main__":
    app()

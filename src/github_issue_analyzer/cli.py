from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from github_issue_analyzer.agent.factory import build_agent_adapter
from github_issue_analyzer.branding import BOT_NAME
from github_issue_analyzer.config import load_configuration
from github_issue_analyzer.db import StateStore
from github_issue_analyzer.github.auth import GitHubAppAuth
from github_issue_analyzer.github.client import GitHubClient
from github_issue_analyzer.github.personal_project_client import PersonalProjectClient
from github_issue_analyzer.logging import configure_logging
from github_issue_analyzer.paths import AppPaths
from github_issue_analyzer.services.bootstrap import BootstrapService
from github_issue_analyzer.services.checkout import CheckoutManager
from github_issue_analyzer.services.project_metadata import ProjectMetadataService
from github_issue_analyzer.services.refresh import RefreshService
from github_issue_analyzer.services.worker import WorkerService
from github_issue_analyzer.workflow.service import WorkflowService


app = typer.Typer(help=BOT_NAME)


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


@app.command()
def bootstrap(
    owner_repo: str | None = typer.Option(None, help="Optional owner/repo filter"),
    config: Path = typer.Option(_default_config_path(), exists=False, help="Path to repos.toml"),
) -> None:
    async def runner() -> None:
        (
            file_config,
            _,
            paths,
            state_store,
            auth,
            github_client,
            personal_project_client,
            workflow_service,
            project_metadata_service,
        ) = _build_dependencies(config)
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

    asyncio.run(runner())


@app.command()
def worker(
    once: bool = typer.Option(False, help="Run a single polling iteration"),
    config: Path = typer.Option(_default_config_path(), exists=False, help="Path to repos.toml"),
) -> None:
    async def runner() -> None:
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
        ) = _build_dependencies(config)
        service = WorkerService(
            state_store=state_store,
            file_config=file_config,
            workflow_service=workflow_service,
        )
        try:
            await service.run(once=once)
        finally:
            await _close_clients(auth, github_client, personal_project_client)

    asyncio.run(runner())


@app.command()
def refresh(
    owner_repo: str = typer.Argument(..., help="owner/repo"),
    issue_number: int = typer.Argument(..., help="Issue number"),
    config: Path = typer.Option(_default_config_path(), exists=False, help="Path to repos.toml"),
) -> None:
    async def runner() -> None:
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
        ) = _build_dependencies(config)
        repo = next((item for item in file_config.repos if item.owner_repo == owner_repo), None)
        if repo is None:
            raise typer.BadParameter(f"Repo not found in config: {owner_repo}")
        service = RefreshService(workflow_service)
        try:
            await service.run(repo, issue_number)
        finally:
            await _close_clients(auth, github_client, personal_project_client)

    asyncio.run(runner())


if __name__ == "__main__":
    app()

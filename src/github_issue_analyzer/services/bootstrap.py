from __future__ import annotations

import logging
from pathlib import Path

from github_issue_analyzer.branding import BOT_NAME
from github_issue_analyzer.db import StateStore
from github_issue_analyzer.github.client import GitHubClient
from github_issue_analyzer.models import FileConfig, RepoConfig
from github_issue_analyzer.paths import AppPaths
from github_issue_analyzer.services.checkout import CheckoutManager
from github_issue_analyzer.services.project_metadata import ProjectMetadataService
from github_issue_analyzer.workflow.comments import BOOTSTRAP_LABEL_SPECS


logger = logging.getLogger(__name__)


class BootstrapService:
    def __init__(
        self,
        github_client: GitHubClient,
        state_store: StateStore,
        checkout_manager: CheckoutManager,
        file_config: FileConfig,
        paths: AppPaths,
        project_metadata_service: ProjectMetadataService,
    ) -> None:
        self.github_client = github_client
        self.state_store = state_store
        self.checkout_manager = checkout_manager
        self.file_config = file_config
        self.paths = paths
        self.project_metadata_service = project_metadata_service

    async def run(self, owner_repo: str | None = None) -> None:
        repos = [repo for repo in self.file_config.repos if repo.enabled]
        if owner_repo:
            repos = [repo for repo in repos if repo.owner_repo == owner_repo]

        for repo in repos:
            await self._bootstrap_repo(repo)

    async def _bootstrap_repo(self, repo: RepoConfig) -> None:
        installation_id = await self.github_client.auth.get_installation_id(repo.owner, repo.repo)
        repo_data = await self.github_client.get_repo(repo.owner, repo.repo, installation_id=installation_id)
        default_branch = repo.base_branch_override or repo_data["default_branch"]
        checkout_path = self.paths.checkout_path_for(repo.owner_repo, repo.checkout_path_override)

        token = await self.github_client.auth.get_installation_token(installation_id)
        await self.checkout_manager.ensure_checkout(repo.owner_repo, checkout_path, default_branch, token)
        await self._ensure_labels(repo, installation_id)
        await self.project_metadata_service.validate_repo_config(repo, installation_id)

        self.state_store.sync_repo_registration(
            repo=repo,
            defaults=self.file_config.defaults,
            checkout_path=checkout_path,
            app_installation_id=installation_id,
        )
        logger.info("bootstrapped repo %s", repo.owner_repo)

    async def _ensure_labels(self, repo: RepoConfig, installation_id: int) -> None:
        existing = {
            label["name"]
            for label in await self.github_client.list_repo_labels(
                repo.owner, repo.repo, installation_id=installation_id
            )
        }
        trigger_label = repo.resolved_trigger_label(self.file_config.defaults)
        label_specs = dict(BOOTSTRAP_LABEL_SPECS)
        if trigger_label not in label_specs:
            label_specs[trigger_label] = ("1d76db", f"{BOT_NAME} trigger label")

        for name, (color, description) in label_specs.items():
            if name in existing:
                continue
            await self.github_client.create_label(
                repo.owner,
                repo.repo,
                name=name,
                color=color,
                description=description,
                installation_id=installation_id,
            )

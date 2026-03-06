from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from github_issue_analyzer.db import StateStore
from github_issue_analyzer.models import FileConfig, RepoConfig
from github_issue_analyzer.utils import ensure_utc_datetime
from github_issue_analyzer.workflow.service import WorkflowService


logger = logging.getLogger(__name__)


class WorkerService:
    def __init__(
        self,
        state_store: StateStore,
        file_config: FileConfig,
        workflow_service: WorkflowService,
    ) -> None:
        self.state_store = state_store
        self.file_config = file_config
        self.workflow_service = workflow_service

    async def run(self, once: bool = False) -> None:
        while True:
            await self.run_once()
            if once:
                return
            await asyncio.sleep(1)

    async def run_once(self) -> None:
        for repo in [item for item in self.file_config.repos if item.enabled]:
            await self._poll_repo(repo)
        await self._poll_active_clarifications()

    async def _poll_repo(self, repo: RepoConfig) -> None:
        registration = self.state_store.get_repo_registration(repo.owner_repo)
        if registration is None or registration.app_installation_id is None:
            logger.warning("repo not bootstrapped: %s", repo.owner_repo)
            return

        now = datetime.now(UTC)
        interval = timedelta(seconds=repo.resolved_polling_interval(self.file_config.defaults))
        last_issue_poll_at = ensure_utc_datetime(registration.last_issue_poll_at)
        if last_issue_poll_at and now - last_issue_poll_at < interval:
            return

        issues = await self.workflow_service.github_client.list_updated_issues(
            repo.owner,
            repo.repo,
            installation_id=registration.app_installation_id,
            since=last_issue_poll_at,
        )
        for issue in sorted(issues, key=lambda item: item["updated_at"]):
            await self.workflow_service.process_issue(repo, issue["number"])

        await self.workflow_service.process_stale_candidates(repo)
        self.state_store.touch_repo_poll(repo.owner_repo)

    async def _poll_active_clarifications(self) -> None:
        sessions = self.state_store.list_active_clarification_sessions()
        interval = timedelta(seconds=self.workflow_service.runtime_settings.active_clarification_polling_seconds)
        repos = {repo.owner_repo: repo for repo in self.file_config.repos if repo.enabled}
        now = datetime.now(UTC)

        for session in sessions:
            last_polled_at = ensure_utc_datetime(session.last_polled_at)
            if last_polled_at and now - last_polled_at < interval:
                continue
            repo = repos.get(session.owner_repo)
            if repo is None:
                continue
            await self.workflow_service.process_issue(repo, session.issue_number)

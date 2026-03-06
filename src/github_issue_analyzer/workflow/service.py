from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from github_issue_analyzer.agent.base import AgentAdapter
from github_issue_analyzer.db import (
    ClarificationSessionORM,
    IssueRecordORM,
    StateStore,
)
from github_issue_analyzer.github.client import GitHubClient
from github_issue_analyzer.models import (
    AgentRequest,
    AgentResponse,
    FileConfig,
    RecognizedComment,
    RepoConfig,
    WorkflowState,
)
from github_issue_analyzer.paths import AppPaths
from github_issue_analyzer.services.checkout import CheckoutManager
from github_issue_analyzer.utils import hash_text, is_command_comment, is_free_text_answer_comment
from github_issue_analyzer.workflow.clarification import parse_clarification_comment_body
from github_issue_analyzer.workflow.comments import (
    CONFIDENCE_LABELS,
    STATE_LABELS,
    render_clarification_comment,
    render_error_comment,
    render_estimate_comment,
    render_requirements_changed_comment,
    render_stale_comment,
)


logger = logging.getLogger(__name__)


class WorkflowService:
    def __init__(
        self,
        github_client: GitHubClient,
        state_store: StateStore,
        checkout_manager: CheckoutManager,
        file_config: FileConfig,
        paths: AppPaths,
        runtime_settings,
        agent_factory,
    ) -> None:
        self.github_client = github_client
        self.state_store = state_store
        self.checkout_manager = checkout_manager
        self.file_config = file_config
        self.paths = paths
        self.runtime_settings = runtime_settings
        self.agent_factory = agent_factory

    async def process_issue(self, repo: RepoConfig, issue_number: int, force_refresh: bool = False) -> None:
        registration = self.state_store.get_repo_registration(repo.owner_repo)
        if registration is None or registration.app_installation_id is None:
            raise RuntimeError(f"Repo is not bootstrapped: {repo.owner_repo}")

        try:
            issue = await self.github_client.get_issue(
                repo.owner, repo.repo, issue_number, installation_id=registration.app_installation_id
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in (404, 410):
                logger.warning(
                    "issue no longer available; skipping %s#%s with status %s",
                    repo.owner_repo,
                    issue_number,
                    exc.response.status_code,
                )
                self.state_store.supersede_clarification_sessions(repo.owner_repo, issue_number)
                self.state_store.update_issue_record(
                    repo.owner_repo,
                    issue_number,
                    issue_state="gone",
                    active_clarification_round=None,
                    active_clarification_comment_id=None,
                )
                return
            raise
        if "pull_request" in issue:
            return

        record = self.state_store.get_or_create_issue_record(repo.owner_repo, issue_number)
        issue_body = issue.get("body") or ""
        body_hash = hash_text(issue_body)
        trigger_label = repo.resolved_trigger_label(self.file_config.defaults)
        trigger_present = trigger_label in {label["name"] for label in issue["labels"]}
        allowed_logins = {repo.owner, issue["user"]["login"]}
        comments = await self.github_client.list_issue_comments(
            repo.owner, repo.repo, issue_number, installation_id=registration.app_installation_id
        )

        stop_requested = self._owner_requested_stop(comments, repo.owner, record.latest_processed_comment_id)
        if stop_requested:
            await self._set_state(
                repo,
                issue_number,
                WorkflowState.STOPPED,
                registration.app_installation_id,
            )
            self.state_store.update_issue_record(
                repo.owner_repo,
                issue_number,
                issue_id=issue["id"],
                issue_state=issue["state"],
                workflow_state=WorkflowState.STOPPED.value,
                trigger_label_present=trigger_present,
            )
            return

        if record.workflow_state == WorkflowState.STOPPED.value:
            if not record.trigger_label_present and trigger_present:
                self.state_store.update_issue_record(
                    repo.owner_repo,
                    issue_number,
                    workflow_state=WorkflowState.NEW.value,
                    trigger_label_present=True,
                )
            else:
                self.state_store.update_issue_record(
                    repo.owner_repo,
                    issue_number,
                    trigger_label_present=trigger_present,
                    issue_state=issue["state"],
                )
                return

        if not trigger_present and record.workflow_state == WorkflowState.NEW.value:
            self.state_store.update_issue_record(
                repo.owner_repo,
                issue_number,
                trigger_label_present=False,
                issue_state=issue["state"],
            )
            return

        accepted_comments = self._accepted_comments(comments, allowed_logins)
        latest_authorized_comment_id = max(
            (comment.comment_id for comment in accepted_comments),
            default=record.latest_processed_comment_id or 0,
        )
        refresh_requested = force_refresh or self._refresh_requested(
            comments, allowed_logins, record.latest_processed_comment_id
        )
        issue_changed_after_estimate = self._issue_changed_after_estimate(
            record, body_hash, latest_authorized_comment_id
        )

        if record.workflow_state == WorkflowState.ESTIMATED.value and issue_changed_after_estimate and not refresh_requested:
            await self._set_state(
                repo,
                issue_number,
                WorkflowState.NEEDS_CLARIFICATION,
                registration.app_installation_id,
            )
            await self._clear_confidence_label(repo, issue_number, registration.app_installation_id)
            await self.github_client.create_issue_comment(
                repo.owner,
                repo.repo,
                issue_number,
                render_requirements_changed_comment(),
                installation_id=registration.app_installation_id,
            )
            self.state_store.update_issue_record(
                repo.owner_repo,
                issue_number,
                workflow_state=WorkflowState.NEEDS_CLARIFICATION.value,
                issue_state=issue["state"],
                trigger_label_present=trigger_present,
            )
            return

        repo_data = await self.github_client.get_repo(
            repo.owner, repo.repo, installation_id=registration.app_installation_id
        )
        base_branch = repo.base_branch_override or repo_data["default_branch"]
        checkout_path = self.paths.checkout_path_for(repo.owner_repo, repo.checkout_path_override)
        token = await self.github_client.auth.get_installation_token(registration.app_installation_id)
        await self.checkout_manager.ensure_checkout(repo.owner_repo, checkout_path, base_branch, token)
        base_commit = await self.checkout_manager.current_head(checkout_path)

        clarification_lines: list[str] = []
        active_session = self.state_store.get_active_clarification_session(repo.owner_repo, issue_number)
        if active_session:
            clarification = await self._parse_active_clarification(
                repo, issue_number, active_session, comments, registration.app_installation_id, allowed_logins
            )
            if not clarification.valid:
                await self._set_state(
                    repo,
                    issue_number,
                    WorkflowState.NEEDS_CLARIFICATION,
                    registration.app_installation_id,
                )
                self.state_store.update_issue_record(
                    repo.owner_repo,
                    issue_number,
                    workflow_state=WorkflowState.NEEDS_CLARIFICATION.value,
                    issue_state=issue["state"],
                    trigger_label_present=trigger_present,
                )
                return
            if not clarification.complete:
                await self._set_state(
                    repo,
                    issue_number,
                    WorkflowState.NEEDS_CLARIFICATION,
                    registration.app_installation_id,
                )
                return
            clarification_lines = clarification.as_prompt_lines()

        backend = repo.agent_backend_override or self.runtime_settings.default_agent_backend
        agent: AgentAdapter = self.agent_factory(backend)
        request = AgentRequest(
            owner_repo=repo.owner_repo,
            issue_number=issue_number,
            issue_title=issue["title"],
            issue_body=issue_body,
            checkout_path=checkout_path,
            base_branch=base_branch,
            accepted_comments=accepted_comments,
            clarification_answers=clarification_lines,
        )

        try:
            response = await agent.analyze(
                request,
                clarification_timeout=self.runtime_settings.clarification_timeout_seconds,
                estimate_timeout=self.runtime_settings.estimate_timeout_seconds,
            )
        except Exception as exc:
            logger.exception("agent execution failed for %s#%s", repo.owner_repo, issue_number)
            await self._set_state(
                repo,
                issue_number,
                WorkflowState.ERROR,
                registration.app_installation_id,
            )
            await self.github_client.create_issue_comment(
                repo.owner,
                repo.repo,
                issue_number,
                render_error_comment(str(exc)),
                installation_id=registration.app_installation_id,
            )
            self.state_store.update_issue_record(
                repo.owner_repo,
                issue_number,
                workflow_state=WorkflowState.ERROR.value,
                issue_state=issue["state"],
                trigger_label_present=trigger_present,
            )
            return

        if response.status == "needs_clarification":
            await self._handle_needs_clarification(
                repo,
                issue_number,
                response,
                record,
                issue,
                registration.app_installation_id,
                body_hash,
                trigger_present,
                latest_authorized_comment_id,
                base_branch,
                base_commit,
            )
            return

        if response.status == "estimated" and response.estimate is not None:
            response.estimate.base_commit = response.estimate.base_commit or base_commit
            await self._handle_estimated(
                repo,
                issue_number,
                response,
                issue,
                registration.app_installation_id,
                body_hash,
                trigger_present,
                latest_authorized_comment_id,
                base_branch,
            )

    async def process_stale_candidates(self, repo: RepoConfig) -> None:
        registration = self.state_store.get_repo_registration(repo.owner_repo)
        if registration is None or registration.app_installation_id is None:
            return
        checkout_path = self.paths.checkout_path_for(repo.owner_repo, repo.checkout_path_override)
        token = await self.github_client.auth.get_installation_token(registration.app_installation_id)
        repo_data = await self.github_client.get_repo(
            repo.owner, repo.repo, installation_id=registration.app_installation_id
        )
        base_branch = repo.base_branch_override or repo_data["default_branch"]
        await self.checkout_manager.ensure_checkout(repo.owner_repo, checkout_path, base_branch, token)
        current_head = await self.checkout_manager.current_head(checkout_path)

        for issue_record in self.state_store.list_estimated_issue_records(repo.owner_repo):
            if not issue_record.base_commit_sha or issue_record.base_commit_sha == current_head:
                continue
            snapshot = self.state_store.get_latest_estimate(repo.owner_repo, issue_record.issue_number)
            if snapshot is None or not snapshot.candidate_files:
                continue
            changed_files = await self.checkout_manager.changed_files_since(
                checkout_path, issue_record.base_commit_sha
            )
            matched_files = sorted(set(snapshot.candidate_files).intersection(changed_files))
            if not matched_files:
                continue

            if issue_record.workflow_state != WorkflowState.STALE.value:
                await self._set_state(
                    repo,
                    issue_record.issue_number,
                    WorkflowState.STALE,
                    registration.app_installation_id,
                )
                await self.github_client.create_issue_comment(
                    repo.owner,
                    repo.repo,
                    issue_record.issue_number,
                    render_stale_comment(issue_record.base_commit_sha, current_head, matched_files),
                    installation_id=registration.app_installation_id,
                )
                self.state_store.update_issue_record(
                    repo.owner_repo,
                    issue_record.issue_number,
                    workflow_state=WorkflowState.STALE.value,
                )

    async def _handle_needs_clarification(
        self,
        repo: RepoConfig,
        issue_number: int,
        response: AgentResponse,
        record: IssueRecordORM,
        issue: dict,
        installation_id: int,
        body_hash: str,
        trigger_present: bool,
        latest_authorized_comment_id: int,
        base_branch: str,
        base_commit: str,
    ) -> None:
        self.state_store.supersede_clarification_sessions(repo.owner_repo, issue_number)
        round_number = (record.active_clarification_round or 0) + 1
        body = render_clarification_comment(
            response.missing_slots,
            response.question_specs,
            round_number,
        )
        comment = await self.github_client.create_issue_comment(
            repo.owner,
            repo.repo,
            issue_number,
            body,
            installation_id=installation_id,
        )
        self.state_store.create_clarification_session(
            repo.owner_repo,
            issue_number,
            round_number,
            comment["id"],
            response.missing_slots,
            [spec.model_dump() for spec in response.question_specs],
        )
        self.state_store.update_issue_record(
            repo.owner_repo,
            issue_number,
            issue_id=issue["id"],
            issue_state=issue["state"],
            workflow_state=WorkflowState.NEEDS_CLARIFICATION.value,
            trigger_label_present=trigger_present,
            latest_processed_comment_id=latest_authorized_comment_id,
            latest_body_hash=body_hash,
            active_clarification_round=round_number,
            active_clarification_comment_id=comment["id"],
            base_branch=base_branch,
            base_commit_sha=base_commit,
        )
        await self._set_state(repo, issue_number, WorkflowState.NEEDS_CLARIFICATION, installation_id)

    async def _handle_estimated(
        self,
        repo: RepoConfig,
        issue_number: int,
        response: AgentResponse,
        issue: dict,
        installation_id: int,
        body_hash: str,
        trigger_present: bool,
        latest_authorized_comment_id: int,
        base_branch: str,
    ) -> None:
        assert response.estimate is not None
        self.state_store.resolve_clarification_session(repo.owner_repo, issue_number, [])
        self.state_store.create_estimate_snapshot(
            repo.owner_repo,
            issue_number,
            {
                "base_commit_sha": response.estimate.base_commit,
                "lines_added_min": response.estimate.lines_added_min,
                "lines_added_max": response.estimate.lines_added_max,
                "lines_modified_min": response.estimate.lines_modified_min,
                "lines_modified_max": response.estimate.lines_modified_max,
                "lines_deleted_min": response.estimate.lines_deleted_min,
                "lines_deleted_max": response.estimate.lines_deleted_max,
                "lines_total_min": response.estimate.lines_total_min,
                "lines_total_max": response.estimate.lines_total_max,
                "confidence": response.estimate.confidence.value,
                "candidate_files": response.estimate.files,
                "reasons": response.estimate.reasons,
            },
        )
        await self.github_client.create_issue_comment(
            repo.owner,
            repo.repo,
            issue_number,
            render_estimate_comment(base_branch, response.estimate),
            installation_id=installation_id,
        )
        self.state_store.update_issue_record(
            repo.owner_repo,
            issue_number,
            issue_id=issue["id"],
            issue_state=issue["state"],
            workflow_state=WorkflowState.ESTIMATED.value,
            trigger_label_present=trigger_present,
            latest_processed_comment_id=latest_authorized_comment_id,
            latest_body_hash=body_hash,
            active_clarification_round=None,
            active_clarification_comment_id=None,
            base_branch=base_branch,
            base_commit_sha=response.estimate.base_commit,
            last_estimated_at=datetime.now(UTC),
        )
        await self._set_state(repo, issue_number, WorkflowState.ESTIMATED, installation_id)
        await self._set_confidence_label(
            repo,
            issue_number,
            response.estimate.confidence.value,
            installation_id,
        )

    async def _parse_active_clarification(
        self,
        repo: RepoConfig,
        issue_number: int,
        session: ClarificationSessionORM,
        issue_comments: list[dict],
        installation_id: int,
        allowed_logins: set[str],
    ):
        comment = await self.github_client.get_issue_comment(
            repo.owner, repo.repo, session.clarification_comment_id, installation_id=installation_id
        )
        question_specs = [
            self._question_spec_from_data(data) for data in session.question_specs
        ]
        free_text_comments = [
            item["body"]
            for item in issue_comments
            if item["id"] > session.clarification_comment_id
            and item["user"]["login"] in allowed_logins
            and is_free_text_answer_comment(item.get("body") or "")
        ]
        result = parse_clarification_comment_body(comment["body"], question_specs, free_text_comments)
        self.state_store.touch_clarification_poll(session.id)
        return result

    def _question_spec_from_data(self, data: dict):
        from github_issue_analyzer.models import QuestionSpec

        return QuestionSpec.model_validate(data)

    def _accepted_comments(
        self, issue_comments: list[dict], allowed_logins: set[str]
    ) -> list[RecognizedComment]:
        accepted: list[RecognizedComment] = []
        for item in issue_comments:
            login = item["user"]["login"]
            body = item.get("body") or ""
            if login not in allowed_logins:
                continue
            if is_command_comment(body) or is_free_text_answer_comment(body):
                continue
            accepted.append(
                RecognizedComment(
                    comment_id=item["id"],
                    author_login=login,
                    body=body,
                    created_at=item.get("created_at"),
                    updated_at=item.get("updated_at"),
                )
            )
        return accepted

    def _owner_requested_stop(self, comments: list[dict], owner: str, latest_processed_comment_id: int | None) -> bool:
        baseline = latest_processed_comment_id or 0
        for item in comments:
            if item["id"] <= baseline:
                continue
            if item["user"]["login"] != owner:
                continue
            if (item.get("body") or "").strip().lower().startswith("/stop"):
                return True
        return False

    def _refresh_requested(
        self, comments: list[dict], allowed_logins: set[str], latest_processed_comment_id: int | None
    ) -> bool:
        baseline = latest_processed_comment_id or 0
        for item in comments:
            if item["id"] <= baseline:
                continue
            if item["user"]["login"] not in allowed_logins:
                continue
            if (item.get("body") or "").strip().lower().startswith("/refresh"):
                return True
        return False

    def _issue_changed_after_estimate(
        self, record: IssueRecordORM, latest_body_hash: str, latest_authorized_comment_id: int
    ) -> bool:
        body_changed = record.latest_body_hash not in (None, latest_body_hash)
        comments_changed = latest_authorized_comment_id > (record.latest_processed_comment_id or 0)
        return body_changed or comments_changed

    async def _set_state(
        self, repo: RepoConfig, issue_number: int, state: WorkflowState, installation_id: int
    ) -> None:
        current_issue = await self.github_client.get_issue(
            repo.owner, repo.repo, issue_number, installation_id=installation_id
        )
        current_labels = {label["name"] for label in current_issue["labels"]}
        desired = STATE_LABELS[state]
        for label in STATE_LABELS.values():
            if label in current_labels and label != desired:
                await self.github_client.remove_label_from_issue(
                    repo.owner, repo.repo, issue_number, label, installation_id=installation_id
                )
        if desired not in current_labels:
            await self.github_client.add_labels_to_issue(
                repo.owner, repo.repo, issue_number, [desired], installation_id=installation_id
            )

    async def _set_confidence_label(
        self, repo: RepoConfig, issue_number: int, confidence: str, installation_id: int
    ) -> None:
        current_issue = await self.github_client.get_issue(
            repo.owner, repo.repo, issue_number, installation_id=installation_id
        )
        current_labels = {label["name"] for label in current_issue["labels"]}
        desired = CONFIDENCE_LABELS[confidence]
        for label in CONFIDENCE_LABELS.values():
            if label in current_labels and label != desired:
                await self.github_client.remove_label_from_issue(
                    repo.owner, repo.repo, issue_number, label, installation_id=installation_id
                )
        if desired not in current_labels:
            await self.github_client.add_labels_to_issue(
                repo.owner, repo.repo, issue_number, [desired], installation_id=installation_id
            )

    async def _clear_confidence_label(
        self, repo: RepoConfig, issue_number: int, installation_id: int
    ) -> None:
        current_issue = await self.github_client.get_issue(
            repo.owner, repo.repo, issue_number, installation_id=installation_id
        )
        current_labels = {label["name"] for label in current_issue["labels"]}
        for label in CONFIDENCE_LABELS.values():
            if label in current_labels:
                await self.github_client.remove_label_from_issue(
                    repo.owner, repo.repo, issue_number, label, installation_id=installation_id
                )

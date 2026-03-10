import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx

from github_issue_analyzer.db import StateStore
from github_issue_analyzer.models import AgentResponse, EstimateResult, FileConfig, RepoConfig, WorkflowState
from github_issue_analyzer.paths import AppPaths
from github_issue_analyzer.workflow.comments import REFRESH_LABEL
from github_issue_analyzer.workflow.service import WorkflowService


class FakeAuth:
    async def get_installation_token(self, installation_id: int) -> str:
        assert installation_id == 1
        return "token"


class FakeGitHubClient:
    def __init__(self) -> None:
        self.auth = FakeAuth()
        self.create_issue_comment_calls: list[int] = []

    async def get_repo(self, owner: str, repo: str, installation_id: int | None = None) -> dict:
        return {"default_branch": "main"}

    async def get_issue(self, owner: str, repo: str, issue_number: int, installation_id: int | None = None) -> dict:
        request = httpx.Request("GET", f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}")
        response = httpx.Response(410, request=request)
        raise httpx.HTTPStatusError("issue gone", request=request, response=response)

    async def create_issue_comment(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
        installation_id: int | None = None,
    ) -> dict:
        self.create_issue_comment_calls.append(issue_number)
        return {"id": 1}


class FakeCheckoutManager:
    async def ensure_checkout(
        self,
        owner_repo: str,
        checkout_path: Path,
        default_branch: str,
        token: str,
    ) -> None:
        return None

    async def current_head(self, checkout_path: Path) -> str:
        return "newhead"

    async def changed_files_since(self, checkout_path: Path, base_commit: str) -> list[str]:
        return ["src/app.py"]


class FakeProjectMetadataService:
    def __init__(self) -> None:
        self.clear_calls: list[int] = []
        self.sync_calls: list[int] = []

    async def clear_estimate(self, repo: RepoConfig, issue: dict, installation_id: int) -> None:
        self.clear_calls.append(issue["number"])

    async def sync_estimate(self, repo: RepoConfig, issue: dict, installation_id: int, estimate) -> None:
        self.sync_calls.append(issue["number"])


class FakeRefreshGitHubClient:
    def __init__(self) -> None:
        self.auth = FakeAuth()
        self.issue_labels = {REFRESH_LABEL}
        self.removed_labels: list[str] = []
        self.added_labels: list[str] = []
        self.comments: list[str] = []

    async def get_repo(self, owner: str, repo: str, installation_id: int | None = None) -> dict:
        return {"default_branch": "main"}

    async def get_issue(self, owner: str, repo: str, issue_number: int, installation_id: int | None = None) -> dict:
        return {
            "id": 42,
            "number": issue_number,
            "state": "open",
            "title": "Example issue",
            "body": "Example body",
            "labels": [{"name": label} for label in sorted(self.issue_labels)],
            "user": {"login": "helpingstar"},
            "node_id": "ISSUE_1",
        }

    async def list_issue_comments(
        self, owner: str, repo: str, issue_number: int, installation_id: int | None = None
    ) -> list[dict]:
        return []

    async def create_issue_comment(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
        installation_id: int | None = None,
    ) -> dict:
        self.comments.append(body)
        return {"id": len(self.comments)}

    async def add_labels_to_issue(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        labels: list[str],
        installation_id: int | None = None,
    ) -> None:
        self.issue_labels.update(labels)
        self.added_labels.extend(labels)

    async def remove_label_from_issue(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        label_name: str,
        installation_id: int | None = None,
    ) -> None:
        self.issue_labels.discard(label_name)
        self.removed_labels.append(label_name)


class FakeMissingClarificationCommentGitHubClient(FakeRefreshGitHubClient):
    def __init__(self) -> None:
        super().__init__()
        self.issue_labels = {"ai:analyze", "ai:needs-clarification"}

    async def get_issue_comment(
        self, owner: str, repo: str, comment_id: int, installation_id: int | None = None
    ) -> dict:
        request = httpx.Request(
            "GET",
            f"https://api.github.com/repos/{owner}/{repo}/issues/comments/{comment_id}",
        )
        response = httpx.Response(404, request=request)
        raise httpx.HTTPStatusError("comment missing", request=request, response=response)


class FakeEstimatedAgent:
    def __init__(self) -> None:
        self.called = False
        self.last_request = None

    async def analyze(self, request, *, clarification_timeout: int, estimate_timeout: int) -> AgentResponse:
        self.called = True
        self.last_request = request
        return AgentResponse(
            status="estimated",
            ready_for_estimate=True,
            estimate=EstimateResult(
                base_commit="headsha",
                lines_added_min=1,
                lines_added_max=2,
                lines_modified_min=0,
                lines_modified_max=1,
                lines_deleted_min=0,
                lines_deleted_max=0,
                lines_total_min=1,
                lines_total_max=3,
                files=["src/app.py"],
                reasons=["Refresh label requested reevaluation"],
            ),
        )


class FakeClarificationHistoryGitHubClient(FakeRefreshGitHubClient):
    def __init__(self) -> None:
        super().__init__()
        self.issue_labels = {"ai:analyze", "ai:needs-clarification"}
        self.updated_comments: dict[int, str] = {}
        self._next_comment_id = 100

    async def get_issue_comment(
        self, owner: str, repo: str, comment_id: int, installation_id: int | None = None
    ) -> dict:
        assert comment_id == 4024602143
        return {
            "id": comment_id,
            "body": "\n".join(
                [
                    "### Q1. 어떤 동작을 원하나요?",
                    "- 타입: `single-select`",
                    "- 허용 선택 수: `1~1`",
                    "- [x] today_section_first",
                    "- [ ] scroll_to_today",
                    "- [ ] 답변 보류",
                ]
            ),
        }

    async def create_issue_comment(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
        installation_id: int | None = None,
    ) -> dict:
        self.comments.append(body)
        comment_id = self._next_comment_id
        self._next_comment_id += 1
        return {"id": comment_id}

    async def update_issue_comment(
        self,
        owner: str,
        repo: str,
        comment_id: int,
        body: str,
        installation_id: int | None = None,
    ) -> dict:
        self.updated_comments[comment_id] = body
        return {"id": comment_id}


def test_process_stale_candidates_skips_gone_issue(tmp_path: Path) -> None:
    db_path = tmp_path / "analyzer.db"
    state_store = StateStore(db_path)
    state_store.create_all()

    repo = RepoConfig(owner_repo="helpingstar/example")
    file_config = FileConfig()
    checkout_root = tmp_path / "checkouts"
    checkout_root.mkdir()
    state_store.sync_repo_registration(
        repo,
        file_config.defaults,
        checkout_root / "helpingstar" / "example",
        app_installation_id=1,
    )
    state_store.update_issue_record(
        repo.owner_repo,
        3,
        issue_state="open",
        workflow_state=WorkflowState.ESTIMATED.value,
        base_commit_sha="oldhead",
    )
    state_store.create_estimate_snapshot(
        repo.owner_repo,
        3,
        {
            "base_commit_sha": "oldhead",
            "lines_added_min": 1,
            "lines_added_max": 2,
            "lines_modified_min": 3,
            "lines_modified_max": 4,
            "lines_deleted_min": 0,
            "lines_deleted_max": 1,
            "lines_total_min": 4,
            "lines_total_max": 7,
            "candidate_files": ["src/app.py"],
            "reasons": ["Touches workflow"],
        },
    )

    github_client = FakeGitHubClient()
    project_metadata_service = FakeProjectMetadataService()
    service = WorkflowService(
        github_client=github_client,  # type: ignore[arg-type]
        state_store=state_store,
        checkout_manager=FakeCheckoutManager(),  # type: ignore[arg-type]
        file_config=file_config,
        paths=AppPaths(
            project_root=tmp_path,
            config_file=tmp_path / "repos.toml",
            state_dir=tmp_path,
            db_path=db_path,
            checkout_root=checkout_root,
            log_root=tmp_path / "logs",
        ),
        runtime_settings=SimpleNamespace(active_clarification_polling_seconds=10),
        agent_factory=lambda *args, **kwargs: None,
        project_metadata_service=project_metadata_service,  # type: ignore[arg-type]
    )

    asyncio.run(service.process_stale_candidates(repo))

    record = state_store.get_or_create_issue_record(repo.owner_repo, 3)
    assert record.issue_state == "gone"
    assert record.active_clarification_round is None
    assert record.active_clarification_comment_id is None
    assert github_client.create_issue_comment_calls == []
    assert project_metadata_service.clear_calls == []


def test_process_stale_candidates_sets_label_without_comment_or_project_clear(tmp_path: Path) -> None:
    db_path = tmp_path / "analyzer.db"
    state_store = StateStore(db_path)
    state_store.create_all()

    repo = RepoConfig(owner_repo="helpingstar/example")
    file_config = FileConfig()
    checkout_root = tmp_path / "checkouts"
    checkout_root.mkdir()
    state_store.sync_repo_registration(
        repo,
        file_config.defaults,
        checkout_root / "helpingstar" / "example",
        app_installation_id=1,
    )
    state_store.update_issue_record(
        repo.owner_repo,
        5,
        issue_state="open",
        workflow_state=WorkflowState.ESTIMATED.value,
        base_commit_sha="oldhead",
    )
    state_store.create_estimate_snapshot(
        repo.owner_repo,
        5,
        {
            "base_commit_sha": "oldhead",
            "lines_added_min": 1,
            "lines_added_max": 2,
            "lines_modified_min": 3,
            "lines_modified_max": 4,
            "lines_deleted_min": 0,
            "lines_deleted_max": 1,
            "lines_total_min": 4,
            "lines_total_max": 7,
            "candidate_files": ["src/app.py"],
            "reasons": ["Touches workflow"],
        },
    )

    github_client = FakeRefreshGitHubClient()
    github_client.issue_labels = {"ai:estimated"}
    project_metadata_service = FakeProjectMetadataService()
    service = WorkflowService(
        github_client=github_client,  # type: ignore[arg-type]
        state_store=state_store,
        checkout_manager=FakeCheckoutManager(),  # type: ignore[arg-type]
        file_config=file_config,
        paths=AppPaths(
            project_root=tmp_path,
            config_file=tmp_path / "repos.toml",
            state_dir=tmp_path,
            db_path=db_path,
            checkout_root=checkout_root,
            log_root=tmp_path / "logs",
        ),
        runtime_settings=SimpleNamespace(active_clarification_polling_seconds=10),
        agent_factory=lambda *args, **kwargs: None,
        project_metadata_service=project_metadata_service,  # type: ignore[arg-type]
    )

    asyncio.run(service.process_stale_candidates(repo))

    record = state_store.get_or_create_issue_record(repo.owner_repo, 5)
    assert record.workflow_state == WorkflowState.STALE.value
    assert github_client.comments == []
    assert "ai:stale" in github_client.issue_labels
    assert "ai:stale" in github_client.added_labels
    assert "ai:estimated" in github_client.removed_labels
    assert project_metadata_service.clear_calls == []


def test_process_issue_refresh_label_reanalyzes_stopped_issue(tmp_path: Path) -> None:
    db_path = tmp_path / "analyzer.db"
    state_store = StateStore(db_path)
    state_store.create_all()

    repo = RepoConfig(owner_repo="helpingstar/example")
    file_config = FileConfig()
    checkout_root = tmp_path / "checkouts"
    checkout_root.mkdir()
    state_store.sync_repo_registration(
        repo,
        file_config.defaults,
        checkout_root / "helpingstar" / "example",
        app_installation_id=1,
    )
    state_store.update_issue_record(
        repo.owner_repo,
        7,
        issue_state="open",
        workflow_state=WorkflowState.STOPPED.value,
        trigger_label_present=False,
    )

    github_client = FakeRefreshGitHubClient()
    project_metadata_service = FakeProjectMetadataService()
    agent = FakeEstimatedAgent()
    service = WorkflowService(
        github_client=github_client,  # type: ignore[arg-type]
        state_store=state_store,
        checkout_manager=FakeCheckoutManager(),  # type: ignore[arg-type]
        file_config=file_config,
        paths=AppPaths(
            project_root=tmp_path,
            config_file=tmp_path / "repos.toml",
            state_dir=tmp_path,
            db_path=db_path,
            checkout_root=checkout_root,
            log_root=tmp_path / "logs",
        ),
        runtime_settings=SimpleNamespace(
            active_clarification_polling_seconds=10,
            clarification_timeout_seconds=300,
            estimate_timeout_seconds=300,
            default_agent_backend="codex",
            default_agent_model=None,
            default_agent_reasoning_effort=None,
            default_agent_role="Android developer",
            default_agent_language=None,
        ),
        agent_factory=lambda *args, **kwargs: agent,
        project_metadata_service=project_metadata_service,  # type: ignore[arg-type]
    )

    asyncio.run(service.process_issue(repo, 7))

    record = state_store.get_or_create_issue_record(repo.owner_repo, 7)
    assert agent.called is True
    assert REFRESH_LABEL in github_client.removed_labels
    assert REFRESH_LABEL not in github_client.issue_labels
    assert WorkflowState.ESTIMATED.value == record.workflow_state
    assert "ai:estimated" in github_client.added_labels
    assert project_metadata_service.sync_calls == [7]


def test_process_issue_does_not_reestimate_unchanged_estimated_issue(tmp_path: Path) -> None:
    db_path = tmp_path / "analyzer.db"
    state_store = StateStore(db_path)
    state_store.create_all()

    repo = RepoConfig(owner_repo="helpingstar/example")
    file_config = FileConfig()
    checkout_root = tmp_path / "checkouts"
    checkout_root.mkdir()
    state_store.sync_repo_registration(
        repo,
        file_config.defaults,
        checkout_root / "helpingstar" / "example",
        app_installation_id=1,
    )

    github_client = FakeRefreshGitHubClient()
    github_client.issue_labels = {"ai:analyze"}
    project_metadata_service = FakeProjectMetadataService()
    agent = FakeEstimatedAgent()
    service = WorkflowService(
        github_client=github_client,  # type: ignore[arg-type]
        state_store=state_store,
        checkout_manager=FakeCheckoutManager(),  # type: ignore[arg-type]
        file_config=file_config,
        paths=AppPaths(
            project_root=tmp_path,
            config_file=tmp_path / "repos.toml",
            state_dir=tmp_path,
            db_path=db_path,
            checkout_root=checkout_root,
            log_root=tmp_path / "logs",
        ),
        runtime_settings=SimpleNamespace(
            active_clarification_polling_seconds=10,
            clarification_timeout_seconds=300,
            estimate_timeout_seconds=300,
            default_agent_backend="codex",
            default_agent_model=None,
            default_agent_reasoning_effort=None,
            default_agent_role="Android developer",
            default_agent_language=None,
        ),
        agent_factory=lambda *args, **kwargs: agent,
        project_metadata_service=project_metadata_service,  # type: ignore[arg-type]
    )

    asyncio.run(service.process_issue(repo, 7))
    asyncio.run(service.process_issue(repo, 7))

    record = state_store.get_or_create_issue_record(repo.owner_repo, 7)
    assert record.workflow_state == WorkflowState.ESTIMATED.value
    assert len(github_client.comments) == 1
    assert project_metadata_service.sync_calls == [7]

def test_process_issue_recovers_when_clarification_comment_is_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "analyzer.db"
    state_store = StateStore(db_path)
    state_store.create_all()

    repo = RepoConfig(owner_repo="helpingstar/example")
    file_config = FileConfig()
    checkout_root = tmp_path / "checkouts"
    checkout_root.mkdir()
    state_store.sync_repo_registration(
        repo,
        file_config.defaults,
        checkout_root / "helpingstar" / "example",
        app_installation_id=1,
    )
    state_store.update_issue_record(
        repo.owner_repo,
        10,
        issue_state="open",
        workflow_state=WorkflowState.NEEDS_CLARIFICATION.value,
        trigger_label_present=True,
        active_clarification_round=1,
        active_clarification_comment_id=4024432602,
    )
    state_store.create_clarification_session(
        repo.owner_repo,
        10,
        1,
        4024432602,
        ["scope"],
        [
            {
                "question_id": "q1",
                "slot": "scope",
                "type": "single-select",
                "min_select": 1,
                "max_select": 1,
                "prompt": "범위를 선택해 주세요.",
                "options": ["API"],
            }
        ],
    )

    github_client = FakeMissingClarificationCommentGitHubClient()
    project_metadata_service = FakeProjectMetadataService()
    agent = FakeEstimatedAgent()
    service = WorkflowService(
        github_client=github_client,  # type: ignore[arg-type]
        state_store=state_store,
        checkout_manager=FakeCheckoutManager(),  # type: ignore[arg-type]
        file_config=file_config,
        paths=AppPaths(
            project_root=tmp_path,
            config_file=tmp_path / "repos.toml",
            state_dir=tmp_path,
            db_path=db_path,
            checkout_root=checkout_root,
            log_root=tmp_path / "logs",
        ),
        runtime_settings=SimpleNamespace(
            active_clarification_polling_seconds=10,
            clarification_timeout_seconds=300,
            estimate_timeout_seconds=300,
            default_agent_backend="codex",
            default_agent_model=None,
            default_agent_reasoning_effort=None,
            default_agent_role="Android developer",
            default_agent_language=None,
        ),
        agent_factory=lambda *args, **kwargs: agent,
        project_metadata_service=project_metadata_service,  # type: ignore[arg-type]
    )

    asyncio.run(service.process_issue(repo, 10))

    record = state_store.get_or_create_issue_record(repo.owner_repo, 10)
    assert agent.called is True
    assert record.workflow_state == WorkflowState.ESTIMATED.value
    assert record.active_clarification_round is None
    assert record.active_clarification_comment_id is None
    assert state_store.get_active_clarification_session(repo.owner_repo, 10) is None


def test_process_issue_merges_all_clarification_rounds_into_agent_request_and_comments(tmp_path: Path) -> None:
    db_path = tmp_path / "analyzer.db"
    state_store = StateStore(db_path)
    state_store.create_all()

    repo = RepoConfig(owner_repo="helpingstar/example")
    file_config = FileConfig()
    checkout_root = tmp_path / "checkouts"
    checkout_root.mkdir()
    state_store.sync_repo_registration(
        repo,
        file_config.defaults,
        checkout_root / "helpingstar" / "example",
        app_installation_id=1,
    )
    state_store.update_issue_record(
        repo.owner_repo,
        10,
        issue_state="open",
        workflow_state=WorkflowState.NEEDS_CLARIFICATION.value,
        trigger_label_present=True,
        active_clarification_round=2,
        active_clarification_comment_id=4024602143,
    )
    first_session = state_store.create_clarification_session(
        repo.owner_repo,
        10,
        1,
        4024432602,
        ["scope"],
        [
            {
                "question_id": "q1_scope",
                "slot": "scope",
                "type": "single-select",
                "min_select": 1,
                "max_select": 1,
                "prompt": "어느 범위인가요?",
                "options": ["API", "UI"],
            }
        ],
    )
    state_store.update_clarification_session_answer_sources(
        first_session.id,
        [
            {
                "type": "clarification_answer",
                "question_id": "q1_scope",
                "slot": "scope",
                "prompt": "어느 범위인가요?",
                "selected_options": ["API"],
                "selected_option_descriptions": ["공개 API 계약만 바꿉니다."],
                "free_text": None,
            },
            {
                "type": "requirements_summary_comment",
                "comment_id": 90,
            },
        ],
    )
    state_store.supersede_clarification_sessions(repo.owner_repo, 10)
    state_store.create_clarification_session(
        repo.owner_repo,
        10,
        2,
        4024602143,
        ["desired_behavior"],
        [
            {
                "question_id": "q1_today_top_behavior",
                "slot": "desired_behavior",
                "type": "single-select",
                "min_select": 1,
                "max_select": 1,
                "prompt": "어떤 동작을 원하나요?",
                "options": ["today_section_first", "scroll_to_today"],
                "option_descriptions": [
                    "오늘 섹션만 목록 맨 위로 올립니다.",
                    "상단 액션으로 오늘 섹션으로 바로 이동합니다.",
                ],
            }
        ],
    )

    github_client = FakeClarificationHistoryGitHubClient()
    project_metadata_service = FakeProjectMetadataService()
    agent = FakeEstimatedAgent()
    service = WorkflowService(
        github_client=github_client,  # type: ignore[arg-type]
        state_store=state_store,
        checkout_manager=FakeCheckoutManager(),  # type: ignore[arg-type]
        file_config=file_config,
        paths=AppPaths(
            project_root=tmp_path,
            config_file=tmp_path / "repos.toml",
            state_dir=tmp_path,
            db_path=db_path,
            checkout_root=checkout_root,
            log_root=tmp_path / "logs",
        ),
        runtime_settings=SimpleNamespace(
            active_clarification_polling_seconds=10,
            clarification_timeout_seconds=300,
            estimate_timeout_seconds=300,
            default_agent_backend="codex",
            default_agent_model=None,
            default_agent_reasoning_effort=None,
            default_agent_role="Android developer",
            default_agent_language=None,
        ),
        agent_factory=lambda *args, **kwargs: agent,
        project_metadata_service=project_metadata_service,  # type: ignore[arg-type]
    )

    asyncio.run(service.process_issue(repo, 10))

    assert agent.last_request is not None
    assert agent.last_request.clarification_answers == [
        "slot=scope | question=어느 범위인가요? | answer=API | "
        "answer_description=API: 공개 API 계약만 바꿉니다.",
        "slot=desired_behavior | question=어떤 동작을 원하나요? | "
        "answer=today_section_first | answer_description=today_section_first: 오늘 섹션만 목록 맨 위로 올립니다.",
    ]
    assert github_client.updated_comments == {}
    assert len(github_client.comments) == 1
    estimate_comment = github_client.comments[-1]
    assert "1. 질문: 어느 범위인가요?" in estimate_comment
    assert "1. 답변: API" in estimate_comment
    assert "2. 질문: 어떤 동작을 원하나요?" in estimate_comment
    assert "2. 답변: today_section_first" in estimate_comment

import asyncio

import pytest

from github_issue_analyzer.models import EstimateResult, RepoConfig
from github_issue_analyzer.services.project_metadata import ProjectMetadataService


class FakeAppGitHubClient:
    async def resolve_project_v2(self, *args, **kwargs):  # pragma: no cover - unused in these tests
        raise AssertionError("app project resolution should not be used for personal project tests")

    async def get_project_v2_item_id_for_issue(self, *args, **kwargs):  # pragma: no cover - unused in these tests
        raise AssertionError("app project item lookup should not be used for personal project tests")

    async def add_issue_to_project_v2(self, *args, **kwargs):  # pragma: no cover - unused in these tests
        raise AssertionError("app project add should not be used for personal project tests")

    async def update_project_v2_number_field(self, *args, **kwargs):  # pragma: no cover - unused in these tests
        raise AssertionError("app project update should not be used for personal project tests")

    async def get_project_v2_item_number_field_value(self, *args, **kwargs):  # pragma: no cover - unused
        raise AssertionError("app project field lookup should not be used for personal project tests")

    async def clear_project_v2_field_value(self, *args, **kwargs):  # pragma: no cover - unused in these tests
        raise AssertionError("app project clear should not be used for personal project tests")


class FakePersonalProjectClient:
    def __init__(self) -> None:
        self.viewer = {"id": "USER_1", "login": "helpingstar"}
        self.projects_by_title: dict[str, dict] = {}
        self.projects_by_number: dict[int, dict] = {}
        self.item_id: str | None = None
        self.create_project_calls: list[str] = []
        self.create_field_calls: list[tuple[str, str]] = []
        self.link_calls: list[tuple[str, str]] = []
        self.add_calls: list[tuple[str, str]] = []
        self.update_calls: list[tuple[str, str, float]] = []
        self.clear_calls: list[tuple[str, str]] = []
        self.item_number_values: dict[tuple[str, str], float] = {}

    async def get_viewer(self) -> dict[str, str]:
        return self.viewer

    async def find_viewer_project_by_title(self, title: str) -> dict | None:
        return self.projects_by_title.get(title)

    async def get_user_project_by_number(self, login: str, number: int) -> dict | None:
        assert login == self.viewer["login"]
        return self.projects_by_number.get(number)

    async def create_viewer_project(self, title: str) -> dict:
        self.create_project_calls.append(title)
        project = {
            "id": "PROJECT_1",
            "title": title,
            "number": 7,
            "fields": {"nodes": []},
        }
        self.projects_by_title[title] = project
        self.projects_by_number[7] = project
        return {"id": project["id"], "title": title, "number": 7}

    async def create_number_field(self, project_id: str, field_name: str) -> None:
        self.create_field_calls.append((project_id, field_name))
        field_id = f"FIELD_{len(self.create_field_calls)}"
        for project in self.projects_by_number.values():
            if project["id"] != project_id:
                continue
            project["fields"]["nodes"].append(
                {
                    "id": field_id,
                    "name": field_name,
                    "dataType": "NUMBER",
                }
            )
            return
        raise AssertionError("project not found for field creation")

    async def get_project_v2_item_id_for_issue(self, issue_node_id: str, project_id: str) -> str | None:
        return self.item_id

    async def add_issue_to_project_v2(self, project_id: str, issue_node_id: str) -> str:
        self.add_calls.append((project_id, issue_node_id))
        self.item_id = "ITEM_1"
        return self.item_id

    async def link_repository_to_project_v2(self, project_id: str, repository_id: str) -> None:
        self.link_calls.append((project_id, repository_id))

    async def update_project_v2_number_field(
        self,
        project_id: str,
        item_id: str,
        field_id: str,
        value: float,
    ) -> None:
        self.update_calls.append((item_id, field_id, value))
        self.item_number_values[(item_id, self._field_name(project_id, field_id))] = value

    async def get_project_v2_item_number_field_value(
        self,
        item_id: str,
        field_name: str,
    ) -> float | None:
        return self.item_number_values.get((item_id, field_name))

    async def clear_project_v2_field_value(
        self,
        project_id: str,
        item_id: str,
        field_id: str,
    ) -> None:
        self.clear_calls.append((item_id, field_id))
        self.item_number_values.pop((item_id, self._field_name(project_id, field_id)), None)

    def _field_name(self, project_id: str, field_id: str) -> str:
        for project in self.projects_by_number.values():
            if project["id"] != project_id:
                continue
            for field in project["fields"]["nodes"]:
                if field["id"] == field_id:
                    return field["name"]
        raise AssertionError("field not found")


def build_repo_config() -> RepoConfig:
    return RepoConfig(
        owner_repo="helpingstar/example",
        project_v2_title="Issue Prioritization",
        project_v2_impact_field_name="Total Impact",
        project_v2_priority_field_name="Priority",
        project_v2_create_if_missing=True,
    )


def build_repo_config_with_derived_title() -> RepoConfig:
    return RepoConfig(
        owner_repo="helpingstar/example",
        project_v2_impact_field_name="Total Impact",
        project_v2_priority_field_name="Priority",
        project_v2_create_if_missing=True,
    )


def build_estimate() -> EstimateResult:
    return EstimateResult(
        base_commit="abc123",
        lines_added_min=40,
        lines_added_max=90,
        lines_modified_min=80,
        lines_modified_max=150,
        lines_deleted_min=0,
        lines_deleted_max=20,
        lines_total_min=120,
        lines_total_max=260,
        files=["src/app.py"],
        reasons=["Touches core workflow"],
    )


def test_sync_estimate_creates_missing_personal_project_and_field() -> None:
    service = ProjectMetadataService(
        FakeAppGitHubClient(),  # type: ignore[arg-type]
        FakePersonalProjectClient(),  # type: ignore[arg-type]
    )

    personal_client = service.personal_project_client
    assert personal_client is not None

    asyncio.run(
        service.sync_estimate(
            build_repo_config(),
            {"number": 42, "node_id": "ISSUE_1"},
            1,
            build_estimate(),
        )
    )

    assert personal_client.create_project_calls == ["Issue Prioritization"]
    assert personal_client.create_field_calls == [
        ("PROJECT_1", "Total Impact"),
        ("PROJECT_1", "Priority"),
    ]
    assert personal_client.add_calls == [("PROJECT_1", "ISSUE_1")]
    assert personal_client.update_calls == [("ITEM_1", "FIELD_1", 190.0)]
    assert personal_client.clear_calls == []


def test_clear_estimate_skips_missing_personal_project_item() -> None:
    personal_client = FakePersonalProjectClient()
    personal_client.projects_by_title["Issue Prioritization"] = {
        "id": "PROJECT_1",
        "title": "Issue Prioritization",
        "number": 7,
        "fields": {
            "nodes": [{"id": "FIELD_1", "name": "Total Impact", "dataType": "NUMBER"}]
        },
    }
    personal_client.projects_by_number[7] = personal_client.projects_by_title["Issue Prioritization"]
    service = ProjectMetadataService(
        FakeAppGitHubClient(),  # type: ignore[arg-type]
        personal_client,  # type: ignore[arg-type]
    )

    asyncio.run(service.clear_estimate(build_repo_config(), {"number": 42, "node_id": "ISSUE_1"}, 1))

    assert personal_client.clear_calls == []


def test_clear_estimate_clears_total_impact_only() -> None:
    personal_client = FakePersonalProjectClient()
    personal_client.projects_by_title["Issue Prioritization"] = {
        "id": "PROJECT_1",
        "title": "Issue Prioritization",
        "number": 7,
        "fields": {
            "nodes": [
                {"id": "FIELD_1", "name": "Total Impact", "dataType": "NUMBER"},
                {"id": "FIELD_2", "name": "Priority", "dataType": "NUMBER"},
            ]
        },
    }
    personal_client.projects_by_number[7] = personal_client.projects_by_title["Issue Prioritization"]
    personal_client.item_id = "ITEM_1"
    personal_client.item_number_values[("ITEM_1", "Priority")] = 5.0
    personal_client.item_number_values[("ITEM_1", "Total Impact")] = 190.0
    service = ProjectMetadataService(
        FakeAppGitHubClient(),  # type: ignore[arg-type]
        personal_client,  # type: ignore[arg-type]
    )

    asyncio.run(service.clear_estimate(build_repo_config(), {"number": 42, "node_id": "ISSUE_1"}, 1))

    assert personal_client.clear_calls == [("ITEM_1", "FIELD_1")]
    assert personal_client.item_number_values[("ITEM_1", "Priority")] == 5.0


def test_validate_repo_config_requires_pat_for_personal_project_sync() -> None:
    service = ProjectMetadataService(FakeAppGitHubClient())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="GIA_GITHUB_PROJECT_TOKEN"):
        asyncio.run(service.validate_repo_config(build_repo_config(), 1))


def test_validate_repo_config_uses_derived_project_title_and_links_repository() -> None:
    personal_client = FakePersonalProjectClient()
    service = ProjectMetadataService(
        FakeAppGitHubClient(),  # type: ignore[arg-type]
        personal_client,  # type: ignore[arg-type]
    )

    asyncio.run(
        service.validate_repo_config(
            build_repo_config_with_derived_title(),
            1,
            repository_node_id="REPO_1",
        )
    )

    assert personal_client.create_project_calls == ["example_project_issue_prioritization"]
    assert personal_client.create_field_calls == [
        ("PROJECT_1", "Total Impact"),
        ("PROJECT_1", "Priority"),
    ]
    assert personal_client.link_calls == [("PROJECT_1", "REPO_1")]

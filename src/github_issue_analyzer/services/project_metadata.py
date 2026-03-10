from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from github_issue_analyzer.github.client import GitHubClient
from github_issue_analyzer.github.personal_project_client import PersonalProjectClient
from github_issue_analyzer.models import EstimateResult, RepoConfig


@dataclass(frozen=True)
class ProjectFieldReference:
    transport: Literal["app", "personal"]
    project_id: str
    project_title: str
    field_id: str
    field_name: str


@dataclass(frozen=True)
class ProjectFieldBundle:
    impact: ProjectFieldReference
    priority: ProjectFieldReference | None = None


@dataclass(frozen=True)
class ProjectLocator:
    owner_type: Literal["orgs", "users"]
    owner_login: str
    project_number: int


class ProjectMetadataService:
    def __init__(
        self,
        github_client: GitHubClient,
        personal_project_client: PersonalProjectClient | None = None,
    ) -> None:
        self.github_client = github_client
        self.personal_project_client = personal_project_client
        self._field_cache: dict[tuple[str, str, tuple[str, ...]], ProjectFieldBundle] = {}

    async def validate_repo_config(
        self,
        repo: RepoConfig,
        installation_id: int,
        repository_node_id: str | None = None,
    ) -> None:
        if not repo.project_v2_enabled:
            return
        reference = await self._resolve_project_fields(repo, installation_id)
        if repository_node_id:
            await self._ensure_repository_link(
                repo,
                reference.impact,
                repository_node_id,
                installation_id,
            )

    async def sync_estimate(
        self,
        repo: RepoConfig,
        issue: dict,
        installation_id: int,
        estimate: EstimateResult,
    ) -> None:
        if not repo.project_v2_enabled:
            return
        issue_node_id = issue.get("node_id")
        if not issue_node_id:
            raise RuntimeError(f"Issue node_id is missing for {repo.owner_repo}#{issue['number']}")

        total_impact = float(estimate.representative_total_impact())
        reference = await self._resolve_project_fields(repo, installation_id)
        item_id = await self._get_project_item_id(repo, issue_node_id, reference.impact, installation_id)
        if item_id is None:
            item_id = await self._add_issue_to_project(repo, issue_node_id, reference.impact, installation_id)

        await self._update_number_field(
            repo,
            reference.impact,
            item_id,
            total_impact,
            installation_id,
        )

    async def clear_estimate(self, repo: RepoConfig, issue: dict, installation_id: int) -> None:
        if not repo.project_v2_enabled:
            return
        issue_node_id = issue.get("node_id")
        if not issue_node_id:
            raise RuntimeError(f"Issue node_id is missing for {repo.owner_repo}#{issue['number']}")

        reference = await self._resolve_project_fields(repo, installation_id)
        item_id = await self._get_project_item_id(repo, issue_node_id, reference.impact, installation_id)
        if item_id is None:
            return

        await self._clear_field(repo, reference.impact, item_id, installation_id)

    async def _resolve_project_fields(
        self,
        repo: RepoConfig,
        installation_id: int,
    ) -> ProjectFieldBundle:
        assert repo.project_v2_impact_field_name is not None

        project_key = repo.resolved_project_v2_title or repo.project_v2_url
        assert project_key is not None
        cache_key = (repo.owner_repo, project_key, tuple(self._required_field_names(repo)))
        cached = self._field_cache.get(cache_key)
        if cached is not None:
            return cached

        if repo.resolved_project_v2_title:
            reference = await self._resolve_personal_project_by_title(repo)
            self._field_cache[cache_key] = reference
            return reference

        assert repo.project_v2_url is not None
        locator = self._parse_project_url(repo.project_v2_url)
        if locator.owner_type == "users":
            reference = await self._resolve_personal_project_by_locator(repo, locator)
            self._field_cache[cache_key] = reference
            return reference

        project = await self.github_client.resolve_project_v2(
            repo.owner,
            repo.repo,
            locator.owner_login,
            locator.project_number,
            installation_id=installation_id,
        )
        reference = self._build_bundle_from_project(project, repo, "app")
        self._field_cache[cache_key] = reference
        return reference

    async def _resolve_personal_project_by_title(self, repo: RepoConfig) -> ProjectFieldBundle:
        project_title = repo.resolved_project_v2_title
        assert project_title is not None
        client = self._require_personal_client()
        project = await client.find_viewer_project_by_title(project_title)
        if project is None:
            if not repo.project_v2_create_if_missing:
                raise RuntimeError(f"GitHub personal Project '{project_title}' not found")
            created = await client.create_viewer_project(project_title)
            viewer = await client.get_viewer()
            project = await client.get_user_project_by_number(viewer["login"], int(created["number"]))
        return await self._ensure_personal_project_fields(repo, project)

    async def _resolve_personal_project_by_locator(
        self,
        repo: RepoConfig,
        locator: ProjectLocator,
    ) -> ProjectFieldBundle:
        client = self._require_personal_client()
        project = await client.get_user_project_by_number(locator.owner_login, locator.project_number)
        if project is None:
            raise RuntimeError(
                f"GitHub personal Project not found or not accessible: {locator.owner_login}#{locator.project_number}"
            )
        return await self._ensure_personal_project_fields(repo, project)

    async def _ensure_personal_project_fields(
        self,
        repo: RepoConfig,
        project: dict | None,
    ) -> ProjectFieldBundle:
        client = self._require_personal_client()
        if not project:
            raise RuntimeError("GitHub personal Project lookup returned no project")
        missing_field_names = self._missing_or_invalid_number_field_names(project, repo)
        if missing_field_names:
            if not repo.project_v2_create_if_missing:
                return self._build_bundle_from_project(project, repo, "personal")
            for field_name in missing_field_names:
                await client.create_number_field(project["id"], field_name)
            viewer = await client.get_viewer()
            refreshed = await client.get_user_project_by_number(viewer["login"], int(project["number"]))
            if refreshed is None:
                raise RuntimeError(
                    f"GitHub personal Project '{project['title']}' could not be refreshed after field creation"
                )
            return self._build_bundle_from_project(refreshed, repo, "personal")
        return self._build_bundle_from_project(project, repo, "personal")

    def _required_field_names(self, repo: RepoConfig) -> list[str]:
        assert repo.project_v2_impact_field_name is not None
        names = [repo.project_v2_impact_field_name]
        if repo.project_v2_priority_field_name and repo.project_v2_priority_field_name not in names:
            names.append(repo.project_v2_priority_field_name)
        return names

    def _missing_or_invalid_number_field_names(self, project: dict, repo: RepoConfig) -> list[str]:
        fields_by_name = {
            field.get("name"): field
            for field in (project.get("fields") or {}).get("nodes") or []
            if field and field.get("name")
        }
        missing: list[str] = []
        for field_name in self._required_field_names(repo):
            field = fields_by_name.get(field_name)
            if field is None:
                missing.append(field_name)
                continue
            if field.get("dataType") != "NUMBER":
                raise RuntimeError(
                    f"GitHub Project field '{field_name}' must be a number field"
                )
        return missing

    def _build_bundle_from_project(
        self,
        project: dict,
        repo: RepoConfig,
        transport: Literal["app", "personal"],
    ) -> ProjectFieldBundle:
        assert repo.project_v2_impact_field_name is not None
        return ProjectFieldBundle(
            impact=self._build_reference_from_project(project, repo.project_v2_impact_field_name, transport),
            priority=(
                self._build_reference_from_project(project, repo.project_v2_priority_field_name, transport)
                if repo.project_v2_priority_field_name
                else None
            ),
        )

    def _build_reference_from_project(
        self,
        project: dict,
        field_name: str,
        transport: Literal["app", "personal"],
    ) -> ProjectFieldReference:
        for field in (project.get("fields") or {}).get("nodes") or []:
            if not field:
                continue
            if field.get("name") != field_name:
                continue
            if field.get("dataType") != "NUMBER":
                raise RuntimeError(
                    f"GitHub Project field '{field_name}' must be a number field"
                )
            return ProjectFieldReference(
                transport=transport,
                project_id=project["id"],
                project_title=project["title"],
                field_id=field["id"],
                field_name=field_name,
            )

        raise RuntimeError(
            f"GitHub Project field '{field_name}' not found in '{project['title']}'"
        )

    def _parse_project_url(self, project_url: str) -> ProjectLocator:
        parsed = urlparse(project_url)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 4 or parts[0] not in {"orgs", "users"} or parts[2] != "projects":
            raise RuntimeError(
                "project_v2_url must look like https://github.com/orgs/<owner>/projects/<number>"
            )
        try:
            project_number = int(parts[3])
        except ValueError as exc:
            raise RuntimeError(f"Invalid GitHub Project number in URL: {project_url}") from exc
        return ProjectLocator(owner_type=parts[0], owner_login=parts[1], project_number=project_number)

    def _require_personal_client(self) -> PersonalProjectClient:
        if self.personal_project_client is None:
            raise RuntimeError(
                "GIA_GITHUB_PROJECT_TOKEN is required for personal GitHub Projects sync"
            )
        return self.personal_project_client

    async def _ensure_repository_link(
        self,
        repo: RepoConfig,
        reference: ProjectFieldReference,
        repository_node_id: str,
        installation_id: int,
    ) -> None:
        try:
            if reference.transport == "personal":
                client = self._require_personal_client()
                await client.link_repository_to_project_v2(reference.project_id, repository_node_id)
                return
            await self.github_client.link_repository_to_project_v2(
                repo.owner,
                repo.repo,
                reference.project_id,
                repository_node_id,
                installation_id=installation_id,
            )
        except RuntimeError as exc:
            if "already linked" in str(exc).lower():
                return
            raise

    async def _get_project_item_id(
        self,
        repo: RepoConfig,
        issue_node_id: str,
        reference: ProjectFieldReference,
        installation_id: int,
    ) -> str | None:
        if reference.transport == "personal":
            client = self._require_personal_client()
            return await client.get_project_v2_item_id_for_issue(issue_node_id, reference.project_id)
        return await self.github_client.get_project_v2_item_id_for_issue(
            repo.owner,
            repo.repo,
            issue_node_id,
            reference.project_id,
            installation_id=installation_id,
        )

    async def _add_issue_to_project(
        self,
        repo: RepoConfig,
        issue_node_id: str,
        reference: ProjectFieldReference,
        installation_id: int,
    ) -> str:
        if reference.transport == "personal":
            client = self._require_personal_client()
            return await client.add_issue_to_project_v2(reference.project_id, issue_node_id)
        return await self.github_client.add_issue_to_project_v2(
            repo.owner,
            repo.repo,
            reference.project_id,
            issue_node_id,
            installation_id=installation_id,
        )

    async def _update_number_field(
        self,
        repo: RepoConfig,
        reference: ProjectFieldReference,
        item_id: str,
        value: float,
        installation_id: int,
    ) -> None:
        if reference.transport == "personal":
            client = self._require_personal_client()
            await client.update_project_v2_number_field(reference.project_id, item_id, reference.field_id, value)
            return
        await self.github_client.update_project_v2_number_field(
            repo.owner,
            repo.repo,
            reference.project_id,
            item_id,
            reference.field_id,
            value,
            installation_id=installation_id,
        )

    async def _clear_field(
        self,
        repo: RepoConfig,
        reference: ProjectFieldReference,
        item_id: str,
        installation_id: int,
    ) -> None:
        if reference.transport == "personal":
            client = self._require_personal_client()
            await client.clear_project_v2_field_value(reference.project_id, item_id, reference.field_id)
            return
        await self.github_client.clear_project_v2_field_value(
            repo.owner,
            repo.repo,
            reference.project_id,
            item_id,
            reference.field_id,
            installation_id=installation_id,
        )

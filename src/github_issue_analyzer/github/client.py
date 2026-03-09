from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from github_issue_analyzer.branding import BOT_NAME
from github_issue_analyzer.github.auth import GitHubAppAuth


class GitHubClient:
    def __init__(self, auth: GitHubAppAuth, api_base_url: str) -> None:
        self.auth = auth
        self.api_base_url = api_base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.api_base_url,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": BOT_NAME,
            },
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    def _graphql_url(self) -> str:
        if self.api_base_url == "https://api.github.com":
            return "https://api.github.com/graphql"
        if self.api_base_url.endswith("/api/v3"):
            return self.api_base_url.removesuffix("/api/v3") + "/api/graphql"
        return f"{self.api_base_url}/graphql"

    async def _request(
        self,
        method: str,
        owner: str,
        repo: str,
        path: str,
        *,
        installation_id: int | None = None,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | list[Any] | None = None,
    ) -> httpx.Response:
        if installation_id is None:
            installation_id = await self.auth.get_installation_id(owner, repo)
        token = await self.auth.get_installation_token(installation_id)
        headers = {"Authorization": f"Bearer {token}"}
        response = await self._client.request(
            method,
            path,
            headers=headers,
            params=params,
            json=json,
        )
        response.raise_for_status()
        return response

    async def graphql(
        self,
        owner: str,
        repo: str,
        query: str,
        variables: dict[str, Any],
        *,
        installation_id: int | None = None,
    ) -> dict[str, Any]:
        if installation_id is None:
            installation_id = await self.auth.get_installation_id(owner, repo)
        token = await self.auth.get_installation_token(installation_id)
        response = await self._client.post(
            self._graphql_url(),
            headers={"Authorization": f"Bearer {token}"},
            json={"query": query, "variables": variables},
        )
        response.raise_for_status()
        payload = response.json()
        errors = payload.get("errors") or []
        if errors:
            messages = "; ".join(str(item.get("message", "unknown GraphQL error")) for item in errors)
            raise RuntimeError(f"GitHub GraphQL error: {messages}")
        return payload["data"]

    async def get_repo(self, owner: str, repo: str, installation_id: int | None = None) -> dict[str, Any]:
        response = await self._request("GET", owner, repo, f"/repos/{owner}/{repo}", installation_id=installation_id)
        return response.json()

    async def list_updated_issues(
        self,
        owner: str,
        repo: str,
        *,
        installation_id: int | None = None,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "state": "all",
            "sort": "updated",
            "direction": "desc",
            "per_page": 100,
        }
        if since is not None:
            params["since"] = since.isoformat().replace("+00:00", "Z")
        response = await self._request(
            "GET",
            owner,
            repo,
            f"/repos/{owner}/{repo}/issues",
            installation_id=installation_id,
            params=params,
        )
        issues = response.json()
        return [issue for issue in issues if "pull_request" not in issue]

    async def get_issue(
        self, owner: str, repo: str, issue_number: int, installation_id: int | None = None
    ) -> dict[str, Any]:
        response = await self._request(
            "GET",
            owner,
            repo,
            f"/repos/{owner}/{repo}/issues/{issue_number}",
            installation_id=installation_id,
        )
        return response.json()

    async def list_issue_comments(
        self, owner: str, repo: str, issue_number: int, installation_id: int | None = None
    ) -> list[dict[str, Any]]:
        response = await self._request(
            "GET",
            owner,
            repo,
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            installation_id=installation_id,
            params={"per_page": 100, "sort": "created", "direction": "asc"},
        )
        return response.json()

    async def get_issue_comment(
        self, owner: str, repo: str, comment_id: int, installation_id: int | None = None
    ) -> dict[str, Any]:
        response = await self._request(
            "GET",
            owner,
            repo,
            f"/repos/{owner}/{repo}/issues/comments/{comment_id}",
            installation_id=installation_id,
        )
        return response.json()

    async def create_issue_comment(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        body: str,
        installation_id: int | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            owner,
            repo,
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            installation_id=installation_id,
            json={"body": body},
        )
        return response.json()

    async def update_issue_comment(
        self,
        owner: str,
        repo: str,
        comment_id: int,
        body: str,
        installation_id: int | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            "PATCH",
            owner,
            repo,
            f"/repos/{owner}/{repo}/issues/comments/{comment_id}",
            installation_id=installation_id,
            json={"body": body},
        )
        return response.json()

    async def list_repo_labels(
        self, owner: str, repo: str, installation_id: int | None = None
    ) -> list[dict[str, Any]]:
        response = await self._request(
            "GET",
            owner,
            repo,
            f"/repos/{owner}/{repo}/labels",
            installation_id=installation_id,
            params={"per_page": 100},
        )
        return response.json()

    async def create_label(
        self,
        owner: str,
        repo: str,
        name: str,
        color: str,
        description: str,
        installation_id: int | None = None,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            owner,
            repo,
            f"/repos/{owner}/{repo}/labels",
            installation_id=installation_id,
            json={"name": name, "color": color, "description": description},
        )
        return response.json()

    async def add_labels_to_issue(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        labels: list[str],
        installation_id: int | None = None,
    ) -> None:
        await self._request(
            "POST",
            owner,
            repo,
            f"/repos/{owner}/{repo}/issues/{issue_number}/labels",
            installation_id=installation_id,
            json={"labels": labels},
        )

    async def remove_label_from_issue(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        label_name: str,
        installation_id: int | None = None,
    ) -> None:
        encoded = quote(label_name, safe="")
        response = await self._request(
            "DELETE",
            owner,
            repo,
            f"/repos/{owner}/{repo}/issues/{issue_number}/labels/{encoded}",
            installation_id=installation_id,
        )
        if response.status_code not in (200, 204):
            response.raise_for_status()

    async def resolve_project_v2(
        self,
        owner: str,
        repo: str,
        project_owner_login: str,
        project_number: int,
        installation_id: int | None = None,
    ) -> dict[str, Any]:
        query = """
        query($login: String!, $number: Int!) {
          organization(login: $login) {
            projectV2(number: $number) {
              id
              title
              fields(first: 100) {
                nodes {
                  __typename
                  ... on ProjectV2Field {
                    id
                    name
                    dataType
                  }
                  ... on ProjectV2SingleSelectField {
                    id
                    name
                    dataType
                  }
                  ... on ProjectV2IterationField {
                    id
                    name
                  }
                }
              }
            }
          }
          user(login: $login) {
            projectV2(number: $number) {
              id
              title
              fields(first: 100) {
                nodes {
                  __typename
                  ... on ProjectV2Field {
                    id
                    name
                    dataType
                  }
                  ... on ProjectV2SingleSelectField {
                    id
                    name
                    dataType
                  }
                  ... on ProjectV2IterationField {
                    id
                    name
                  }
                }
              }
            }
          }
        }
        """
        data = await self.graphql(
            owner,
            repo,
            query,
            {"login": project_owner_login, "number": project_number},
            installation_id=installation_id,
        )
        for owner_type in ("organization", "user"):
            project = (data.get(owner_type) or {}).get("projectV2")
            if project:
                return project
        raise RuntimeError(
            f"GitHub Project not found or not accessible: {project_owner_login}#{project_number}"
        )

    async def get_project_v2_item_id_for_issue(
        self,
        owner: str,
        repo: str,
        issue_node_id: str,
        project_id: str,
        installation_id: int | None = None,
    ) -> str | None:
        query = """
        query($contentId: ID!, $cursor: String) {
          node(id: $contentId) {
            ... on Issue {
              projectItems(first: 100, after: $cursor) {
                nodes {
                  id
                  project {
                    id
                  }
                }
                pageInfo {
                  hasNextPage
                  endCursor
                }
              }
            }
          }
        }
        """
        cursor: str | None = None
        while True:
            data = await self.graphql(
                owner,
                repo,
                query,
                {"contentId": issue_node_id, "cursor": cursor},
                installation_id=installation_id,
            )
            node = data.get("node") or {}
            connection = node.get("projectItems") or {}
            for item in connection.get("nodes") or []:
                if not item:
                    continue
                if ((item.get("project") or {}).get("id")) == project_id:
                    return item.get("id")
            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return None
            cursor = page_info.get("endCursor")

    async def add_issue_to_project_v2(
        self,
        owner: str,
        repo: str,
        project_id: str,
        issue_node_id: str,
        installation_id: int | None = None,
    ) -> str:
        mutation = """
        mutation($projectId: ID!, $contentId: ID!) {
          addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
            item {
              id
            }
          }
        }
        """
        data = await self.graphql(
            owner,
            repo,
            mutation,
            {"projectId": project_id, "contentId": issue_node_id},
            installation_id=installation_id,
        )
        item = ((data.get("addProjectV2ItemById") or {}).get("item")) or {}
        item_id = item.get("id")
        if not item_id:
            raise RuntimeError("GitHub Project item was not created")
        return item_id

    async def update_project_v2_number_field(
        self,
        owner: str,
        repo: str,
        project_id: str,
        item_id: str,
        field_id: str,
        value: float,
        installation_id: int | None = None,
    ) -> None:
        mutation = """
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $value: Float!) {
          updateProjectV2ItemFieldValue(
            input: {
              projectId: $projectId
              itemId: $itemId
              fieldId: $fieldId
              value: {number: $value}
            }
          ) {
            projectV2Item {
              id
            }
          }
        }
        """
        await self.graphql(
            owner,
            repo,
            mutation,
            {
                "projectId": project_id,
                "itemId": item_id,
                "fieldId": field_id,
                "value": value,
            },
            installation_id=installation_id,
        )

    async def clear_project_v2_field_value(
        self,
        owner: str,
        repo: str,
        project_id: str,
        item_id: str,
        field_id: str,
        installation_id: int | None = None,
    ) -> None:
        mutation = """
        mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!) {
          clearProjectV2ItemFieldValue(
            input: {projectId: $projectId, itemId: $itemId, fieldId: $fieldId}
          ) {
            projectV2Item {
              id
            }
          }
        }
        """
        await self.graphql(
            owner,
            repo,
            mutation,
            {"projectId": project_id, "itemId": item_id, "fieldId": field_id},
            installation_id=installation_id,
        )

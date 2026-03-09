from __future__ import annotations

from typing import Any

import httpx

from github_issue_analyzer.branding import BOT_NAME


PROJECT_FIELD_FRAGMENT = """
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
"""


class PersonalProjectClient:
    def __init__(self, token: str, api_base_url: str) -> None:
        self.token = token
        self.api_base_url = api_base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": BOT_NAME,
                "Authorization": f"Bearer {token}",
            },
            timeout=30.0,
        )
        self._viewer_cache: dict[str, str] | None = None

    async def close(self) -> None:
        await self._client.aclose()

    def _graphql_url(self) -> str:
        if self.api_base_url == "https://api.github.com":
            return "https://api.github.com/graphql"
        if self.api_base_url.endswith("/api/v3"):
            return self.api_base_url.removesuffix("/api/v3") + "/api/graphql"
        return f"{self.api_base_url}/graphql"

    async def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._client.post(
            self._graphql_url(),
            json={"query": query, "variables": variables or {}},
        )
        response.raise_for_status()
        payload = response.json()
        errors = payload.get("errors") or []
        if errors:
            messages = "; ".join(str(item.get("message", "unknown GraphQL error")) for item in errors)
            raise RuntimeError(f"GitHub GraphQL error: {messages}")
        return payload["data"]

    async def get_viewer(self) -> dict[str, str]:
        if self._viewer_cache is not None:
            return self._viewer_cache
        query = """
        query {
          viewer {
            login
            ... on User {
              id
            }
          }
        }
        """
        data = await self.graphql(query)
        viewer = data.get("viewer") or {}
        viewer_data = {"id": viewer["id"], "login": viewer["login"]}
        self._viewer_cache = viewer_data
        return viewer_data

    async def find_viewer_project_by_title(self, title: str) -> dict[str, Any] | None:
        viewer = await self.get_viewer()
        return await self.get_user_project_by_title(viewer["login"], title)

    async def get_user_project_by_title(self, login: str, title: str) -> dict[str, Any] | None:
        query = f"""
        query($login: String!, $cursor: String) {{
          user(login: $login) {{
            projectsV2(first: 100, after: $cursor) {{
              nodes {{
                id
                title
                number
                {PROJECT_FIELD_FRAGMENT}
              }}
              pageInfo {{
                hasNextPage
                endCursor
              }}
            }}
          }}
        }}
        """
        cursor: str | None = None
        while True:
            data = await self.graphql(query, {"login": login, "cursor": cursor})
            connection = ((data.get("user") or {}).get("projectsV2")) or {}
            for project in connection.get("nodes") or []:
                if project and project.get("title") == title:
                    return project
            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return None
            cursor = page_info.get("endCursor")

    async def get_user_project_by_number(self, login: str, number: int) -> dict[str, Any] | None:
        query = f"""
        query($login: String!, $number: Int!) {{
          user(login: $login) {{
            projectV2(number: $number) {{
              id
              title
              number
              {PROJECT_FIELD_FRAGMENT}
            }}
          }}
        }}
        """
        data = await self.graphql(query, {"login": login, "number": number})
        return ((data.get("user") or {}).get("projectV2")) or None

    async def create_viewer_project(self, title: str) -> dict[str, Any]:
        viewer = await self.get_viewer()
        mutation = """
        mutation($ownerId: ID!, $title: String!) {
          createProjectV2(input: {ownerId: $ownerId, title: $title}) {
            projectV2 {
              id
              title
              number
            }
          }
        }
        """
        data = await self.graphql(mutation, {"ownerId": viewer["id"], "title": title})
        project = ((data.get("createProjectV2") or {}).get("projectV2")) or {}
        if not project.get("id"):
            raise RuntimeError("GitHub personal Project was not created")
        return project

    async def create_number_field(self, project_id: str, field_name: str) -> None:
        mutation = """
        mutation($projectId: ID!, $fieldName: String!) {
          createProjectV2Field(
            input: {projectId: $projectId, dataType: NUMBER, name: $fieldName}
          ) {
            projectV2Field {
              __typename
            }
          }
        }
        """
        await self.graphql(mutation, {"projectId": project_id, "fieldName": field_name})

    async def get_project_v2_item_id_for_issue(self, issue_node_id: str, project_id: str) -> str | None:
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
            data = await self.graphql(query, {"contentId": issue_node_id, "cursor": cursor})
            connection = ((data.get("node") or {}).get("projectItems")) or {}
            for item in connection.get("nodes") or []:
                if item and ((item.get("project") or {}).get("id")) == project_id:
                    return item.get("id")
            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return None
            cursor = page_info.get("endCursor")

    async def add_issue_to_project_v2(self, project_id: str, issue_node_id: str) -> str:
        mutation = """
        mutation($projectId: ID!, $contentId: ID!) {
          addProjectV2ItemById(input: {projectId: $projectId, contentId: $contentId}) {
            item {
              id
            }
          }
        }
        """
        data = await self.graphql(mutation, {"projectId": project_id, "contentId": issue_node_id})
        item = ((data.get("addProjectV2ItemById") or {}).get("item")) or {}
        item_id = item.get("id")
        if not item_id:
            raise RuntimeError("GitHub Project item was not created")
        return item_id

    async def update_project_v2_number_field(
        self,
        project_id: str,
        item_id: str,
        field_id: str,
        value: float,
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
            mutation,
            {"projectId": project_id, "itemId": item_id, "fieldId": field_id, "value": value},
        )

    async def clear_project_v2_field_value(
        self,
        project_id: str,
        item_id: str,
        field_id: str,
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
        await self.graphql(mutation, {"projectId": project_id, "itemId": item_id, "fieldId": field_id})

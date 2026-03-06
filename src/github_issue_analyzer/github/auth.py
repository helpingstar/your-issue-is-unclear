from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt

from github_issue_analyzer.branding import BOT_NAME


@dataclass
class InstallationToken:
    token: str
    expires_at: datetime


class GitHubAppAuth:
    def __init__(self, app_id: int, private_key_path: Path, api_base_url: str) -> None:
        self.app_id = app_id
        self.private_key = private_key_path.read_text(encoding="utf-8")
        self.api_base_url = api_base_url.rstrip("/")
        self._installation_cache: dict[str, int] = {}
        self._token_cache: dict[int, InstallationToken] = {}
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

    def _build_app_jwt(self) -> str:
        now = datetime.now(UTC)
        payload = {
            "iat": int((now - timedelta(seconds=30)).timestamp()),
            "exp": int((now + timedelta(minutes=9)).timestamp()),
            "iss": str(self.app_id),
        }
        return jwt.encode(payload, self.private_key, algorithm="RS256")

    async def _app_request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self._build_app_jwt()}"
        response = await self._client.request(method, path, headers=headers, **kwargs)
        response.raise_for_status()
        return response

    async def get_installation_id(self, owner: str, repo: str) -> int:
        cache_key = f"{owner}/{repo}"
        cached = self._installation_cache.get(cache_key)
        if cached is not None:
            return cached

        response = await self._app_request("GET", f"/repos/{owner}/{repo}/installation")
        installation_id = int(response.json()["id"])
        self._installation_cache[cache_key] = installation_id
        return installation_id

    async def get_installation_token(self, installation_id: int) -> str:
        cached = self._token_cache.get(installation_id)
        now = datetime.now(UTC)
        if cached and cached.expires_at > now + timedelta(minutes=1):
            return cached.token

        response = await self._app_request(
            "POST", f"/app/installations/{installation_id}/access_tokens"
        )
        data = response.json()
        token = data["token"]
        expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
        self._token_cache[installation_id] = InstallationToken(token=token, expires_at=expires_at)
        return token

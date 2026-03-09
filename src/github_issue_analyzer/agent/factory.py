from __future__ import annotations

from github_issue_analyzer.agent.base import AgentAdapter
from github_issue_analyzer.agent.codex import CodexAdapter


def build_agent_adapter(
    backend: str,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
    role: str | None = None,
    language: str | None = None,
) -> AgentAdapter:
    normalized = backend.lower()
    if normalized == "codex":
        return CodexAdapter(
            model=model,
            reasoning_effort=reasoning_effort,
            role=role,
            language=language,
        )
    raise RuntimeError(f"Unsupported agent backend: {backend}")

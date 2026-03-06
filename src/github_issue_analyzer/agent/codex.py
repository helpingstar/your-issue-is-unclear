from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from pydantic import TypeAdapter

from github_issue_analyzer.agent.base import AgentAdapter
from github_issue_analyzer.branding import BOT_NAME
from github_issue_analyzer.models import AgentRequest, AgentResponse


class CodexAdapter(AgentAdapter):
    def __init__(self, command: str = "codex") -> None:
        self.command = command

    async def analyze(
        self, request: AgentRequest, *, clarification_timeout: int, estimate_timeout: int
    ) -> AgentResponse:
        timeout = estimate_timeout
        prompt = self._build_prompt(request)
        schema = self._build_output_schema()

        for attempt in range(2):
            try:
                return await self._run(prompt=prompt, schema=schema, cwd=request.checkout_path, timeout=timeout)
            except (asyncio.TimeoutError, json.JSONDecodeError, ValueError):
                if attempt == 1:
                    raise
        raise RuntimeError("unreachable")

    async def _run(self, *, prompt: str, schema: dict, cwd: Path, timeout: int) -> AgentResponse:
        with tempfile.TemporaryDirectory(prefix="gia-codex-") as temp_dir:
            schema_path = Path(temp_dir) / "schema.json"
            output_path = Path(temp_dir) / "result.json"
            schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")

            process = await asyncio.create_subprocess_exec(
                self.command,
                "exec",
                "-C",
                str(cwd),
                "-s",
                "read-only",
                "--output-schema",
                str(schema_path),
                "-o",
                str(output_path),
                prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                process.kill()
                raise

            if process.returncode != 0:
                details = self._format_process_error(stdout, stderr)
                raise ValueError(f"codex exec failed{details}")

            if not output_path.exists():
                details = self._format_process_error(stdout, stderr)
                raise ValueError(f"codex output file missing{details}")

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            return AgentResponse.model_validate(payload)

    def _build_output_schema(self) -> dict:
        schema = TypeAdapter(AgentResponse).json_schema()
        return self._normalize_schema(schema)

    def _normalize_schema(self, node):
        if isinstance(node, list):
            return [self._normalize_schema(item) for item in node]
        if not isinstance(node, dict):
            return node

        normalized = {}
        for key, value in node.items():
            if key in {"default", "title"}:
                continue
            normalized[key] = self._normalize_schema(value)

        if normalized.get("type") == "object" or "properties" in normalized:
            properties = normalized.setdefault("properties", {})
            normalized["required"] = list(properties.keys())
            normalized["additionalProperties"] = False

        return normalized

    def _format_process_error(self, stdout: bytes, stderr: bytes) -> str:
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        combined = stderr_text or stdout_text
        if not combined:
            return ""
        lines = [line.strip() for line in combined.splitlines() if line.strip()]
        priority_lines = [
            line
            for line in lines
            if any(token in line.lower() for token in ("error", "failed", "denied", "timed out", "missing"))
        ]
        excerpt_lines = priority_lines[-6:] if priority_lines else lines[-6:]
        excerpt = " | ".join(excerpt_lines)
        if len(lines) > len(excerpt_lines):
            excerpt += " | ..."
        return f": {excerpt}"

    def _build_prompt(self, request: AgentRequest) -> str:
        accepted_comments = "\n".join(
            f"- {comment.author_login}: {comment.body}" for comment in request.accepted_comments
        )
        clarification_answers = "\n".join(
            f"- {line}" for line in request.clarification_answers
        )
        return f"""
You are the backend analysis agent for {BOT_NAME}.

Read the local repository in a strictly read-only way.
Do not modify files.
Do not run builds or tests.
You may inspect files and use git read commands.

Repository: {request.owner_repo}
Issue number: {request.issue_number}
Base branch: {request.base_branch}

Issue title:
{request.issue_title}

Issue body:
{request.issue_body}

Accepted comments:
{accepted_comments or "(none)"}

Clarification answers:
{clarification_answers or "(none)"}

Return JSON only and follow the provided schema exactly.
""".strip()

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
    def __init__(
        self,
        command: str = "codex",
        model: str | None = None,
        reasoning_effort: str | None = None,
        role: str | None = None,
        language: str | None = None,
    ) -> None:
        self.command = command
        self.model = model.strip() if model else None
        self.reasoning_effort = reasoning_effort.strip() if reasoning_effort else None
        self.role = role.strip() if role and role.strip() else "Android developer"
        self.language = language.strip() if language else None

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

            command = [
                self.command,
                "exec",
                "-C",
                str(cwd),
                "-s",
                "read-only",
            ]
            if self.model:
                command.extend(["-m", self.model])
            if self.reasoning_effort:
                command.extend(["-c", f'model_reasoning_effort="{self.reasoning_effort}"'])
            command.extend(
                [
                    "--output-schema",
                    str(schema_path),
                    "-o",
                    str(output_path),
                    prompt,
                ]
            )

            process = await asyncio.create_subprocess_exec(
                *command,
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
        clarification_example = json.dumps(
            {
                "status": "needs_clarification",
                "ready_for_estimate": False,
                "missing_slots": ["desired_behavior"],
                "question_specs": [
                    {
                        "question_id": "q1_today_top_behavior",
                        "slot": "desired_behavior",
                        "type": "single-select",
                        "min_select": 1,
                        "max_select": 1,
                        "prompt": "`오늘 시위 맨 위에 보는 기능`이 정확히 어떤 동작을 뜻하는지 확인이 필요합니다. 현재 일정 화면은 날짜별 섹션을 그려서 목록으로 보여주고 있어 구현 방식에 따라 범위가 크게 달라집니다.",
                        "options": [
                            "today_section_first",
                            "scroll_to_today",
                            "today_only_filter",
                            "답변 보류",
                        ],
                        "recommended_option": "today_section_first",
                        "option_descriptions": [
                            "오늘 날짜 섹션이 있으면 그 섹션만 목록 맨 위로 재정렬합니다. 현재 구조상 가장 작은 변경입니다.",
                            "목록 정렬은 유지하고, 상단에 `오늘 보기` 같은 액션을 추가해 오늘 섹션으로 바로 이동합니다.",
                            "오늘 일정만 따로 모아 보여주거나 오늘만 보도록 필터를 추가합니다. UI와 상태 변경 범위가 가장 큽니다.",
                            "지금 단계에서는 답변을 보류합니다.",
                        ],
                    }
                ],
                "estimate": None,
                "error_message": None,
            },
            ensure_ascii=False,
            indent=2,
        )
        estimate_example = json.dumps(
            {
                "status": "estimated",
                "ready_for_estimate": True,
                "missing_slots": [],
                "question_specs": [],
                "estimate": {
                    "base_commit": "abc123def456",
                    "lines_added_min": 30,
                    "lines_added_max": 80,
                    "lines_modified_min": 50,
                    "lines_modified_max": 140,
                    "lines_deleted_min": 0,
                    "lines_deleted_max": 20,
                    "lines_total_min": 80,
                    "lines_total_max": 240,
                    "files": ["app/schedule/view.py", "app/schedule/controller.py"],
                    "reasons": [
                        "오늘 날짜 섹션을 맨 위로 재정렬하려면 일정 정렬 로직과 날짜 그룹 렌더링이 함께 바뀔 가능성이 큽니다.",
                        "기존 목록 스크롤 위치와 오늘 섹션 계산이 연결돼 있다면 상태 처리 코드도 수정이 필요합니다.",
                    ],
                },
                "error_message": None,
            },
            ensure_ascii=False,
            indent=2,
        )
        return f"""
You are serving as the repository analysis agent for {BOT_NAME}.
Adopt this engineer profile while analyzing the issue and repository: {self.role}.

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

Output language:
{self.language or "(default)"}

Write all human-readable strings in the requested output language.
This includes clarification question prompts, options, option descriptions, estimate reasons, and error messages.
Do not translate file paths, branch names, repository names, issue numbers, or commit SHAs.
When you return `question_specs`, every `question_id` must be a stable machine-readable token using only letters, digits, `_`, or `-`.
When you return `question_specs`, keep the structure machine-readable JSON and let the application render the final GitHub markdown comment.
The application will render clarification questions as `Q1`, `Q2`, ... in GitHub comments, so `question_id` is an internal machine key, not the final visible heading.

Example JSON for `needs_clarification`:
```json
{clarification_example}
```

Example JSON for `estimated`:
```json
{estimate_example}
```

Return JSON only and follow the provided schema exactly.
""".strip()

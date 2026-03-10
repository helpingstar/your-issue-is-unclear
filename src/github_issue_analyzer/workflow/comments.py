from __future__ import annotations

from datetime import UTC, datetime

from github_issue_analyzer.branding import BOT_NAME
from github_issue_analyzer.models import ClarificationAnswer, EstimateResult, QuestionSpec, WorkflowState


REFRESH_LABEL = "ai:refresh"

STATE_LABELS = {
    WorkflowState.NEEDS_CLARIFICATION: "ai:needs-clarification",
    WorkflowState.READY_FOR_ESTIMATE: "ai:ready-for-estimate",
    WorkflowState.ESTIMATING: "ai:estimating",
    WorkflowState.ESTIMATED: "ai:estimated",
    WorkflowState.STALE: "ai:stale",
    WorkflowState.REFRESHING: "ai:refreshing",
    WorkflowState.STOPPED: "ai:stopped",
    WorkflowState.ERROR: "ai:error",
}

CONFIDENCE_LABELS = {
    "low": "ai:confidence:low",
    "medium": "ai:confidence:medium",
    "high": "ai:confidence:high",
}

BOOTSTRAP_LABEL_SPECS = {
    "ai:analyze": ("1d76db", f"{BOT_NAME} trigger label"),
    REFRESH_LABEL: ("bfd4f2", f"{BOT_NAME} manual refresh request"),
    "ai:needs-clarification": ("fbca04", f"{BOT_NAME} needs more detail"),
    "ai:ready-for-estimate": ("0e8a16", f"{BOT_NAME} has enough detail to estimate"),
    "ai:estimating": ("5319e7", f"{BOT_NAME} is running estimate"),
    "ai:estimated": ("0052cc", f"{BOT_NAME} posted an estimate"),
    "ai:stale": ("b60205", f"{BOT_NAME} estimate is stale"),
    "ai:refreshing": ("c5def5", f"{BOT_NAME} is refreshing"),
    "ai:stopped": ("6a737d", f"{BOT_NAME} workflow was stopped"),
    "ai:error": ("d93f0b", f"{BOT_NAME} encountered an error"),
}


def _render_agent_settings_lines(model: str | None, reasoning_effort: str | None) -> list[str]:
    resolved_model = model or "(codex default)"
    resolved_reasoning_effort = reasoning_effort or "(codex default)"
    return [
        f"- 모델: `{resolved_model}`",
        f"- reasoning_effort: `{resolved_reasoning_effort}`",
    ]


def _render_clarification_answer_lines(answers: list[ClarificationAnswer]) -> list[str]:
    lines: list[str] = []
    for index, answer in enumerate(answers, start=1):
        lines.append(f"{index}. 질문: {answer.prompt}")
        lines.append(f"{index}. 답변: {answer.answer_value()}")
        description = answer.answer_description()
        if description:
            lines.append(f"{index}. 답변 설명: {description}")
        lines.append("")
    if lines:
        lines.pop()
        return lines
    return lines or ["- (정리된 요구사항 없음)"]


def _render_requirement_snapshot_lines(
    issue_title: str,
    issue_body: str,
    answers: list[ClarificationAnswer],
) -> list[str]:
    body_lines = issue_body.splitlines() if issue_body.strip() else ["(본문 없음)"]
    return [
        "요구사항 정리:",
        "````text",
        "[원본 이슈]",
        f"제목: {issue_title}",
        "본문:",
        *body_lines,
        "",
        "[clarification 답변]",
        *_render_clarification_answer_lines(answers),
        "````",
    ]


def render_clarification_comment(
    missing_slots: list[str],
    question_specs: list[QuestionSpec],
    round_number: int,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> str:
    lines = [
        f"[{BOT_NAME}]",
        "",
        *_render_agent_settings_lines(model, reasoning_effort),
        f"현재 부족한 항목: {', '.join(missing_slots)}",
        "아래 체크리스트를 직접 수정해 답변해 주세요.",
        "선택지에 없으면 새 댓글로 `Q번호: 내용` 형식으로 답해 주세요.",
        "",
        f"<!-- issue-analyzer:clarification round={round_number} -->",
    ]

    for index, spec in enumerate(question_specs, start=1):
        lines.extend(
            [
                "",
                f"### Q{index}. {spec.prompt}",
                f"- 타입: `{spec.type}`",
                f"- 허용 선택 수: `{spec.min_select}~{spec.max_select}`",
            ]
        )
        for option in spec.options:
            lines.append(f"- [ ] {option}")
        if spec.option_descriptions:
            lines.append("")
            lines.append("옵션 설명:")
            for option, description in zip(spec.options, spec.option_descriptions, strict=False):
                lines.append(f"- `{option}`: {description}")
        if spec.recommended_option:
            lines.append(f"- 추천: `{spec.recommended_option}`")

    lines.extend(
        [
            "",
            "답변이 모두 유효해지면 다음 단계로 자동 진행합니다.",
        ]
    )
    return "\n".join(lines)


def render_estimate_comment(
    issue_title: str,
    issue_body: str,
    base_branch: str,
    estimate: EstimateResult,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
    clarification_answers: list[ClarificationAnswer] | None = None,
) -> str:
    now = datetime.now(UTC).isoformat()
    clarification_block: list[str] = []
    if clarification_answers:
        clarification_block = _render_requirement_snapshot_lines(
            issue_title,
            issue_body,
            clarification_answers,
        )
    return "\n".join(
        [
            f"[{BOT_NAME}]",
            "",
            *clarification_block,
            *([""] if clarification_block else []),
            f"- 분석 시각: `{now}`",
            *_render_agent_settings_lines(model, reasoning_effort),
            f"- 기준 브랜치: `{base_branch}`",
            f"- 기준 커밋: `{estimate.base_commit}`",
            f"- 예상 추가: `+{estimate.lines_added_min} ~ +{estimate.lines_added_max} lines`",
            f"- 예상 수정: `{estimate.lines_modified_min} ~ {estimate.lines_modified_max} lines touched`",
            f"- 예상 삭제: `{estimate.lines_deleted_min} ~ {estimate.lines_deleted_max} lines`",
            f"- 총 영향 범위: `{estimate.lines_total_min} ~ {estimate.lines_total_max} lines`",
            "<details>",
            "<summary>주요 파일 후보</summary>",
            "",
            *[f"- `{path}`" for path in estimate.files],
            "</details>",
            "- 근거:",
            *[f"  - {reason}" for reason in estimate.reasons],
            "",
            f"`{REFRESH_LABEL}` 라벨 또는 `/refresh` 로 전체 재평가를 다시 실행할 수 있습니다.",
        ]
    )


def render_clarification_summary_comment(
    issue_title: str,
    issue_body: str,
    answers: list[ClarificationAnswer],
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> str:
    return "\n".join(
        [
            f"[{BOT_NAME}]",
            "",
            *_render_agent_settings_lines(model, reasoning_effort),
            "clarification이 완료되어 현재까지 확인된 요구사항을 정리했습니다.",
            *_render_requirement_snapshot_lines(issue_title, issue_body, answers),
        ]
    )


def render_requirements_changed_comment() -> str:
    return "\n".join(
        [
            f"[{BOT_NAME}]",
            "",
            "기존 추정 이후 요구사항 변경이 감지되어 상태를 `needs-clarification`으로 되돌렸습니다.",
            f"필요하면 내용을 보완한 뒤 `{REFRESH_LABEL}` 라벨 또는 `/refresh` 로 전체 재평가를 다시 실행해 주세요.",
        ]
    )


def render_error_comment(
    error_message: str,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> str:
    return "\n".join(
        [
            f"[{BOT_NAME}]",
            "",
            "처리 중 오류가 발생했습니다.",
            *_render_agent_settings_lines(model, reasoning_effort),
            f"- 오류: `{error_message}`",
        ]
    )

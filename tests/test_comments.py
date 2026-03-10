from github_issue_analyzer.models import ClarificationAnswer, EstimateResult, QuestionSpec
from github_issue_analyzer.workflow.comments import (
    REFRESH_LABEL,
    render_clarification_comment,
    render_clarification_summary_comment,
    render_error_comment,
    render_estimate_comment,
    render_requirements_changed_comment,
)


def build_estimate() -> EstimateResult:
    return EstimateResult(
        base_commit="abc123",
        lines_added_min=10,
        lines_added_max=20,
        lines_modified_min=5,
        lines_modified_max=15,
        lines_deleted_min=1,
        lines_deleted_max=3,
        lines_total_min=16,
        lines_total_max=38,
        files=["src/app.py"],
        reasons=["Touches workflow logic"],
    )


def test_render_estimate_comment_includes_agent_settings() -> None:
    body = render_estimate_comment(
        "원본 제목",
        "원본 본문",
        "main",
        build_estimate(),
        model="gpt-5.4",
        reasoning_effort="medium",
    )

    assert "- 모델: `gpt-5.4`" in body
    assert "- reasoning_effort: `medium`" in body
    assert "- 신뢰도:" not in body
    assert "<details>" in body
    assert "<summary>주요 파일 후보</summary>" in body
    assert "- `src/app.py`" in body
    assert "</details>" in body
    assert REFRESH_LABEL in body


def test_render_estimate_comment_includes_clarification_summary_when_present() -> None:
    body = render_estimate_comment(
        "원본 제목",
        "첫 줄\n둘째 줄",
        "main",
        build_estimate(),
        model="gpt-5.4",
        reasoning_effort="medium",
        clarification_answers=[
            ClarificationAnswer(
                question_id="Q1",
                prompt="어느 범위인가요?",
                selected_options=["API"],
                selected_option_descriptions=["공개 API 계약만 바꿉니다."],
            )
        ],
    )

    assert "요구사항 정리:" in body
    assert "````text" in body
    assert "[원본 이슈]" in body
    assert "제목: 원본 제목" in body
    assert "첫 줄" in body
    assert "둘째 줄" in body
    assert "[clarification 답변]" in body
    assert "1. 질문: 어느 범위인가요?" in body
    assert "1. 답변: API" in body
    assert "1. 답변 설명: API: 공개 API 계약만 바꿉니다." in body


def test_render_clarification_comment_includes_agent_settings() -> None:
    body = render_clarification_comment(
        ["scope"],
        [
            QuestionSpec(
                question_id="q1_scope_choice",
                slot="scope",
                type="single-select",
                min_select=1,
                max_select=1,
                prompt="어느 범위인가요?",
                options=["API", "UI"],
            )
        ],
        1,
        model="gpt-5.4",
        reasoning_effort="medium",
    )

    assert "- 모델: `gpt-5.4`" in body
    assert "- reasoning_effort: `medium`" in body
    assert "- [ ] 답변 보류" in body
    assert "### Q1. 어느 범위인가요?" in body
    assert "q1_scope_choice" not in body


def test_render_clarification_summary_comment_wraps_requirements_in_code_block() -> None:
    body = render_clarification_summary_comment(
        "원본 제목",
        "원본 본문",
        [],
        model="gpt-5.4",
        reasoning_effort="medium",
    )

    assert "````text" in body
    assert "````" in body
    assert "제목: 원본 제목" in body
    assert "원본 본문" in body


def test_render_clarification_summary_comment_lists_answers() -> None:
    body = render_clarification_summary_comment(
        "원본 제목",
        "원본 본문",
        [
            ClarificationAnswer(
                question_id="Q1",
                slot="scope",
                prompt="어느 범위인가요?",
                selected_options=["API"],
                selected_option_descriptions=["공개 API 계약만 바꿉니다."],
            )
        ],
        model="gpt-5.4",
        reasoning_effort="medium",
    )

    assert "1. 질문: 어느 범위인가요?" in body
    assert "1. 답변: API" in body
    assert "1. 답변 설명: API: 공개 API 계약만 바꿉니다." in body


def test_render_clarification_summary_comment_numbers_multiple_answers() -> None:
    body = render_clarification_summary_comment(
        "원본 제목",
        "원본 본문",
        [
            ClarificationAnswer(
                question_id="Q1",
                slot="scope",
                prompt="어느 범위인가요?",
                selected_options=["API"],
                selected_option_descriptions=["공개 API 계약만 바꿉니다."],
            ),
            ClarificationAnswer(
                question_id="Q2",
                slot="desired_behavior",
                prompt="어떤 동작을 원하나요?",
                selected_options=["today_section_first"],
                selected_option_descriptions=["오늘 섹션만 목록 맨 위로 올립니다."],
            ),
        ],
        model="gpt-5.4",
        reasoning_effort="medium",
    )

    assert "1. 질문: 어느 범위인가요?" in body
    assert "1. 답변: API" in body
    assert "2. 질문: 어떤 동작을 원하나요?" in body
    assert "2. 답변: today_section_first" in body


def test_clarification_answer_prompt_line_prefers_slot() -> None:
    answer = ClarificationAnswer(
        question_id="q1_scope_choice",
        slot="scope",
        prompt="어느 범위인가요?",
        selected_options=["API"],
        selected_option_descriptions=["공개 API 계약만 바꿉니다."],
    )

    assert (
        answer.as_prompt_line()
        == "slot=scope | question=어느 범위인가요? | answer=API | "
        "answer_description=API: 공개 API 계약만 바꿉니다."
    )


def test_render_error_comment_uses_codex_defaults_when_settings_missing() -> None:
    body = render_error_comment("boom")

    assert "- 모델: `(codex default)`" in body
    assert "- reasoning_effort: `(codex default)`" in body


def test_render_requirements_changed_comment_mentions_refresh_label() -> None:
    body = render_requirements_changed_comment()

    assert REFRESH_LABEL in body

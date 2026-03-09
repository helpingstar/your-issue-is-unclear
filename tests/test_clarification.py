from github_issue_analyzer.models import ANSWER_PENDING_OPTION, QuestionSpec
from github_issue_analyzer.workflow.clarification import parse_clarification_comment_body


def test_parse_single_select_with_checked_box() -> None:
    body = """
### Q1. 입력/출력 변경 여부를 선택해 주세요.
- 타입: `single-select`
- 허용 선택 수: `1~1`
- [x] 있음
- [ ] 없음 (N/A)
- [ ] 아직 미정
""".strip()
    question = QuestionSpec(
        question_id="Q1",
        slot="input_output",
        type="single-select",
        min_select=1,
        max_select=1,
        prompt="입력/출력 변경 여부를 선택해 주세요.",
        options=["있음", "없음 (N/A)", "아직 미정"],
    )

    result = parse_clarification_comment_body(body, [question], [])

    assert result.valid is True
    assert result.complete is True
    assert result.answers[0].selected_options == ["있음"]


def test_parse_free_text_fallback() -> None:
    body = """
### Q1. 완료 조건을 선택해 주세요.
- [ ] 동작 구현만
- [ ] 테스트 포함
- [ ] 테스트 + 문서 포함
""".strip()
    question = QuestionSpec(
        question_id="Q1",
        slot="done_criteria",
        type="single-select",
        min_select=1,
        max_select=1,
        prompt="완료 조건을 선택해 주세요.",
        options=["동작 구현만", "테스트 포함", "테스트 + 문서 포함"],
    )

    result = parse_clarification_comment_body(body, [question], ["Q1: E2E 테스트까지 포함"])

    assert result.valid is True
    assert result.complete is True
    assert result.answers[0].free_text == "E2E 테스트까지 포함"


def test_question_spec_appends_answer_pending_option() -> None:
    question = QuestionSpec(
        question_id="Q1",
        slot="scope",
        type="single-select",
        min_select=1,
        max_select=1,
        prompt="범위를 선택해 주세요.",
        options=["API", "UI"],
    )

    assert question.options == ["API", "UI", ANSWER_PENDING_OPTION]


def test_question_spec_normalizes_lowercase_question_id() -> None:
    question = QuestionSpec(
        question_id="q1",
        slot="scope",
        type="single-select",
        min_select=1,
        max_select=1,
        prompt="범위를 선택해 주세요.",
        options=["API", "UI"],
    )

    assert question.question_id == "Q1"


def test_parse_lowercase_question_header_and_free_text() -> None:
    body = """
### q1. 완료 조건을 선택해 주세요.
- [ ] 동작 구현만
- [ ] 테스트 포함
- [ ] 테스트 + 문서 포함
""".strip()
    question = QuestionSpec(
        question_id="q1",
        slot="done_criteria",
        type="single-select",
        min_select=1,
        max_select=1,
        prompt="완료 조건을 선택해 주세요.",
        options=["동작 구현만", "테스트 포함", "테스트 + 문서 포함"],
    )

    result = parse_clarification_comment_body(body, [question], ["q1: E2E 테스트까지 포함"])

    assert result.valid is True
    assert result.complete is True
    assert result.answers[0].question_id == "Q1"
    assert result.answers[0].free_text == "E2E 테스트까지 포함"


def test_parse_question_id_with_suffix_and_checked_box() -> None:
    body = """
### q1_today_top_behavior. 오늘 시위 맨 위에 보이는 기능이 정확히 어떤 동작을 뜻하는지 확인이 필요합니다.
- 타입: `single-select`
- 허용 선택 수: `1~1`
- [x] today_section_first
- [ ] scroll_to_today
- [ ] today_only_filter
""".strip()
    question = QuestionSpec(
        question_id="q1_today_top_behavior",
        slot="desired_behavior",
        type="single-select",
        min_select=1,
        max_select=1,
        prompt="오늘 시위 맨 위에 보이는 기능이 정확히 어떤 동작을 뜻하는지 확인이 필요합니다.",
        options=["today_section_first", "scroll_to_today", "today_only_filter"],
        option_descriptions=[
            "오늘 날짜 섹션이 있으면 그 섹션만 목록 맨 위로 재정렬합니다.",
            "상단 액션으로 오늘 섹션으로 바로 이동합니다.",
            "오늘 일정만 따로 모아 보여주거나 오늘만 보도록 필터를 추가합니다.",
        ],
    )

    result = parse_clarification_comment_body(body, [question], [])

    assert result.valid is True
    assert result.complete is True
    assert result.answers[0].question_id == "q1_today_top_behavior"
    assert result.answers[0].selected_options == ["today_section_first"]


def test_parse_display_question_number_maps_to_internal_question_id() -> None:
    body = """
### Q1. 오늘 시위 맨 위에 보이는 기능이 정확히 어떤 동작을 뜻하는지 확인이 필요합니다.
- 타입: `single-select`
- 허용 선택 수: `1~1`
- [x] today_section_first
- [ ] scroll_to_today
- [ ] today_only_filter
""".strip()
    question = QuestionSpec(
        question_id="q1_today_top_behavior",
        slot="desired_behavior",
        type="single-select",
        min_select=1,
        max_select=1,
        prompt="오늘 시위 맨 위에 보이는 기능이 정확히 어떤 동작을 뜻하는지 확인이 필요합니다.",
        options=["today_section_first", "scroll_to_today", "today_only_filter"],
        option_descriptions=[
            "오늘 날짜 섹션이 있으면 그 섹션만 목록 맨 위로 재정렬합니다.",
            "상단 액션으로 오늘 섹션으로 바로 이동합니다.",
            "오늘 일정만 따로 모아 보여주거나 오늘만 보도록 필터를 추가합니다.",
        ],
    )

    result = parse_clarification_comment_body(body, [question], [])

    assert result.valid is True
    assert result.complete is True
    assert result.answers[0].question_id == "q1_today_top_behavior"
    assert result.answers[0].selected_options == ["today_section_first"]
    assert result.answers[0].selected_option_descriptions == [
        "오늘 날짜 섹션이 있으면 그 섹션만 목록 맨 위로 재정렬합니다."
    ]


def test_parse_display_question_number_free_text_maps_to_internal_question_id() -> None:
    body = """
### Q1. 완료 조건을 선택해 주세요.
- [ ] 동작 구현만
- [ ] 테스트 포함
- [ ] 테스트 + 문서 포함
""".strip()
    question = QuestionSpec(
        question_id="done_criteria",
        slot="done_criteria",
        type="single-select",
        min_select=1,
        max_select=1,
        prompt="완료 조건을 선택해 주세요.",
        options=["동작 구현만", "테스트 포함", "테스트 + 문서 포함"],
    )

    result = parse_clarification_comment_body(body, [question], ["Q1: E2E 테스트까지 포함"])

    assert result.valid is True
    assert result.complete is True
    assert result.answers[0].question_id == "done_criteria"
    assert result.answers[0].free_text == "E2E 테스트까지 포함"


def test_parse_answer_pending_option() -> None:
    body = """
### Q1. 범위를 선택해 주세요.
- 타입: `single-select`
- 허용 선택 수: `1~1`
- [ ] API
- [ ] UI
- [x] 답변 보류
""".strip()
    question = QuestionSpec(
        question_id="Q1",
        slot="scope",
        type="single-select",
        min_select=1,
        max_select=1,
        prompt="범위를 선택해 주세요.",
        options=["API", "UI"],
    )

    result = parse_clarification_comment_body(body, [question], [])

    assert result.valid is True
    assert result.complete is True
    assert result.answers[0].selected_options == ["답변 보류"]

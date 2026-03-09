from __future__ import annotations

import re
from collections import defaultdict

from github_issue_analyzer.models import (
    ClarificationAnswer,
    ClarificationParseResult,
    QuestionSpec,
)


QUESTION_HEADER_RE = re.compile(r"^###\s+([A-Za-z][A-Za-z0-9_-]*)\.", re.MULTILINE)
CHECKED_OPTION_RE = re.compile(r"^- \[(?P<mark>[ xX])\]\s+(?P<label>.+)$")
FREE_TEXT_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.+)$", re.DOTALL)


def parse_clarification_comment_body(
    body: str,
    question_specs: list[QuestionSpec],
    free_text_comments: list[str],
) -> ClarificationParseResult:
    sections = _extract_sections(body)
    free_text_by_question = _group_free_text_answers(free_text_comments)

    answers: list[ClarificationAnswer] = []
    errors: list[str] = []
    all_complete = True

    for index, question in enumerate(question_specs, start=1):
        display_id = _display_question_id(index)
        question_keys = _question_lookup_keys(question.question_id, index)
        section = _first_matching_section(sections, question_keys)
        checked = _extract_checked_options(section, question.options)
        free_text_values = _matching_free_text_values(free_text_by_question, question_keys)

        if checked and free_text_values:
            errors.append(f"{display_id}: 체크 응답과 자유 입력을 동시에 사용할 수 없습니다.")
            continue

        if len(free_text_values) > 1:
            errors.append(f"{display_id}: 자유 입력은 하나만 허용됩니다.")
            continue

        if free_text_values:
            answers.append(
                ClarificationAnswer(
                    question_id=question.question_id,
                    slot=question.slot,
                    prompt=question.prompt,
                    free_text=free_text_values[0],
                )
            )
            continue

        count = len(checked)
        if count == 0:
            all_complete = False
            continue

        if not question.min_select <= count <= question.max_select:
            errors.append(
                f"{display_id}: 허용 선택 수는 {question.min_select}~{question.max_select}개입니다."
            )
            continue

        selected_option_descriptions = _selected_option_descriptions(question, checked)

        answers.append(
            ClarificationAnswer(
                question_id=question.question_id,
                slot=question.slot,
                prompt=question.prompt,
                selected_options=checked,
                selected_option_descriptions=selected_option_descriptions,
            )
        )

    return ClarificationParseResult(
        valid=not errors,
        complete=all_complete and not errors and len(answers) == len(question_specs),
        answers=answers,
        errors=errors,
    )


def _extract_sections(body: str) -> dict[str, str]:
    matches = list(QUESTION_HEADER_RE.finditer(body))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[_normalize_question_id(match.group(1))] = body[start:end]
    return sections


def _extract_checked_options(section: str, allowed_options: list[str]) -> list[str]:
    checked: list[str] = []
    allowed = set(allowed_options)
    for line in section.splitlines():
        match = CHECKED_OPTION_RE.match(line.strip())
        if not match:
            continue
        if match.group("mark").lower() != "x":
            continue
        label = match.group("label").strip()
        if label in allowed:
            checked.append(label)
    return checked


def _group_free_text_answers(comments: list[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for body in comments:
        match = FREE_TEXT_RE.match(body.strip())
        if match:
            grouped[_normalize_question_id(match.group(1))].append(match.group(2).strip())
    return grouped


def _display_question_id(index: int) -> str:
    return f"Q{index}"


def _question_lookup_keys(question_id: str, index: int) -> tuple[str, ...]:
    display_key = _normalize_question_id(_display_question_id(index))
    internal_key = _normalize_question_id(question_id)
    if display_key == internal_key:
        return (display_key,)
    return (display_key, internal_key)


def _first_matching_section(sections: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        section = sections.get(key)
        if section is not None:
            return section
    return ""


def _matching_free_text_values(grouped: dict[str, list[str]], keys: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for key in keys:
        values.extend(grouped.get(key, []))
    return values


def _selected_option_descriptions(question: QuestionSpec, selected_options: list[str]) -> list[str]:
    option_description_map = {
        option: description
        for option, description in zip(question.options, question.option_descriptions, strict=False)
    }
    return [
        option_description_map[option]
        for option in selected_options
        if option in option_description_map
    ]


def _normalize_question_id(value: str) -> str:
    return value.strip().casefold()

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ANSWER_PENDING_OPTION = "답변 보류"


class WorkflowState(StrEnum):
    NEW = "NEW"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    READY_FOR_ESTIMATE = "READY_FOR_ESTIMATE"
    ESTIMATING = "ESTIMATING"
    ESTIMATED = "ESTIMATED"
    STALE = "STALE"
    REFRESHING = "REFRESHING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


class RepoDefaults(BaseModel):
    trigger_label: str = "ai:analyze"
    clarification_reminder_days: int = 7
    polling_interval_seconds: int = 30


class RepoConfig(BaseModel):
    owner_repo: str
    trigger_label: str | None = None
    clarification_reminder_days: int | None = None
    polling_interval_seconds: int | None = None
    base_branch_override: str | None = None
    agent_backend_override: str | None = None
    agent_model_override: str | None = None
    agent_role_override: str | None = None
    agent_language_override: str | None = None
    checkout_path_override: str | None = None
    project_v2_url: str | None = None
    project_v2_title: str | None = None
    project_v2_impact_field_name: str | None = None
    project_v2_create_if_missing: bool = False
    enabled: bool = True

    @field_validator("owner_repo")
    @classmethod
    def validate_owner_repo(cls, value: str) -> str:
        if value.count("/") != 1:
            raise ValueError("owner_repo must be in 'owner/repo' format")
        return value

    @model_validator(mode="after")
    def validate_project_v2_config(self) -> "RepoConfig":
        has_url = bool(self.project_v2_url)
        has_title = bool(self.project_v2_title)
        has_field_name = bool(self.project_v2_impact_field_name)
        if has_url and has_title:
            raise ValueError(
                "project_v2_url and project_v2_title are mutually exclusive"
            )
        if (has_url or has_title) and not has_field_name:
            raise ValueError(
                "project_v2_impact_field_name must be set together with project_v2_url or project_v2_title"
            )
        if has_field_name and has_url and self.project_v2_create_if_missing:
            raise ValueError(
                "project_v2_create_if_missing cannot be used with project_v2_url"
            )
        if self.project_v2_create_if_missing and not (has_title or (has_field_name and not has_url)):
            raise ValueError(
                "project_v2_create_if_missing requires project_v2_title or an implied default title"
            )
        return self

    @property
    def owner(self) -> str:
        return self.owner_repo.split("/", maxsplit=1)[0]

    @property
    def repo(self) -> str:
        return self.owner_repo.split("/", maxsplit=1)[1]

    def resolved_trigger_label(self, defaults: RepoDefaults) -> str:
        return self.trigger_label or defaults.trigger_label

    def resolved_reminder_days(self, defaults: RepoDefaults) -> int:
        return self.clarification_reminder_days or defaults.clarification_reminder_days

    def resolved_polling_interval(self, defaults: RepoDefaults) -> int:
        return self.polling_interval_seconds or defaults.polling_interval_seconds

    @property
    def project_v2_enabled(self) -> bool:
        return bool((self.project_v2_url or self.resolved_project_v2_title) and self.project_v2_impact_field_name)

    @property
    def resolved_project_v2_title(self) -> str | None:
        if self.project_v2_url:
            return None
        if self.project_v2_title:
            return self.project_v2_title
        if not self.project_v2_impact_field_name:
            return None
        return f"{self.repo}_project_issue_prioritization"


class FileConfig(BaseModel):
    defaults: RepoDefaults = Field(default_factory=RepoDefaults)
    repos: list[RepoConfig] = Field(default_factory=list)


class AppRuntimeSettings(BaseModel):
    github_app_id: int
    github_app_private_key_path: Path
    github_api_base_url: str = "https://api.github.com"
    github_project_token: str | None = None
    clarification_debounce_seconds: int = 10
    active_clarification_polling_seconds: int = 10
    clarification_timeout_seconds: int = 300
    estimate_timeout_seconds: int = 1800
    default_agent_backend: str = "codex"
    default_agent_model: str | None = None
    default_agent_reasoning_effort: str | None = None
    default_agent_role: str = "Android developer"
    default_agent_language: str | None = None
    log_level: str = "INFO"


class RecognizedComment(BaseModel):
    comment_id: int
    author_login: str
    body: str
    created_at: str | None = None
    updated_at: str | None = None


class QuestionSpec(BaseModel):
    question_id: str
    slot: str
    type: Literal["single-select", "multi-select"]
    min_select: int
    max_select: int
    prompt: str
    options: list[str]
    recommended_option: str | None = None
    option_descriptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def ensure_answer_pending_option(self) -> "QuestionSpec":
        if ANSWER_PENDING_OPTION not in self.options:
            self.options.append(ANSWER_PENDING_OPTION)
        return self


class EstimateResult(BaseModel):
    base_commit: str | None = None
    lines_added_min: int
    lines_added_max: int
    lines_modified_min: int
    lines_modified_max: int
    lines_deleted_min: int
    lines_deleted_max: int
    lines_total_min: int
    lines_total_max: int
    files: list[str]
    reasons: list[str]

    def representative_total_impact(self) -> int:
        total = self.lines_total_min + self.lines_total_max
        return (total + 1) // 2


class AgentResponse(BaseModel):
    status: Literal["needs_clarification", "estimated", "error"]
    ready_for_estimate: bool
    missing_slots: list[str] = Field(default_factory=list)
    question_specs: list[QuestionSpec] = Field(default_factory=list)
    estimate: EstimateResult | None = None
    error_message: str | None = None


class AgentRequest(BaseModel):
    owner_repo: str
    issue_number: int
    issue_title: str
    issue_body: str
    checkout_path: Path
    base_branch: str
    accepted_comments: list[RecognizedComment] = Field(default_factory=list)
    clarification_answers: list[str] = Field(default_factory=list)


class ClarificationAnswer(BaseModel):
    question_id: str
    prompt: str
    selected_options: list[str] = Field(default_factory=list)
    free_text: str | None = None


class ClarificationParseResult(BaseModel):
    valid: bool
    complete: bool
    answers: list[ClarificationAnswer] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    def as_prompt_lines(self) -> list[str]:
        lines: list[str] = []
        for answer in self.answers:
            if answer.free_text:
                value = answer.free_text
            else:
                value = ", ".join(answer.selected_options)
            lines.append(f"{answer.question_id} ({answer.prompt}): {value}")
        return lines

    def as_summary_lines(self) -> list[str]:
        lines: list[str] = []
        for answer in self.answers:
            if answer.free_text:
                value = answer.free_text
            else:
                value = ", ".join(answer.selected_options)
            lines.append(f"- {answer.prompt}: {value}")
        return lines

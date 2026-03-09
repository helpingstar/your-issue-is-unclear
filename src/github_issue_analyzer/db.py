from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from github_issue_analyzer.models import RepoConfig, RepoDefaults, WorkflowState


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class RepoRegistrationORM(Base):
    __tablename__ = "repo_registrations"

    owner_repo: Mapped[str] = mapped_column(String, primary_key=True)
    app_installation_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checkout_path: Mapped[str] = mapped_column(String)
    checkout_path_override: Mapped[str | None] = mapped_column(String, nullable=True)
    trigger_label: Mapped[str] = mapped_column(String)
    clarification_reminder_days: Mapped[int] = mapped_column(Integer, default=7)
    polling_interval_seconds: Mapped[int] = mapped_column(Integer, default=30)
    base_branch_override: Mapped[str | None] = mapped_column(String, nullable=True)
    agent_backend_override: Mapped[str | None] = mapped_column(String, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_issue_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class IssueRecordORM(Base):
    __tablename__ = "issue_records"

    owner_repo: Mapped[str] = mapped_column(String, primary_key=True)
    issue_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    issue_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issue_state: Mapped[str] = mapped_column(String, default="open")
    workflow_state: Mapped[str] = mapped_column(String, default=WorkflowState.NEW.value)
    trigger_label_present: Mapped[bool] = mapped_column(Boolean, default=False)
    latest_processed_comment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latest_body_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    active_clarification_round: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_clarification_comment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    base_branch: Mapped[str | None] = mapped_column(String, nullable=True)
    base_commit_sha: Mapped[str | None] = mapped_column(String, nullable=True)
    last_estimated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ClarificationSessionORM(Base):
    __tablename__ = "clarification_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_repo: Mapped[str] = mapped_column(String, index=True)
    issue_number: Mapped[int] = mapped_column(Integer, index=True)
    round: Mapped[int] = mapped_column(Integer)
    clarification_comment_id: Mapped[int] = mapped_column(Integer)
    missing_slots: Mapped[list[str]] = mapped_column(JSON)
    question_specs: Mapped[list[dict]] = mapped_column(JSON)
    answer_sources: Mapped[list[dict]] = mapped_column(JSON, default=list)
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    superseded: Mapped[bool] = mapped_column(Boolean, default=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EstimateSnapshotORM(Base):
    __tablename__ = "estimate_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_repo: Mapped[str] = mapped_column(String, index=True)
    issue_number: Mapped[int] = mapped_column(Integer, index=True)
    base_commit_sha: Mapped[str] = mapped_column(String)
    lines_added_min: Mapped[int] = mapped_column(Integer)
    lines_added_max: Mapped[int] = mapped_column(Integer)
    lines_modified_min: Mapped[int] = mapped_column(Integer)
    lines_modified_max: Mapped[int] = mapped_column(Integer)
    lines_deleted_min: Mapped[int] = mapped_column(Integer)
    lines_deleted_max: Mapped[int] = mapped_column(Integer)
    lines_total_min: Mapped[int] = mapped_column(Integer)
    lines_total_max: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[str] = mapped_column(String, default="")
    candidate_files: Mapped[list[str]] = mapped_column(JSON)
    reasons: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobRunORM(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String)
    owner_repo: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class StateStore:
    def __init__(self, db_path: Path) -> None:
        self.engine = create_engine(f"sqlite:///{db_path}", future=True)
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def sync_repo_registration(
        self,
        repo: RepoConfig,
        defaults: RepoDefaults,
        checkout_path: Path,
        app_installation_id: int | None = None,
    ) -> RepoRegistrationORM:
        with self.session() as session:
            record = session.get(RepoRegistrationORM, repo.owner_repo)
            if record is None:
                record = RepoRegistrationORM(owner_repo=repo.owner_repo, checkout_path=str(checkout_path))
                session.add(record)

            record.app_installation_id = app_installation_id
            record.checkout_path = str(checkout_path)
            record.checkout_path_override = repo.checkout_path_override
            record.trigger_label = repo.resolved_trigger_label(defaults)
            record.clarification_reminder_days = repo.resolved_reminder_days(defaults)
            record.polling_interval_seconds = repo.resolved_polling_interval(defaults)
            record.base_branch_override = repo.base_branch_override
            record.agent_backend_override = repo.agent_backend_override
            record.enabled = repo.enabled
            session.flush()
            return record

    def list_repo_registrations(self) -> list[RepoRegistrationORM]:
        with self.session() as session:
            return list(session.scalars(select(RepoRegistrationORM)))

    def get_repo_registration(self, owner_repo: str) -> RepoRegistrationORM | None:
        with self.session() as session:
            return session.get(RepoRegistrationORM, owner_repo)

    def touch_repo_poll(self, owner_repo: str) -> None:
        with self.session() as session:
            record = session.get(RepoRegistrationORM, owner_repo)
            if record:
                record.last_issue_poll_at = utcnow()

    def get_or_create_issue_record(self, owner_repo: str, issue_number: int) -> IssueRecordORM:
        with self.session() as session:
            record = session.get(IssueRecordORM, {"owner_repo": owner_repo, "issue_number": issue_number})
            if record is None:
                record = IssueRecordORM(owner_repo=owner_repo, issue_number=issue_number)
                session.add(record)
                session.flush()
            return record

    def update_issue_record(self, owner_repo: str, issue_number: int, **fields: object) -> IssueRecordORM:
        with self.session() as session:
            record = session.get(IssueRecordORM, {"owner_repo": owner_repo, "issue_number": issue_number})
            if record is None:
                record = IssueRecordORM(owner_repo=owner_repo, issue_number=issue_number)
                session.add(record)
            for key, value in fields.items():
                setattr(record, key, value)
            session.flush()
            return record

    def get_active_clarification_session(
        self, owner_repo: str, issue_number: int
    ) -> ClarificationSessionORM | None:
        stmt = (
            select(ClarificationSessionORM)
            .where(ClarificationSessionORM.owner_repo == owner_repo)
            .where(ClarificationSessionORM.issue_number == issue_number)
            .where(ClarificationSessionORM.resolved.is_(False))
            .where(ClarificationSessionORM.superseded.is_(False))
            .order_by(ClarificationSessionORM.round.desc())
        )
        with self.session() as session:
            return session.scalars(stmt).first()

    def list_active_clarification_sessions(self) -> list[ClarificationSessionORM]:
        stmt = (
            select(ClarificationSessionORM)
            .where(ClarificationSessionORM.resolved.is_(False))
            .where(ClarificationSessionORM.superseded.is_(False))
        )
        with self.session() as session:
            return list(session.scalars(stmt))

    def list_clarification_sessions_for_issue(
        self, owner_repo: str, issue_number: int
    ) -> list[ClarificationSessionORM]:
        stmt = (
            select(ClarificationSessionORM)
            .where(ClarificationSessionORM.owner_repo == owner_repo)
            .where(ClarificationSessionORM.issue_number == issue_number)
            .order_by(ClarificationSessionORM.round.asc(), ClarificationSessionORM.id.asc())
        )
        with self.session() as session:
            return list(session.scalars(stmt))

    def supersede_clarification_sessions(self, owner_repo: str, issue_number: int) -> None:
        now = utcnow()
        with self.session() as session:
            stmt = (
                select(ClarificationSessionORM)
                .where(ClarificationSessionORM.owner_repo == owner_repo)
                .where(ClarificationSessionORM.issue_number == issue_number)
                .where(ClarificationSessionORM.resolved.is_(False))
                .where(ClarificationSessionORM.superseded.is_(False))
            )
            for record in session.scalars(stmt):
                record.superseded = True
                record.superseded_at = now

    def create_clarification_session(
        self,
        owner_repo: str,
        issue_number: int,
        round_number: int,
        clarification_comment_id: int,
        missing_slots: list[str],
        question_specs: list[dict],
    ) -> ClarificationSessionORM:
        with self.session() as session:
            record = ClarificationSessionORM(
                owner_repo=owner_repo,
                issue_number=issue_number,
                round=round_number,
                clarification_comment_id=clarification_comment_id,
                missing_slots=missing_slots,
                question_specs=question_specs,
                answer_sources=[],
            )
            session.add(record)
            session.flush()
            return record

    def resolve_clarification_session(
        self, owner_repo: str, issue_number: int, answer_sources: list[dict]
    ) -> None:
        with self.session() as session:
            stmt = (
                select(ClarificationSessionORM)
                .where(ClarificationSessionORM.owner_repo == owner_repo)
                .where(ClarificationSessionORM.issue_number == issue_number)
                .where(ClarificationSessionORM.resolved.is_(False))
                .where(ClarificationSessionORM.superseded.is_(False))
            )
            record = session.scalars(stmt).first()
            if record:
                record.resolved = True
                record.answer_sources = answer_sources

    def touch_clarification_poll(self, session_id: int) -> None:
        with self.session() as session:
            record = session.get(ClarificationSessionORM, session_id)
            if record:
                record.last_polled_at = utcnow()

    def update_clarification_session_answer_sources(
        self,
        session_id: int,
        answer_sources: list[dict],
    ) -> None:
        with self.session() as session:
            record = session.get(ClarificationSessionORM, session_id)
            if record:
                record.answer_sources = answer_sources

    def create_estimate_snapshot(
        self,
        owner_repo: str,
        issue_number: int,
        estimate: dict,
    ) -> EstimateSnapshotORM:
        with self.session() as session:
            record = EstimateSnapshotORM(owner_repo=owner_repo, issue_number=issue_number, **estimate)
            session.add(record)
            session.flush()
            return record

    def get_latest_estimate(self, owner_repo: str, issue_number: int) -> EstimateSnapshotORM | None:
        stmt = (
            select(EstimateSnapshotORM)
            .where(EstimateSnapshotORM.owner_repo == owner_repo)
            .where(EstimateSnapshotORM.issue_number == issue_number)
            .order_by(EstimateSnapshotORM.created_at.desc())
        )
        with self.session() as session:
            return session.scalars(stmt).first()

    def list_estimated_issue_records(self, owner_repo: str) -> list[IssueRecordORM]:
        stmt = (
            select(IssueRecordORM)
            .where(IssueRecordORM.owner_repo == owner_repo)
            .where(IssueRecordORM.workflow_state == WorkflowState.ESTIMATED.value)
            .where(IssueRecordORM.issue_state == "open")
        )
        with self.session() as session:
            return list(session.scalars(stmt))

    def create_job_run(self, job_type: str, owner_repo: str | None = None) -> int:
        with self.session() as session:
            record = JobRunORM(job_type=job_type, owner_repo=owner_repo, status="running")
            session.add(record)
            session.flush()
            return record.id

    def finish_job_run(self, job_run_id: int, status: str, error_message: str | None = None) -> None:
        with self.session() as session:
            record = session.get(JobRunORM, job_run_id)
            if record:
                record.status = status
                record.error_message = error_message
                record.finished_at = utcnow()

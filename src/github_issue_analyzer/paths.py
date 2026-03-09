from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_log_path, user_state_path


APP_NAME = "your-issue-is-unclear"


@dataclass(frozen=True)
class AppPaths:
    project_root: Path
    config_file: Path
    state_dir: Path
    db_path: Path
    checkout_root: Path
    log_root: Path

    @classmethod
    def from_environment(cls, project_root: Path, config_file: Path) -> "AppPaths":
        state_dir = Path(
            os.getenv("GIA_STATE_DIR", user_state_path(APP_NAME, ensure_exists=True))
        )
        db_path = Path(os.getenv("GIA_DB_PATH", state_dir / "analyzer.db"))
        checkout_root = Path(os.getenv("GIA_CHECKOUT_ROOT", state_dir / "checkouts"))
        log_root = Path(os.getenv("GIA_LOG_ROOT", user_log_path(APP_NAME, ensure_exists=True)))

        state_dir.mkdir(parents=True, exist_ok=True)
        checkout_root.mkdir(parents=True, exist_ok=True)
        log_root.mkdir(parents=True, exist_ok=True)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        return cls(
            project_root=project_root,
            config_file=config_file,
            state_dir=state_dir,
            db_path=db_path,
            checkout_root=checkout_root,
            log_root=log_root,
        )

    def checkout_path_for(self, owner_repo: str, override: str | None = None) -> Path:
        if override:
            return Path(override).expanduser().resolve()
        owner, repo = owner_repo.split("/", maxsplit=1)
        return self.checkout_root / owner / repo

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app import constants

T = TypeVar("T", bound=BaseModel)


def ensure_dirs(paths: Iterable[Path]) -> None:
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def ensure_project_structure() -> None:
    ensure_dirs(
        [
            constants.CONFIG_DIR,
            constants.SCHEMAS_DIR,
            constants.CANDIDATES_DIR,
            constants.OBSERVATIONS_DIR,
            constants.CAMPAIGNS_DIR,
            constants.REPORTS_DIR,
            constants.STATE_DIR,
            constants.JOBS_DIR,
            constants.LOGS_DIR,
            constants.SCREENSHOTS_DIR,
            constants.RAW_HTML_DIR,
            constants.RAW_TEXT_DIR,
        ]
    )


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_model(path: Path, model_cls: type[T]) -> T:
    return model_cls.model_validate(read_json(path))


def write_model(path: Path, model: BaseModel) -> None:
    write_json(path, model.model_dump(mode="json"))


def list_json_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(p for p in directory.glob("*.json") if p.is_file())


def iso_now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def iso_now_tz(tz_name: str = "America/Sao_Paulo") -> str:
    return datetime.now(ZoneInfo(tz_name)).isoformat()


def stamp_for_id(tz_name: str = "America/Sao_Paulo") -> str:
    return datetime.now(ZoneInfo(tz_name)).strftime("%Y%m%d_%H%M%S")


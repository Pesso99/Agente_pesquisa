from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import constants
from app.models import Handoff


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuntimeDB:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or constants.RUNTIME_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "RuntimeDB":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _init_schema(self) -> None:
        cursor = self.conn.cursor()
        cursor.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                config_json TEXT,
                last_error TEXT
            );

            CREATE TABLE IF NOT EXISTS handoffs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                task TEXT NOT NULL,
                source_agent TEXT NOT NULL,
                target_agent TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                source_quality_label TEXT NOT NULL,
                capture_quality_score REAL NOT NULL,
                blocking_reasons_json TEXT NOT NULL,
                input_refs_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                trace_id TEXT NOT NULL,
                source_agent TEXT NOT NULL,
                target_agent TEXT NOT NULL,
                message_type TEXT NOT NULL,
                body_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                status TEXT NOT NULL,
                error_message TEXT,
                backoff_seconds INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS artifacts_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                artifact_type TEXT NOT NULL,
                path TEXT NOT NULL,
                meta_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS approvals (
                job_id TEXT PRIMARY KEY,
                approval_status TEXT NOT NULL,
                approved_by TEXT,
                notes TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dead_letters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                record_id TEXT,
                error_message TEXT NOT NULL,
                payload_json TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fingerprints (
                fingerprint TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                campaign_id TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def upsert_job(self, job_id: str, *, status: str, mode: str, config: dict[str, Any] | None = None) -> None:
        now = _utc_now()
        payload = json.dumps(config, ensure_ascii=False) if config else None
        self.conn.execute(
            """
            INSERT INTO jobs (job_id, status, mode, created_at, updated_at, config_json, last_error)
            VALUES (?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(job_id) DO UPDATE SET
                status = excluded.status,
                mode = excluded.mode,
                updated_at = excluded.updated_at,
                config_json = excluded.config_json
            """,
            (job_id, status, mode, now, now, payload),
        )
        self.conn.commit()

    def set_job_status(self, job_id: str, status: str, *, last_error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE jobs SET status = ?, updated_at = ?, last_error = ? WHERE job_id = ?",
            (status, _utc_now(), last_error, job_id),
        )
        self.conn.commit()

    def add_handoff(self, handoff: Handoff) -> None:
        self.conn.execute(
            """
            INSERT INTO handoffs (
                job_id, trace_id, task, source_agent, target_agent, attempt,
                source_quality_label, capture_quality_score, blocking_reasons_json, input_refs_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                handoff.job_id,
                handoff.trace_id,
                handoff.task,
                handoff.source_agent,
                handoff.target_agent,
                handoff.attempt,
                handoff.source_quality_label,
                handoff.capture_quality_score,
                json.dumps(handoff.blocking_reasons, ensure_ascii=False),
                json.dumps(handoff.input_refs, ensure_ascii=False),
                handoff.created_at,
            ),
        )
        self.conn.commit()

    def add_agent_message(
        self,
        *,
        job_id: str,
        trace_id: str,
        source_agent: str,
        target_agent: str,
        message_type: str,
        body: dict[str, Any],
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO agent_messages (
                job_id, trace_id, source_agent, target_agent, message_type, body_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                trace_id,
                source_agent,
                target_agent,
                message_type,
                json.dumps(body, ensure_ascii=False),
                _utc_now(),
            ),
        )
        self.conn.commit()

    def log_run(
        self,
        *,
        job_id: str,
        stage: str,
        attempt: int,
        status: str,
        error_message: str | None = None,
        backoff_seconds: int = 0,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO runs (job_id, stage, attempt, status, error_message, backoff_seconds, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, stage, attempt, status, error_message, backoff_seconds, _utc_now()),
        )
        self.conn.commit()

    def index_artifact(
        self,
        *,
        job_id: str,
        entity_type: str,
        entity_id: str,
        artifact_type: str,
        path: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO artifacts_index (job_id, entity_type, entity_id, artifact_type, path, meta_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                entity_type,
                entity_id,
                artifact_type,
                path,
                json.dumps(meta or {}, ensure_ascii=False),
                _utc_now(),
            ),
        )
        self.conn.commit()

    def ensure_approval(self, job_id: str) -> None:
        self.conn.execute(
            """
            INSERT INTO approvals (job_id, approval_status, approved_by, notes, updated_at)
            VALUES (?, 'pending', NULL, NULL, ?)
            ON CONFLICT(job_id) DO NOTHING
            """,
            (job_id, _utc_now()),
        )
        self.conn.commit()

    def set_approval(
        self, job_id: str, *, status: str, approved_by: str | None = None, notes: str | None = None
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO approvals (job_id, approval_status, approved_by, notes, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                approval_status = excluded.approval_status,
                approved_by = excluded.approved_by,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (job_id, status, approved_by, notes, _utc_now()),
        )
        self.conn.commit()

    def get_approval_status(self, job_id: str) -> str:
        row = self.conn.execute("SELECT approval_status FROM approvals WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            return "pending"
        return str(row["approval_status"])

    def add_dead_letter(
        self,
        *,
        job_id: str,
        stage: str,
        error_message: str,
        record_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO dead_letters (job_id, stage, record_id, error_message, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                stage,
                record_id,
                error_message,
                json.dumps(payload or {}, ensure_ascii=False),
                _utc_now(),
            ),
        )
        self.conn.commit()

    def register_fingerprint(self, fingerprint: str, *, job_id: str, campaign_id: str | None = None) -> bool:
        try:
            self.conn.execute(
                """
                INSERT INTO fingerprints (fingerprint, job_id, campaign_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (fingerprint, job_id, campaign_id, _utc_now()),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def list_review_jobs(self) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT job_id
            FROM runs
            WHERE status = 'review' OR stage LIKE '%review%'
            ORDER BY id DESC
            """
        ).fetchall()
        return [str(row["job_id"]) for row in rows]

    def list_failed_job_ids(self) -> list[str]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT job_id
            FROM dead_letters
            ORDER BY id DESC
            """
        ).fetchall()
        return [str(row["job_id"]) for row in rows]


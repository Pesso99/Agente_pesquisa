from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import constants
from app.models import Campaign, Handoff


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
                instagram_modal_dismissed INTEGER,
                instagram_block_reason TEXT,
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

            CREATE TABLE IF NOT EXISTS campaign_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL,
                job_id TEXT NOT NULL,
                institution_id TEXT NOT NULL,
                campaign_name TEXT NOT NULL,
                campaign_type TEXT,
                source_url TEXT NOT NULL,
                source_type TEXT,
                status TEXT NOT NULL,
                confidence_final REAL,
                benefit TEXT,
                audience TEXT,
                start_date TEXT,
                end_date TEXT,
                channels_json TEXT,
                evidence_refs_json TEXT,
                validation_notes TEXT,
                fingerprint TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(campaign_id, job_id)
            );

            CREATE TABLE IF NOT EXISTS campaign_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL,
                verdict TEXT NOT NULL,
                reason TEXT,
                reviewed_by TEXT DEFAULT 'human',
                reviewed_at TEXT NOT NULL,
                was_correct INTEGER
            );

            CREATE TABLE IF NOT EXISTS learned_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_type TEXT NOT NULL,
                pattern_key TEXT NOT NULL,
                pattern_value REAL NOT NULL,
                sample_count INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(pattern_type, pattern_key)
            );
            """
        )
        # Backward-compatible migration for existing runtime DBs.
        columns = {
            row["name"]
            for row in cursor.execute("PRAGMA table_info(handoffs)").fetchall()
        }
        if "instagram_modal_dismissed" not in columns:
            cursor.execute("ALTER TABLE handoffs ADD COLUMN instagram_modal_dismissed INTEGER")
        if "instagram_block_reason" not in columns:
            cursor.execute("ALTER TABLE handoffs ADD COLUMN instagram_block_reason TEXT")
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
                source_quality_label, capture_quality_score, blocking_reasons_json,
                instagram_modal_dismissed, instagram_block_reason, input_refs_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                (
                    1
                    if handoff.instagram_modal_dismissed is True
                    else 0 if handoff.instagram_modal_dismissed is False else None
                ),
                handoff.instagram_block_reason,
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

    # --- Campaign History ---

    def save_to_history(
        self,
        campaign: Campaign,
        *,
        job_id: str,
        fingerprint: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO campaign_history (
                campaign_id, job_id, institution_id, campaign_name, campaign_type,
                source_url, source_type, status, confidence_final, benefit, audience,
                start_date, end_date, channels_json, evidence_refs_json,
                validation_notes, fingerprint, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id, job_id) DO UPDATE SET
                status = excluded.status,
                confidence_final = excluded.confidence_final,
                validation_notes = excluded.validation_notes
            """,
            (
                campaign.campaign_id,
                job_id,
                campaign.institution_id,
                campaign.campaign_name,
                campaign.campaign_type,
                campaign.source_url,
                campaign.source_type,
                campaign.status,
                campaign.confidence_final,
                campaign.benefit,
                campaign.audience,
                campaign.start_date,
                campaign.end_date,
                json.dumps(campaign.channels, ensure_ascii=False),
                json.dumps(campaign.evidence_refs, ensure_ascii=False),
                campaign.validation_notes,
                fingerprint,
                _utc_now(),
            ),
        )
        self.conn.commit()

    def get_campaign_history(
        self,
        *,
        institution_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if institution_id:
            clauses.append("institution_id = ?")
            params.append(institution_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        rows = self.conn.execute(
            f"SELECT * FROM campaign_history {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def find_similar_in_history(
        self,
        *,
        fingerprint: str | None = None,
        source_url: str | None = None,
        institution_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if fingerprint:
            clauses.append("fingerprint = ?")
            params.append(fingerprint)
        if source_url:
            clauses.append("source_url = ?")
            params.append(source_url)
        if institution_id and not fingerprint and not source_url:
            clauses.append("institution_id = ?")
            params.append(institution_id)
        if not clauses:
            return []
        where = " OR ".join(clauses) if fingerprint or source_url else " AND ".join(clauses)
        rows = self.conn.execute(
            f"SELECT * FROM campaign_history WHERE {where} ORDER BY created_at DESC LIMIT 20",
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    # --- Campaign Feedback ---

    def add_feedback(
        self,
        campaign_id: str,
        *,
        verdict: str,
        reason: str | None = None,
        reviewed_by: str = "human",
        was_correct: bool | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO campaign_feedback (campaign_id, verdict, reason, reviewed_by, reviewed_at, was_correct)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                campaign_id,
                verdict,
                reason,
                reviewed_by,
                _utc_now(),
                (1 if was_correct else 0) if was_correct is not None else None,
            ),
        )
        self.conn.commit()

    def get_feedback_for_campaign(self, campaign_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM campaign_feedback WHERE campaign_id = ? ORDER BY reviewed_at DESC",
            (campaign_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_feedback_stats(self) -> dict[str, Any]:
        total = self.conn.execute("SELECT COUNT(*) as cnt FROM campaign_feedback").fetchone()["cnt"]
        confirmed = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM campaign_feedback WHERE verdict = 'confirmed'"
        ).fetchone()["cnt"]
        denied = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM campaign_feedback WHERE verdict = 'denied'"
        ).fetchone()["cnt"]
        uncertain = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM campaign_feedback WHERE verdict = 'uncertain'"
        ).fetchone()["cnt"]
        correct = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM campaign_feedback WHERE was_correct = 1"
        ).fetchone()["cnt"]
        incorrect = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM campaign_feedback WHERE was_correct = 0"
        ).fetchone()["cnt"]
        return {
            "total": total,
            "confirmed": confirmed,
            "denied": denied,
            "uncertain": uncertain,
            "correct": correct,
            "incorrect": incorrect,
            "accuracy": round(correct / (correct + incorrect), 3) if (correct + incorrect) > 0 else None,
        }

    def list_campaigns_without_feedback(
        self,
        *,
        status: str | None = None,
        institution_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses = [
            "h.campaign_id NOT IN (SELECT campaign_id FROM campaign_feedback)",
        ]
        params: list[Any] = []
        if status:
            clauses.append("h.status = ?")
            params.append(status)
        if institution_id:
            clauses.append("h.institution_id = ?")
            params.append(institution_id)
        where = " AND ".join(clauses)
        params.append(limit)
        rows = self.conn.execute(
            f"""
            SELECT h.* FROM campaign_history h
            WHERE {where}
            ORDER BY h.created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    # --- Learned Patterns ---

    def save_learned_pattern(
        self,
        *,
        pattern_type: str,
        pattern_key: str,
        pattern_value: float,
        sample_count: int,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO learned_patterns (pattern_type, pattern_key, pattern_value, sample_count, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(pattern_type, pattern_key) DO UPDATE SET
                pattern_value = excluded.pattern_value,
                sample_count = excluded.sample_count,
                updated_at = excluded.updated_at
            """,
            (pattern_type, pattern_key, pattern_value, sample_count, _utc_now()),
        )
        self.conn.commit()

    def get_learned_patterns(self, pattern_type: str | None = None) -> list[dict[str, Any]]:
        if pattern_type:
            rows = self.conn.execute(
                "SELECT * FROM learned_patterns WHERE pattern_type = ? ORDER BY pattern_value DESC",
                (pattern_type,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM learned_patterns ORDER BY pattern_type, pattern_value DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def clear_learned_patterns(self) -> None:
        self.conn.execute("DELETE FROM learned_patterns")
        self.conn.commit()

    # --- Feedback-enriched history queries ---

    def get_confirmed_campaigns(self, *, institution_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        clauses = ["f.verdict = 'confirmed'"]
        params: list[Any] = []
        if institution_id:
            clauses.append("h.institution_id = ?")
            params.append(institution_id)
        where = " AND ".join(clauses)
        params.append(limit)
        rows = self.conn.execute(
            f"""
            SELECT h.*, f.verdict, f.reason as feedback_reason
            FROM campaign_history h
            JOIN campaign_feedback f ON h.campaign_id = f.campaign_id
            WHERE {where}
            ORDER BY f.reviewed_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

    def get_denied_campaigns(self, *, institution_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        clauses = ["f.verdict = 'denied'"]
        params: list[Any] = []
        if institution_id:
            clauses.append("h.institution_id = ?")
            params.append(institution_id)
        where = " AND ".join(clauses)
        params.append(limit)
        rows = self.conn.execute(
            f"""
            SELECT h.*, f.verdict, f.reason as feedback_reason
            FROM campaign_history h
            JOIN campaign_feedback f ON h.campaign_id = f.campaign_id
            WHERE {where}
            ORDER BY f.reviewed_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [dict(row) for row in rows]

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

from __future__ import annotations

import pytest

from app.orchestrator import _run_with_retry
from app.runtime_db import RuntimeDB


def test_retry_writes_dead_letter(tmp_path) -> None:
    db_path = tmp_path / "runtime_test.db"
    with RuntimeDB(db_path=db_path) as db:
        db.upsert_job("job_retry", status="running", mode="autonomous")

        def always_fail(_attempt: int):
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            _run_with_retry(
                db,
                "job_retry",
                "capture",
                max_attempts=2,
                backoff_base=1,
                fn=always_fail,
            )

        count = db.conn.execute("SELECT COUNT(*) AS n FROM dead_letters WHERE job_id = ?", ("job_retry",)).fetchone()["n"]
        assert count >= 1


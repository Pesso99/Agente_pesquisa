from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.io_utils import stamp_for_id
from app.orchestrator import run_autonomous_cycle
from app.runtime_db import RuntimeDB


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reexecuta jobs com falha (dead letters).")
    parser.add_argument("--job-id", default="all", help="ID específico ou 'all'.")
    parser.add_argument("--max-total", type=int, default=None)
    parser.add_argument("--capture-timeout", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    overrides: dict[str, int] = {}
    if args.max_total is not None:
        overrides["max_candidates_total"] = args.max_total
    if args.capture_timeout is not None:
        overrides["capture_timeout_seconds"] = args.capture_timeout

    with RuntimeDB() as db:
        targets = db.list_failed_job_ids() if args.job_id == "all" else [args.job_id]

    if not targets:
        print("Nenhum job com falha encontrado para replay.")
        return

    for source_job in targets:
        replay_job = f"replay_{source_job}_{stamp_for_id()}"
        print(f"Reexecutando {source_job} -> {replay_job}")
        run_autonomous_cycle(
            replay_job,
            send_report_email=False,
            routing_overrides=overrides or None,
            autonomous=True,
        )
        print(f"Replay concluído: {replay_job}")


if __name__ == "__main__":
    main()


from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import constants
from app.io_utils import list_json_files, read_json, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fila jobs com falha para retry.")
    parser.add_argument("--jobs-dir", default=str(constants.JOBS_DIR))
    parser.add_argument("--output", default=str(constants.STATE_DIR / "retry_queue.json"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    retry_ids: list[str] = []

    for path in list_json_files(Path(args.jobs_dir)):
        payload = read_json(path)
        if not isinstance(payload, dict):
            continue
        status = payload.get("status")
        email_sent = payload.get("email_sent")
        if status == "failed" or email_sent is False:
            job_id = payload.get("job_id")
            if job_id:
                retry_ids.append(job_id)

    write_json(Path(args.output), {"retry_job_ids": retry_ids, "count": len(retry_ids)})
    print(f"Jobs enfileirados para retry: {len(retry_ids)}")


if __name__ == "__main__":
    main()


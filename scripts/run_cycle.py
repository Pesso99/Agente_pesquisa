from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.io_utils import stamp_for_id
from app.orchestrator import run_autonomous_cycle, run_manual_cycle


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Executa ciclo operacional com runtime multiagente.")
    parser.add_argument("--job-id", default=f"cycle_{stamp_for_id()}")
    parser.add_argument("--autonomous", action="store_true")
    parser.add_argument("--send-email", action="store_true")
    parser.add_argument("--to", nargs="+", default=None, help="Destinatarios do relatorio.")
    parser.add_argument("--max-total", type=int, default=None)
    parser.add_argument("--max-per-institution", type=int, default=None)
    parser.add_argument("--capture-timeout", type=int, default=None)
    parser.add_argument("--instagram-capture-mode", default=None)
    parser.add_argument("--instagram-dismiss-attempts", type=int, default=None)
    parser.add_argument("--instagram-dismiss-timeout", type=int, default=None)
    parser.add_argument("--no-instagram-require-official-confirmation", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    overrides: dict[str, int] = {}
    if args.max_total is not None:
        overrides["max_candidates_total"] = args.max_total
    if args.max_per_institution is not None:
        overrides["max_candidates_per_institution"] = args.max_per_institution
    if args.capture_timeout is not None:
        overrides["capture_timeout_seconds"] = args.capture_timeout
    if args.instagram_capture_mode is not None:
        overrides["instagram_capture_mode"] = args.instagram_capture_mode
    if args.instagram_dismiss_attempts is not None:
        overrides["instagram_dismiss_attempts"] = args.instagram_dismiss_attempts
    if args.instagram_dismiss_timeout is not None:
        overrides["instagram_dismiss_timeout_seconds"] = args.instagram_dismiss_timeout
    if args.no_instagram_require_official_confirmation:
        overrides["instagram_require_official_confirmation"] = False

    runner = run_autonomous_cycle if args.autonomous else run_manual_cycle
    result = runner(
        args.job_id,
        send_report_email=args.send_email,
        recipients=args.to,
        routing_overrides=overrides or None,
    )

    print(f"Job concluido: {result.job_id}")
    print(f"Candidates: {len(result.candidates)}")
    print(f"Observations: {len(result.observations)}")
    print(f"Campaigns finais: {len(result.campaigns)}")
    for kind, path in result.report_paths.items():
        print(f"- {kind}: {path}")
    print(f"E-mail enviado: {result.email_sent}")


if __name__ == "__main__":
    main()

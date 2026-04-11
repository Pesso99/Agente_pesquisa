from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.io_utils import stamp_for_id
from app.orchestrator import run_manual_cycle


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Executa um ciclo manual ponta a ponta.")
    parser.add_argument("--job-id", default=f"manual_{stamp_for_id()}")
    parser.add_argument("--send-email", action="store_true")
    parser.add_argument("--to", nargs="+", default=None, help="Destinatarios do relatorio.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_manual_cycle(
        args.job_id,
        send_report_email=args.send_email,
        recipients=args.to,
    )

    print(f"Job concluido: {result.job_id}")
    print(f"Candidates: {len(result.candidates)}")
    print(f"Observations: {len(result.observations)}")
    print(f"Campaigns finais: {len(result.campaigns)}")
    print("Relatorios:")
    for kind, path in result.report_paths.items():
        print(f"- {kind}: {path}")
    print(f"E-mail enviado: {result.email_sent}")


if __name__ == "__main__":
    main()


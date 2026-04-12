from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import constants
from app.emailer import send_email
from app.io_utils import read_json
from app.runtime_db import RuntimeDB


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aprova envio de um job e opcionalmente envia o relatório.")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--approved-by", default="human_reviewer")
    parser.add_argument("--notes", default="Aprovado manualmente.")
    parser.add_argument("--send-now", action="store_true")
    parser.add_argument("--to", nargs="+", default=None)
    return parser.parse_args()


def _resolve_html_report(job_id: str) -> Path:
    report_path = constants.REPORTS_DIR / f"report_{job_id}.html"
    if report_path.exists():
        return report_path
    raise FileNotFoundError(f"Relatório HTML não encontrado para job {job_id}: {report_path}")


def main() -> None:
    args = parse_args()
    with RuntimeDB() as db:
        db.set_approval(
            args.job_id,
            status="approved",
            approved_by=args.approved_by,
            notes=args.notes,
        )
        print(f"Aprovação registrada para job: {args.job_id}")

    if not args.send_now:
        return

    html_report = _resolve_html_report(args.job_id)
    report_cfg = read_json(constants.CONFIG_DIR / "report_settings.json")
    email_cfg = read_json(constants.CONFIG_DIR / "email_settings.json")
    recipients = args.to or email_cfg.get("default_recipients", [])
    subject = f"{report_cfg.get('subject_prefix', 'Monitor diario')} - {args.job_id}"

    send_email(
        html_path=str(html_report),
        subject=subject,
        recipients=recipients,
        smtp_host=email_cfg.get("smtp_host", "smtp.gmail.com"),
        smtp_port=int(email_cfg.get("smtp_port", 587)),
        use_tls=bool(email_cfg.get("use_tls", True)),
    )
    print(f"E-mail enviado para {', '.join(recipients)}")


if __name__ == "__main__":
    main()


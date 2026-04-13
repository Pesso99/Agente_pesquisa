from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import constants
from app.emailer import send_email
from app.io_utils import read_json, stamp_for_id
from app.orchestrator import run_autonomous_cycle
from app.runtime_db import RuntimeDB


def _require_configs() -> None:
    required = [
        constants.CONFIG_DIR / "institutions.json",
        constants.CONFIG_DIR / "historical_seeds.json",
        constants.CONFIG_DIR / "report_settings.json",
        constants.CONFIG_DIR / "scoring_rules.json",
        constants.CONFIG_DIR / "email_settings.json",
        constants.CONFIG_DIR / "routing_rules.json",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        print("Configuracoes ausentes:")
        for item in missing:
            print(f"- {item}")
        raise SystemExit(1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Executa processo completo real: ciclo autonomo + resumo + aprovacao/envio opcional."
    )
    parser.add_argument("--job-id", default=f"real_{stamp_for_id()}")
    parser.add_argument("--max-total", type=int, default=8)
    parser.add_argument("--max-per-institution", type=int, default=2)
    parser.add_argument("--capture-timeout", type=int, default=12)
    parser.add_argument("--instagram-capture-mode", default="playwright_dismiss")
    parser.add_argument("--instagram-dismiss-attempts", type=int, default=3)
    parser.add_argument("--instagram-dismiss-timeout", type=int, default=2)
    parser.add_argument("--instagram-require-official-confirmation", action="store_true")
    parser.add_argument("--no-instagram-require-official-confirmation", action="store_true")
    parser.add_argument("--to", nargs="+", default=None, help="Destinatarios para envio quando --approve-and-send.")
    parser.add_argument("--approve-and-send", action="store_true", help="Registra aprovacao humana e envia email no final.")
    parser.add_argument("--approved-by", default="human_reviewer")
    parser.add_argument("--approval-notes", default="Aprovado no run_real_full.")
    return parser.parse_args()


def _print_summary(job_id: str, result_campaigns: int, report_paths: dict[str, Path], email_sent: bool) -> None:
    summary_path = constants.JOBS_DIR / f"{job_id}_summary.json"
    approval_status = "pending"
    useful_ratio = 0.0
    blocked = 0
    instagram_blocked = 0
    if summary_path.exists():
        summary = read_json(summary_path)
        quality = summary.get("quality", {})
        useful_ratio = float(quality.get("screenshot_useful_ratio", 0.0))
        blocked = int(quality.get("blocked_observations", 0))
        instagram_blocked = int(quality.get("instagram_blocked_observations", 0))
        approval_status = str(summary.get("approval_status", "pending"))

    print(f"Job concluido: {job_id}")
    print(f"Campaigns finais: {result_campaigns}")
    print(f"Screenshot util ratio: {useful_ratio:.2f}")
    print(f"Observations bloqueadas: {blocked}")
    print(f"Instagram bloqueados: {instagram_blocked}")
    print(f"Approval status: {approval_status}")
    for kind, path in report_paths.items():
        print(f"- {kind}: {path}")
    print(f"E-mail enviado: {email_sent}")


def _approve_and_send(job_id: str, approved_by: str, notes: str, recipients: list[str] | None) -> bool:
    with RuntimeDB() as db:
        db.set_approval(job_id, status="approved", approved_by=approved_by, notes=notes)

    report_cfg = read_json(constants.CONFIG_DIR / "report_settings.json")
    email_cfg = read_json(constants.CONFIG_DIR / "email_settings.json")
    report_html = constants.REPORTS_DIR / f"report_{job_id}.html"
    if not report_html.exists():
        raise FileNotFoundError(f"Relatorio HTML nao encontrado: {report_html}")

    to = recipients or email_cfg.get("default_recipients", [])
    if not to:
        print("Nenhum destinatario definido. Aprovacao registrada, envio ignorado.")
        return False

    subject = f"{report_cfg.get('subject_prefix', 'Monitor diario')} - {job_id}"
    send_email(
        html_path=str(report_html),
        subject=subject,
        recipients=to,
        smtp_host=email_cfg.get("smtp_host", "smtp.gmail.com"),
        smtp_port=int(email_cfg.get("smtp_port", 587)),
        use_tls=bool(email_cfg.get("use_tls", True)),
    )
    print(f"Aprovacao registrada por: {approved_by}")
    print(f"E-mail enviado para: {', '.join(to)}")
    return True


def main() -> None:
    args = parse_args()
    _require_configs()

    require_official_confirmation = True
    if args.no_instagram_require_official_confirmation:
        require_official_confirmation = False
    if args.instagram_require_official_confirmation:
        require_official_confirmation = True

    overrides = {
        "max_candidates_total": args.max_total,
        "max_candidates_per_institution": args.max_per_institution,
        "capture_timeout_seconds": args.capture_timeout,
        "instagram_capture_mode": args.instagram_capture_mode,
        "instagram_dismiss_attempts": args.instagram_dismiss_attempts,
        "instagram_dismiss_timeout_seconds": args.instagram_dismiss_timeout,
        "instagram_require_official_confirmation": require_official_confirmation,
    }

    result = run_autonomous_cycle(
        args.job_id,
        send_report_email=False,
        recipients=None,
        routing_overrides=overrides,
        autonomous=True,
    )

    email_sent = False
    if args.approve_and_send:
        email_sent = _approve_and_send(
            result.job_id,
            approved_by=args.approved_by,
            notes=args.approval_notes,
            recipients=args.to,
        )

    _print_summary(
        job_id=result.job_id,
        result_campaigns=len(result.campaigns),
        report_paths=result.report_paths,
        email_sent=email_sent,
    )

    if not args.approve_and_send:
        print("Proximo passo para enviar: ")
        print(
            "python scripts/approve_send.py "
            f"--job-id {result.job_id} --approved-by seu_nome --send-now --to voce@empresa.com"
        )


if __name__ == "__main__":
    main()

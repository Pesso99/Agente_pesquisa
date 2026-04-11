from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.emailer import send_email
from app.io_utils import read_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Envia relatorio HTML por SMTP.")
    parser.add_argument("--html", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--to", required=True, nargs="+")
    parser.add_argument("--smtp-host", default=None)
    parser.add_argument("--smtp-port", type=int, default=None)
    parser.add_argument("--no-tls", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    email_cfg = read_json(ROOT / "config" / "email_settings.json")
    smtp_host = args.smtp_host or email_cfg.get("smtp_host", "smtp.gmail.com")
    smtp_port = args.smtp_port or int(email_cfg.get("smtp_port", 587))
    use_tls = not args.no_tls if args.no_tls else bool(email_cfg.get("use_tls", True))

    send_email(
        html_path=args.html,
        subject=args.subject,
        recipients=args.to,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        use_tls=use_tls,
    )
    print("E-mail enviado com sucesso.")


if __name__ == "__main__":
    main()


from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import constants
from app.io_utils import list_json_files, read_json
from app.models import Campaign
from app.reporter import build_report, save_report_files


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gera relatorio a partir das campanhas.")
    parser.add_argument("--report-id", required=True)
    parser.add_argument("--campaigns-dir", default=str(constants.CAMPAIGNS_DIR))
    parser.add_argument("--output-dir", default=str(constants.REPORTS_DIR))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = read_json(constants.CONFIG_DIR / "report_settings.json")
    campaigns = [
        Campaign.model_validate(read_json(path))
        for path in list_json_files(Path(args.campaigns_dir))
    ]
    report = build_report(campaigns, settings, report_id=args.report_id)
    paths = save_report_files(report, Path(args.output_dir))
    print(f"Relatorio gerado: {args.report_id}")
    for kind, path in paths.items():
        print(f"- {kind}: {path}")


if __name__ == "__main__":
    main()


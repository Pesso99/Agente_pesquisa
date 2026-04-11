from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import constants
from app.deduper import dedupe_campaigns
from app.io_utils import list_json_files, read_json, write_json
from app.models import Campaign


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deduplica campanhas por similaridade.")
    parser.add_argument("--input-dir", default=str(constants.CAMPAIGNS_DIR))
    parser.add_argument("--threshold", type=float, default=0.88)
    parser.add_argument(
        "--output",
        default=str(constants.STATE_DIR / "dedupe_result.json"),
        help="Arquivo JSON com resultado de deduplicacao.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    campaigns = [
        Campaign.model_validate(read_json(path))
        for path in list_json_files(Path(args.input_dir))
    ]

    uniques, groups = dedupe_campaigns(campaigns, threshold=args.threshold)
    payload = {
        "threshold": args.threshold,
        "input_count": len(campaigns),
        "output_count": len(uniques),
        "groups": groups,
        "unique_campaign_ids": [campaign.campaign_id for campaign in uniques],
    }
    write_json(Path(args.output), payload)

    print(f"Campanhas de entrada: {len(campaigns)}")
    print(f"Campanhas unicas: {len(uniques)}")
    print(f"Resultado salvo em: {args.output}")


if __name__ == "__main__":
    main()


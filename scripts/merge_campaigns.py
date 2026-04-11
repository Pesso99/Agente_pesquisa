from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.io_utils import list_json_files, read_json, write_json
from app.models import Campaign


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mescla campanhas por campaign_id.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    by_id: dict[str, Campaign] = {}

    for path in list_json_files(Path(args.input_dir)):
        campaign = Campaign.model_validate(read_json(path))
        current = by_id.get(campaign.campaign_id)
        if current is None or campaign.confidence_final > current.confidence_final:
            by_id[campaign.campaign_id] = campaign

    merged = [campaign.model_dump(mode="json") for campaign in by_id.values()]
    write_json(Path(args.output), {"campaigns": merged, "count": len(merged)})
    print(f"Campanhas mescladas: {len(merged)}")


if __name__ == "__main__":
    main()


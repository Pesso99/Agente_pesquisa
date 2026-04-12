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
from app.runtime_db import RuntimeDB


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lista campanhas por status para revisão.")
    parser.add_argument("--status", default="review")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--job-id", default=None, help="Filtra campanhas indexadas em um job específico.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    status = args.status.strip().lower()
    campaigns: list[Campaign] = []
    allowed_ids: set[str] | None = None

    if args.job_id:
        with RuntimeDB() as db:
            rows = db.conn.execute(
                """
                SELECT entity_id
                FROM artifacts_index
                WHERE job_id = ? AND entity_type = 'campaign' AND artifact_type = 'campaign_json'
                """,
                (args.job_id,),
            ).fetchall()
            allowed_ids = {str(row["entity_id"]) for row in rows}

    for path in list_json_files(constants.CAMPAIGNS_DIR):
        campaign = Campaign.model_validate(read_json(path))
        if allowed_ids is not None and campaign.campaign_id not in allowed_ids:
            continue
        if campaign.status == status:
            campaigns.append(campaign)

    campaigns = sorted(campaigns, key=lambda c: c.confidence_final, reverse=True)[: args.limit]
    print(f"Campanhas com status='{status}': {len(campaigns)}")
    for campaign in campaigns:
        print(
            f"- {campaign.campaign_id} | {campaign.institution_id} | "
            f"{campaign.campaign_name[:80]} | score={campaign.confidence_final:.2f} | {campaign.source_url}"
        )


if __name__ == "__main__":
    main()

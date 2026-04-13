from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import constants
from app.io_utils import list_json_files, read_json, write_model
from app.models import Campaign, Observation
from app.scoring import validate_campaign_two_pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aplica scoring e status em campanhas.")
    parser.add_argument("--campaigns-dir", default=str(constants.CAMPAIGNS_DIR))
    parser.add_argument("--observations-dir", default=str(constants.OBSERVATIONS_DIR))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    scoring_rules = read_json(constants.CONFIG_DIR / "scoring_rules.json")

    observations = [
        Observation.model_validate(read_json(path))
        for path in list_json_files(Path(args.observations_dir))
    ]
    by_obs_id = {obs.observation_id: obs for obs in observations}

    count = 0
    for path in list_json_files(Path(args.campaigns_dir)):
        campaign = Campaign.model_validate(read_json(path))
        has_screenshot = False
        for ref in campaign.evidence_refs:
            obs = by_obs_id.get(ref)
            if not obs:
                continue
            if any(artifact.type.startswith("screenshot") for artifact in obs.artifacts):
                has_screenshot = True
                break
        _primary, _critic, final = validate_campaign_two_pass(
            campaign, scoring_rules, has_screenshot=has_screenshot,
        )
        write_model(path, final)
        count += 1

    print(f"Campanhas validadas: {count}")


if __name__ == "__main__":
    main()


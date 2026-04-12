from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import constants
from app.deduper import dedupe_campaigns
from app.io_utils import read_json, stamp_for_id
from app.models import Candidate, Observation
from app.orchestrator import extract_campaigns, generate_report, validate_campaigns


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reprocessa observacoes existentes para gerar campanhas/relatorio."
    )
    parser.add_argument("--job-id", default=f"backfill_{stamp_for_id()}")
    parser.add_argument("--observation-ids", nargs="+", required=True)
    return parser.parse_args()


def _load_observation(obs_id: str) -> Observation:
    path = constants.OBSERVATIONS_DIR / f"{obs_id}.json"
    return Observation.model_validate(read_json(path))


def _load_candidate(candidate_id: str) -> Candidate:
    path = constants.CANDIDATES_DIR / f"{candidate_id}.json"
    return Candidate.model_validate(read_json(path))


def main() -> None:
    args = parse_args()
    observations = [_load_observation(obs_id) for obs_id in args.observation_ids]

    candidate_ids = sorted({obs.candidate_id for obs in observations})
    candidates = [_load_candidate(candidate_id) for candidate_id in candidate_ids]

    scoring_rules = read_json(constants.CONFIG_DIR / "scoring_rules.json")
    report_settings = read_json(constants.CONFIG_DIR / "report_settings.json")

    extracted = extract_campaigns(args.job_id, candidates, observations)
    validated = validate_campaigns(args.job_id, extracted, observations, scoring_rules)
    unique_campaigns, _ = dedupe_campaigns(validated)
    report_paths = generate_report(args.job_id, unique_campaigns, report_settings)

    print(f"Backfill concluido: {args.job_id}")
    print(f"Observations: {len(observations)}")
    print(f"Campaigns: {len(unique_campaigns)}")
    for kind, path in report_paths.items():
        print(f"- {kind}: {path}")


if __name__ == "__main__":
    main()


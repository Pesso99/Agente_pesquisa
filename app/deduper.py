from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher

from app.constants import CAMPAIGNS_DIR
from app.io_utils import list_json_files, read_json
from app.models import Campaign


def _text_signature(campaign: Campaign) -> str:
    return " ".join(
        filter(
            None,
            [
                campaign.campaign_name.lower(),
                (campaign.benefit or "").lower(),
                campaign.source_url.lower(),
            ],
        )
    )


def campaign_similarity(a: Campaign, b: Campaign) -> float:
    if a.institution_id != b.institution_id:
        return 0.0
    text_a = _text_signature(a)
    text_b = _text_signature(b)
    return SequenceMatcher(None, text_a, text_b).ratio()


def dedupe_campaigns(
    campaigns: list[Campaign], threshold: float = 0.88
) -> tuple[list[Campaign], dict[str, list[str]]]:
    uniques: list[Campaign] = []
    groups: dict[str, list[str]] = defaultdict(list)

    for campaign in campaigns:
        matched_index = None
        for idx, unique in enumerate(uniques):
            if campaign_similarity(campaign, unique) >= threshold:
                matched_index = idx
                break

        if matched_index is None:
            uniques.append(campaign)
            groups[campaign.campaign_id].append(campaign.campaign_id)
            continue

        anchor = uniques[matched_index]
        groups[anchor.campaign_id].append(campaign.campaign_id)
        if campaign.confidence_final > anchor.confidence_final:
            uniques[matched_index] = campaign

    return uniques, dict(groups)


def load_campaigns_from_disk() -> list[Campaign]:
    """Carrega todos os JSON de campanha em data/campaigns."""
    return [Campaign.model_validate(read_json(path)) for path in list_json_files(CAMPAIGNS_DIR)]


def build_full_catalog_for_report(cycle_campaigns: list[Campaign]) -> tuple[list[Campaign], dict[str, list[str]]]:
    """Unifica o historico em disco com o ciclo atual; deduplica mantendo o ciclo primeiro."""
    cycle_ids = {c.campaign_id for c in cycle_campaigns}
    on_disk = load_campaigns_from_disk()
    rest = [c for c in on_disk if c.campaign_id not in cycle_ids]
    combined = list(cycle_campaigns) + rest
    return dedupe_campaigns(combined)


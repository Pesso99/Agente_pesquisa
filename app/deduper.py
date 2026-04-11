from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher

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


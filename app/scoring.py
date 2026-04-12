from __future__ import annotations

from app.models import Campaign


def clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def evaluate_campaign_score(
    campaign: Campaign,
    rules: dict,
    *,
    has_screenshot: bool,
) -> float:
    score = float(rules.get("base_score", 0.2))
    source_type = (campaign.source_type or "").lower()
    benefit_text = (campaign.benefit or "").strip()

    if source_type == "official_site":
        score += rules.get("official_source_bonus", 0.0)
    if source_type == "social_official":
        score += rules.get("official_social_bonus", 0.0)
    if source_type == "third_party":
        score -= rules.get("third_party_penalty", 0.0)

    if has_screenshot:
        score += rules.get("has_screenshot_bonus", 0.0)
    else:
        score -= rules.get("missing_visual_penalty", 0.0)

    if benefit_text:
        score += rules.get("clear_benefit_bonus", 0.0)
    else:
        score -= rules.get("unclear_benefit_penalty", 0.0)

    if campaign.end_date:
        score += rules.get("clear_deadline_bonus", 0.0)

    return clamp(score, rules.get("min_score", 0.0), rules.get("max_score", 1.0))


def classify_status(score: float, campaign: Campaign, *, has_screenshot: bool) -> str:
    source_type = (campaign.source_type or "").lower()
    benefit_clear = bool((campaign.benefit or "").strip())
    is_official = source_type in {"official_site", "social_official"}

    if score >= 0.85 and has_screenshot and is_official:
        return "validated"
    if score >= 0.7 and has_screenshot and benefit_clear:
        return "validated_with_reservations"
    # Fonte oficial com beneficio claro, mas sem print, deve ir para revisao e nao descarte.
    if (not has_screenshot) and is_official and benefit_clear:
        return "review"
    if score >= 0.45:
        return "review"
    return "discarded"


def validate_campaign(campaign: Campaign, rules: dict, *, has_screenshot: bool) -> Campaign:
    updated = campaign.model_copy(deep=True)
    score = evaluate_campaign_score(updated, rules, has_screenshot=has_screenshot)
    status = classify_status(score, updated, has_screenshot=has_screenshot)
    updated.confidence_final = score
    updated.status = status
    if status == "validated":
        updated.validation_notes = "Fonte e evidencia visual consistentes."
    elif status == "validated_with_reservations":
        updated.validation_notes = "Campanha forte, com pequenas lacunas de contexto."
    elif status == "review":
        updated.validation_notes = "Requer revisao manual por ambiguidade."
    else:
        updated.validation_notes = "Baixa confianca para publicacao."
    return updated

from __future__ import annotations

import re
from datetime import date

from dateutil import parser as dt_parser

from app.models import Campaign


_TYPE_KEYWORDS = {
    "cashback": "cashback",
    "cdb": "renda_fixa",
    "invest": "investimentos",
    "cartao": "cartao",
    "credito": "credito",
    "consorcio": "consorcio",
    "pix": "pix",
}


def slugify_text(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-{2,}", "-", cleaned)


def normalize_institution_id(value: str) -> str:
    return slugify_text(value).replace("-", "_")


def normalize_campaign_type(text: str | None) -> str:
    if not text:
        return "geral"
    lowered = text.lower()
    for token, normalized in _TYPE_KEYWORDS.items():
        if token in lowered:
            return normalized
    return "geral"


def normalize_date_text(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = dt_parser.parse(value, dayfirst=True, fuzzy=True)
    except (ValueError, TypeError, OverflowError):
        return None
    return parsed.date().isoformat()


def normalize_campaign(campaign: Campaign) -> Campaign:
    normalized = campaign.model_copy(deep=True)
    normalized.institution_id = normalize_institution_id(normalized.institution_id)
    normalized.campaign_type = normalize_campaign_type(
        f"{normalized.campaign_name} {normalized.campaign_type}"
    )
    normalized.start_date = normalize_date_text(normalized.start_date)
    normalized.end_date = normalize_date_text(normalized.end_date)
    return normalized


from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import Counter
from typing import Any

from app.models import Campaign
from app.runtime_db import RuntimeDB

logger = logging.getLogger(__name__)

_MIN_SAMPLES_FOR_PATTERN = 2


def _fold(text: str | None) -> str:
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", stripped.lower()).strip()


def _extract_keywords(name: str, benefit: str | None) -> list[str]:
    """Extract meaningful keywords from campaign name and benefit."""
    combined = _fold(f"{name} {benefit or ''}")
    tokens = re.findall(r"[a-z]{3,}", combined)
    stopwords = {
        "para", "com", "por", "uma", "que", "dos", "das", "nos", "nas",
        "seu", "sua", "seus", "suas", "mais", "como", "este", "esta",
        "esse", "essa", "novo", "nova", "voce", "cada", "toda", "todo",
        "sem", "nao", "sim", "ate", "sobre", "entre", "apos",
    }
    return [t for t in tokens if t not in stopwords]


def learn_from_feedback(db: RuntimeDB) -> dict[str, int]:
    """Analyze all feedback and recalculate learned_patterns. Returns pattern counts."""
    confirmed = db.get_confirmed_campaigns(limit=500)
    denied = db.get_denied_campaigns(limit=500)

    if not confirmed and not denied:
        logger.info("No feedback available to learn from.")
        return {"keyword_boost": 0, "source_trust": 0, "institution_signal": 0, "type_frequency": 0}

    db.clear_learned_patterns()
    counts: dict[str, int] = {}

    counts["keyword_boost"] = _learn_keyword_boost(db, confirmed, denied)
    counts["source_trust"] = _learn_source_trust(db, confirmed, denied)
    counts["institution_signal"] = _learn_institution_signal(db, confirmed, denied)
    counts["type_frequency"] = _learn_type_frequency(db, confirmed, denied)

    logger.info("Learned patterns: %s", counts)
    return counts


def _learn_keyword_boost(
    db: RuntimeDB,
    confirmed: list[dict[str, Any]],
    denied: list[dict[str, Any]],
) -> int:
    confirmed_kw: Counter[str] = Counter()
    denied_kw: Counter[str] = Counter()

    for camp in confirmed:
        keywords = _extract_keywords(camp["campaign_name"], camp.get("benefit"))
        confirmed_kw.update(keywords)

    for camp in denied:
        keywords = _extract_keywords(camp["campaign_name"], camp.get("benefit"))
        denied_kw.update(keywords)

    all_keywords = set(confirmed_kw.keys()) | set(denied_kw.keys())
    saved = 0

    for kw in all_keywords:
        c_count = confirmed_kw.get(kw, 0)
        d_count = denied_kw.get(kw, 0)
        total = c_count + d_count
        if total < _MIN_SAMPLES_FOR_PATTERN:
            continue
        # Score: +1.0 if always confirmed, -1.0 if always denied
        score = round((c_count - d_count) / total, 3)
        db.save_learned_pattern(
            pattern_type="keyword_boost",
            pattern_key=kw,
            pattern_value=score,
            sample_count=total,
        )
        saved += 1

    return saved


def _learn_source_trust(
    db: RuntimeDB,
    confirmed: list[dict[str, Any]],
    denied: list[dict[str, Any]],
) -> int:
    source_confirmed: Counter[str] = Counter()
    source_denied: Counter[str] = Counter()

    for camp in confirmed:
        st = camp.get("source_type") or "unknown"
        source_confirmed[st] += 1
    for camp in denied:
        st = camp.get("source_type") or "unknown"
        source_denied[st] += 1

    all_types = set(source_confirmed.keys()) | set(source_denied.keys())
    saved = 0

    for st in all_types:
        c = source_confirmed.get(st, 0)
        d = source_denied.get(st, 0)
        total = c + d
        if total < _MIN_SAMPLES_FOR_PATTERN:
            continue
        trust = round(c / total, 3)
        db.save_learned_pattern(
            pattern_type="source_trust",
            pattern_key=st,
            pattern_value=trust,
            sample_count=total,
        )
        saved += 1

    return saved


def _learn_institution_signal(
    db: RuntimeDB,
    confirmed: list[dict[str, Any]],
    denied: list[dict[str, Any]],
) -> int:
    inst_confirmed: Counter[str] = Counter()
    inst_denied: Counter[str] = Counter()

    for camp in confirmed:
        inst_confirmed[camp["institution_id"]] += 1
    for camp in denied:
        inst_denied[camp["institution_id"]] += 1

    all_inst = set(inst_confirmed.keys()) | set(inst_denied.keys())
    saved = 0

    for inst in all_inst:
        c = inst_confirmed.get(inst, 0)
        d = inst_denied.get(inst, 0)
        total = c + d
        if total < _MIN_SAMPLES_FOR_PATTERN:
            continue
        signal = round(c / total, 3)
        db.save_learned_pattern(
            pattern_type="institution_signal",
            pattern_key=inst,
            pattern_value=signal,
            sample_count=total,
        )
        saved += 1

    return saved


def _learn_type_frequency(
    db: RuntimeDB,
    confirmed: list[dict[str, Any]],
    denied: list[dict[str, Any]],
) -> int:
    type_confirmed: Counter[str] = Counter()
    type_denied: Counter[str] = Counter()

    for camp in confirmed:
        ct = camp.get("campaign_type") or "unknown"
        type_confirmed[ct] += 1
    for camp in denied:
        ct = camp.get("campaign_type") or "unknown"
        type_denied[ct] += 1

    all_types = set(type_confirmed.keys()) | set(type_denied.keys())
    saved = 0

    for ct in all_types:
        c = type_confirmed.get(ct, 0)
        d = type_denied.get(ct, 0)
        total = c + d
        if total < _MIN_SAMPLES_FOR_PATTERN:
            continue
        freq = round(c / total, 3)
        db.save_learned_pattern(
            pattern_type="type_frequency",
            pattern_key=ct,
            pattern_value=freq,
            sample_count=total,
        )
        saved += 1

    return saved


# --- Query helpers for pipeline integration ---


def get_discovery_boost(db: RuntimeDB, institution_id: str, source_type: str) -> float:
    """Return a confidence adjustment for candidate discovery based on historical patterns.

    Positive values boost confidence, negative values penalize.
    Range: approximately -0.15 to +0.15.
    """
    adjustment = 0.0

    inst_patterns = db.get_learned_patterns("institution_signal")
    inst_map = {p["pattern_key"]: p["pattern_value"] for p in inst_patterns}
    if institution_id in inst_map:
        # institution_signal is 0..1 (ratio of confirmed), center at 0.5
        adjustment += (inst_map[institution_id] - 0.5) * 0.2

    source_patterns = db.get_learned_patterns("source_trust")
    source_map = {p["pattern_key"]: p["pattern_value"] for p in source_patterns}
    if source_type in source_map:
        adjustment += (source_map[source_type] - 0.5) * 0.1

    return round(max(-0.15, min(0.15, adjustment)), 3)


def get_extraction_prior(db: RuntimeDB, institution_id: str, campaign_type: str | None) -> float:
    """Return a prior probability adjustment for extraction based on historical patterns.

    Positive means historically this type of campaign is more likely real.
    Range: approximately -0.1 to +0.1.
    """
    adjustment = 0.0

    if campaign_type:
        type_patterns = db.get_learned_patterns("type_frequency")
        type_map = {p["pattern_key"]: p["pattern_value"] for p in type_patterns}
        if campaign_type in type_map:
            adjustment += (type_map[campaign_type] - 0.5) * 0.15

    inst_patterns = db.get_learned_patterns("institution_signal")
    inst_map = {p["pattern_key"]: p["pattern_value"] for p in inst_patterns}
    if institution_id in inst_map:
        adjustment += (inst_map[institution_id] - 0.5) * 0.1

    return round(max(-0.1, min(0.1, adjustment)), 3)


def get_validation_adjustment(db: RuntimeDB, campaign: Campaign) -> float:
    """Return a score adjustment for validation based on historical feedback patterns.

    Range: approximately -0.12 to +0.12.
    """
    adjustment = 0.0
    weight_count = 0

    inst_patterns = db.get_learned_patterns("institution_signal")
    inst_map = {p["pattern_key"]: p["pattern_value"] for p in inst_patterns}
    if campaign.institution_id in inst_map:
        adjustment += (inst_map[campaign.institution_id] - 0.5) * 0.1
        weight_count += 1

    source_patterns = db.get_learned_patterns("source_trust")
    source_map = {p["pattern_key"]: p["pattern_value"] for p in source_patterns}
    src = campaign.source_type or "unknown"
    if src in source_map:
        adjustment += (source_map[src] - 0.5) * 0.08
        weight_count += 1

    if campaign.campaign_type:
        type_patterns = db.get_learned_patterns("type_frequency")
        type_map = {p["pattern_key"]: p["pattern_value"] for p in type_patterns}
        if campaign.campaign_type in type_map:
            adjustment += (type_map[campaign.campaign_type] - 0.5) * 0.08
            weight_count += 1

    kw_patterns = db.get_learned_patterns("keyword_boost")
    if kw_patterns:
        kw_map = {p["pattern_key"]: p["pattern_value"] for p in kw_patterns}
        keywords = _extract_keywords(campaign.campaign_name, campaign.benefit)
        kw_hits = [kw_map[kw] for kw in keywords if kw in kw_map]
        if kw_hits:
            avg_kw = sum(kw_hits) / len(kw_hits)
            adjustment += avg_kw * 0.06
            weight_count += 1

    return round(max(-0.12, min(0.12, adjustment)), 3)

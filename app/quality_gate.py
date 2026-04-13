from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app import constants
from app.models import Candidate, Observation, PageClassification

logger = logging.getLogger(__name__)

SOURCE_QUALITY_LABELS = (
    "campaign_like",
    "institutional",
    "login_wall",
    "error_page",
    "blank_or_broken",
)

BLOCKED_SOURCE_LABELS = {"login_wall", "error_page", "blank_or_broken"}

CAMPAIGN_PRIMARY_HINTS = (
    "promocao",
    "campanha",
    "cashback",
    "oferta",
    "desconto",
    "cupom",
    "ganhe",
    "participe",
    "isencao de anuidade",
    "anuidade gratis",
    "bonus",
)

CAMPAIGN_SECONDARY_HINTS = (
    "regulamento",
    "valida ate",
    "vigencia",
    "por tempo limitado",
    "clientes elegiveis",
    "acumule pontos",
    "milhas",
)

INSTITUTIONAL_HINTS = (
    "sobre",
    "carreiras",
    "imprensa",
    "investor relations",
    "sustentabilidade",
    "governanca",
    "nossa historia",
    "sistema de informacoes de credito",
    "politica de privacidade",
    "termos de uso",
    "ouvidoria",
    "fale conosco",
    "atendimento",
    "tarifas",
    "seguranca",
    "acessibilidade",
    "lgpd",
)

LOGIN_HINTS = (
    "login",
    "entrar",
    "sign in",
    "acessar conta",
    "faca login",
    "cadastre-se",
    "senha",
    "token",
    "internet banking",
)

ERROR_HINTS = (
    "404",
    "500",
    "not found",
    "pagina nao encontrada",
    "algo deu errado",
    "erro",
)


@dataclass
class QualityAssessment:
    source_quality_label: str
    capture_quality_score: float
    blocking_reasons: list[str]
    should_block: bool


def _fold_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_accents.lower()).strip()


def _normalized_text(*parts: str | None) -> str:
    return " ".join(_fold_text(part) for part in parts if part).strip()


def _classify_source_quality_deterministic(*, url: str, page_title: str | None, visible_claims: list[str], raw_text: str) -> str:
    """Deterministic fallback for source quality classification."""
    text = _normalized_text(url, page_title, " ".join(visible_claims), raw_text[:3000])
    if any(token in text for token in LOGIN_HINTS):
        return "login_wall"
    if any(token in text for token in ERROR_HINTS):
        return "error_page"
    if len(raw_text.strip()) < 80 and len(visible_claims) <= 1:
        return "blank_or_broken"

    primary_hits = sum(1 for token in CAMPAIGN_PRIMARY_HINTS if token in text)
    secondary_hits = sum(1 for token in CAMPAIGN_SECONDARY_HINTS if token in text)
    institutional_hits = sum(1 for token in INSTITUTIONAL_HINTS if token in text)
    has_temporal_marker = bool(re.search(r"\b(valida?|vigencia|ate|ate o dia|somente hoje)\b", text))
    has_value_marker = bool(re.search(r"(\d+\s*%\s*(do\s*cdi)?)|(r\$\s*\d+)", text))

    if primary_hits >= 2 and institutional_hits == 0:
        return "campaign_like"
    if primary_hits >= 1 and (secondary_hits >= 1 or has_temporal_marker or has_value_marker) and institutional_hits < 2:
        return "campaign_like"
    if institutional_hits >= 1:
        return "institutional"
    return "institutional"


def _classify_source_quality_llm(*, url: str, page_title: str | None, visible_claims: list[str], raw_text: str) -> str | None:
    """Classify page type using LLM. Returns None on failure."""
    try:
        from app.llm_client import AgentLLM

        llm = AgentLLM()
        claims_text = "\n".join(f"- {c}" for c in visible_claims[:10]) if visible_claims else "(nenhum)"
        prompt = (
            f"URL: {url}\n"
            f"Titulo: {page_title or '(sem titulo)'}\n"
            f"\nClaims visiveis:\n{claims_text}\n"
            f"\nTexto (ate 2000 chars):\n{raw_text[:2000]}\n"
        )
        result = llm.call("quality_gate", prompt, response_format=PageClassification)
        if isinstance(result, PageClassification) and result.label in SOURCE_QUALITY_LABELS:
            logger.info("LLM quality gate: %s (reason: %s)", result.label, result.reasoning)
            return result.label
        return None
    except Exception as exc:
        logger.warning("LLM quality gate failed: %s", exc)
        return None


def classify_source_quality(*, url: str, page_title: str | None, visible_claims: list[str], raw_text: str) -> str:
    """Classify source quality using LLM with deterministic fallback."""
    llm_label = _classify_source_quality_llm(
        url=url, page_title=page_title, visible_claims=visible_claims, raw_text=raw_text,
    )
    if llm_label is not None:
        return llm_label
    return _classify_source_quality_deterministic(
        url=url, page_title=page_title, visible_claims=visible_claims, raw_text=raw_text,
    )


def _visual_ratios(image_path: Path) -> tuple[float, float]:
    with Image.open(image_path) as image:
        grayscale = image.convert("L")
        histogram = grayscale.histogram()
        total = sum(histogram) or 1
        white_pixels = sum(histogram[245:256])
        dark_pixels = sum(histogram[0:80])
        return white_pixels / total, dark_pixels / total


def evaluate_screenshot_quality(
    screenshot_path: Path | None,
    *,
    visible_claims: list[str],
    raw_text: str,
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 1.0

    if screenshot_path is None or not screenshot_path.exists():
        reasons.append("missing_screenshot")
        score -= 0.6
    else:
        size = screenshot_path.stat().st_size
        if size < 8_000:
            reasons.append("screenshot_too_small")
            score -= 0.35

        try:
            with Image.open(screenshot_path) as image:
                width, height = image.size
                if width < 900 or height < 600:
                    reasons.append("screenshot_small_dimensions")
                    score -= 0.2
        except OSError:
            reasons.append("screenshot_unreadable")
            score -= 0.5

        try:
            white_ratio, dark_ratio = _visual_ratios(screenshot_path)
            if white_ratio > 0.985:
                reasons.append("screenshot_mostly_white")
                score -= 0.45
            if dark_ratio < 0.004:
                reasons.append("screenshot_low_text_contrast")
                score -= 0.25
        except OSError:
            if "screenshot_unreadable" not in reasons:
                reasons.append("screenshot_unreadable")
                score -= 0.5

    text_len = len(raw_text.strip())
    if text_len < 120 and len(visible_claims) < 2:
        reasons.append("low_visible_text")
        score -= 0.25

    score = max(0.0, min(1.0, score))
    return score, reasons


def _read_raw_text(obs: Observation) -> str:
    if not obs.raw_text_path:
        return ""
    path = constants.ROOT_DIR / obs.raw_text_path
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def assess_observation_quality(
    candidate: Candidate,
    observation: Observation,
    *,
    block_institutional: bool = True,
) -> QualityAssessment:
    raw_text = _read_raw_text(observation)
    source_quality_label = classify_source_quality(
        url=candidate.source_url,
        page_title=observation.page_title,
        visible_claims=observation.visible_claims,
        raw_text=raw_text,
    )

    screenshot_artifact = next(
        (artifact for artifact in observation.artifacts if artifact.type.startswith("screenshot")),
        None,
    )
    screenshot_path = (constants.ROOT_DIR / screenshot_artifact.path) if screenshot_artifact else None
    score, screenshot_reasons = evaluate_screenshot_quality(
        screenshot_path,
        visible_claims=observation.visible_claims,
        raw_text=raw_text,
    )

    blocked_labels = set(BLOCKED_SOURCE_LABELS)
    if block_institutional:
        blocked_labels.add("institutional")

    reasons = list(screenshot_reasons)
    if observation.instagram_block_reason:
        reasons.append(observation.instagram_block_reason)
        if "login" in _fold_text(observation.instagram_block_reason):
            source_quality_label = "login_wall"

    if source_quality_label in blocked_labels:
        reasons.append(f"blocked_source_label:{source_quality_label}")

    return QualityAssessment(
        source_quality_label=source_quality_label,
        capture_quality_score=score,
        blocking_reasons=reasons,
        should_block=source_quality_label in blocked_labels,
    )

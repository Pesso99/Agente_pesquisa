from __future__ import annotations

import logging
from pathlib import Path

from app.llm_client import AgentLLM
from app.models import Campaign, ScreenshotAnalysis, ValidationVerdict

logger = logging.getLogger(__name__)


def _build_validation_prompt(
    campaign: Campaign,
    *,
    has_screenshot: bool,
    screenshot_analysis: ScreenshotAnalysis | None = None,
) -> str:
    evidence = ", ".join(campaign.evidence_refs[:8]) if campaign.evidence_refs else "(nenhuma)"
    channels = ", ".join(campaign.channels) if campaign.channels else "(nenhum)"
    base = (
        f"Instituicao: {campaign.institution_id}\n"
        f"Nome da campanha: {campaign.campaign_name}\n"
        f"Tipo: {campaign.campaign_type}\n"
        f"URL fonte: {campaign.source_url}\n"
        f"Tipo de fonte: {campaign.source_type or 'desconhecido'}\n"
        f"Beneficio: {campaign.benefit or 'nao especificado'}\n"
        f"Publico: {campaign.audience or 'nao especificado'}\n"
        f"Data inicio: {campaign.start_date or 'nao informada'}\n"
        f"Data fim: {campaign.end_date or 'nao informada'}\n"
        f"URL regulamento: {campaign.regulation_url or 'nao informada'}\n"
        f"Tem screenshot: {'sim' if has_screenshot else 'nao'}\n"
        f"Evidencias: {evidence}\n"
        f"Canais: {channels}\n"
        f"Notas da extracao: {campaign.validation_notes or 'nenhuma'}\n"
    )
    if screenshot_analysis is not None:
        elements = ", ".join(screenshot_analysis.visual_elements_found) or "(nenhum)"
        base += (
            f"\n--- Analise visual do screenshot ---\n"
            f"Conteudo promocional detectado: {'sim' if screenshot_analysis.has_promotional_content else 'nao'}\n"
            f"Confianca visual: {screenshot_analysis.visual_confidence:.2f}\n"
            f"Tipo visual da pagina: {screenshot_analysis.page_type_visual}\n"
            f"Elementos visuais: {elements}\n"
            f"Observacao do analista visual: {screenshot_analysis.reasoning}\n"
        )
    return base


def _build_critic_debate_prompt(
    campaign: Campaign,
    *,
    has_screenshot: bool,
    primary_verdict: ValidationVerdict,
    screenshot_analysis: ScreenshotAnalysis | None = None,
) -> str:
    """Build the critic prompt including the primary verdict for debate."""
    base = _build_validation_prompt(
        campaign,
        has_screenshot=has_screenshot,
        screenshot_analysis=screenshot_analysis,
    )
    base += (
        f"\n--- Veredicto do validador primario ---\n"
        f"Status: {primary_verdict.status}\n"
        f"Confianca: {primary_verdict.confidence:.2f}\n"
        f"Raciocinio: {primary_verdict.reasoning}\n"
        f"Preocupacoes: {'; '.join(primary_verdict.concerns) if primary_verdict.concerns else 'nenhuma'}\n"
        f"\nAvalie criticamente este veredicto. Concorda? Discorda? Que riscos o primario ignorou?\n"
    )
    return base


def _llm_validate(
    llm: AgentLLM,
    agent_name: str,
    campaign: Campaign,
    *,
    has_screenshot: bool,
    screenshot_analysis: ScreenshotAnalysis | None = None,
) -> ValidationVerdict | None:
    try:
        prompt = _build_validation_prompt(
            campaign,
            has_screenshot=has_screenshot,
            screenshot_analysis=screenshot_analysis,
        )
        result = llm.call(agent_name, prompt, response_format=ValidationVerdict)
        return result  # type: ignore[return-value]
    except Exception as exc:
        logger.warning("LLM validate (%s) failed for %s: %s", agent_name, campaign.campaign_id, exc)
        return None


def _llm_validate_critic_debate(
    llm: AgentLLM,
    campaign: Campaign,
    *,
    has_screenshot: bool,
    primary_verdict: ValidationVerdict,
    screenshot_analysis: ScreenshotAnalysis | None = None,
) -> ValidationVerdict | None:
    """Call the critic agent with the primary verdict as debate context."""
    try:
        prompt = _build_critic_debate_prompt(
            campaign,
            has_screenshot=has_screenshot,
            primary_verdict=primary_verdict,
            screenshot_analysis=screenshot_analysis,
        )
        result = llm.call("validate_critic", prompt, response_format=ValidationVerdict)
        return result  # type: ignore[return-value]
    except Exception as exc:
        logger.warning("LLM validate_critic (debate) failed for %s: %s", campaign.campaign_id, exc)
        return None


def analyze_screenshot(
    llm: AgentLLM,
    screenshot_path: Path,
    campaign: Campaign,
) -> ScreenshotAnalysis | None:
    """Run vision analysis on a screenshot. Returns None on failure."""
    try:
        prompt = (
            f"Analise o screenshot desta pagina web.\n"
            f"Instituicao: {campaign.institution_id}\n"
            f"URL: {campaign.source_url}\n"
            f"Campanha alegada: {campaign.campaign_name}\n"
            f"Beneficio alegado: {campaign.benefit or 'nao especificado'}\n"
        )
        result = llm.call_with_image(
            "screenshot_analyst",
            prompt,
            screenshot_path,
            response_format=ScreenshotAnalysis,
        )
        return result  # type: ignore[return-value]
    except Exception as exc:
        logger.warning("Screenshot analysis failed for %s: %s", campaign.campaign_id, exc)
        return None


# --- Deterministic fallback (preserved from v1) ---


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
    if (not has_screenshot) and is_official and benefit_clear:
        return "review"
    if score >= 0.45:
        return "review"
    return "discarded"


def _fallback_validate(campaign: Campaign, rules: dict, *, has_screenshot: bool) -> Campaign:
    updated = campaign.model_copy(deep=True)
    score = evaluate_campaign_score(updated, rules, has_screenshot=has_screenshot)
    status = classify_status(score, updated, has_screenshot=has_screenshot)
    updated.confidence_final = score
    updated.status = status
    if status == "validated":
        updated.validation_notes = "Fonte e evidencia visual consistentes. (fallback deterministico)"
    elif status == "validated_with_reservations":
        updated.validation_notes = "Campanha forte, com pequenas lacunas de contexto. (fallback deterministico)"
    elif status == "review":
        updated.validation_notes = "Requer revisao manual por ambiguidade. (fallback deterministico)"
    else:
        updated.validation_notes = "Baixa confianca para publicacao. (fallback deterministico)"
    return updated


# --- Main two-pass validation with LLM ---


def _resolve_consensus(
    primary: ValidationVerdict,
    critic: ValidationVerdict,
) -> tuple[str, float, str]:
    """Deterministic consensus logic from two LLM verdicts."""
    if primary.status == critic.status:
        avg_conf = round((primary.confidence + critic.confidence) / 2, 3)
        notes = (
            f"Consenso entre validadores: {primary.status}. "
            f"Primary: {primary.reasoning} | "
            f"Critic: {critic.reasoning}"
        )
        return primary.status, avg_conf, notes

    # Divergence: take the more conservative status
    status_order = {"discarded": 0, "review": 1, "validated_with_reservations": 2, "validated": 3}
    primary_rank = status_order.get(primary.status, 1)
    critic_rank = status_order.get(critic.status, 1)

    if primary_rank <= critic_rank:
        conservative_status = primary.status
    else:
        conservative_status = critic.status

    if conservative_status in ("validated", "validated_with_reservations"):
        conservative_status = "review"

    avg_conf = round((primary.confidence + critic.confidence) / 2, 3)
    all_concerns = list(dict.fromkeys(primary.concerns + critic.concerns))
    notes = (
        f"Divergencia: primary={primary.status} (conf={primary.confidence:.2f}), "
        f"critic={critic.status} (conf={critic.confidence:.2f}). "
        f"Preocupacoes: {'; '.join(all_concerns) if all_concerns else 'nenhuma'}. "
        f"Primary: {primary.reasoning} | Critic: {critic.reasoning}"
    )
    return conservative_status, avg_conf, notes


def validate_campaign_two_pass(
    campaign: Campaign,
    rules: dict,
    *,
    has_screenshot: bool,
    llm: AgentLLM | None = None,
    screenshot_analysis: ScreenshotAnalysis | None = None,
) -> tuple[Campaign, Campaign, Campaign]:
    """Two-pass validation with debate: primary validates first, then critic
    receives the primary verdict as context. Screenshot analysis (if available)
    is shared with both agents."""

    primary_verdict: ValidationVerdict | None = None
    critic_verdict: ValidationVerdict | None = None

    if llm is not None:
        primary_verdict = _llm_validate(
            llm, "validate", campaign,
            has_screenshot=has_screenshot,
            screenshot_analysis=screenshot_analysis,
        )
        if primary_verdict is not None:
            critic_verdict = _llm_validate_critic_debate(
                llm, campaign,
                has_screenshot=has_screenshot,
                primary_verdict=primary_verdict,
                screenshot_analysis=screenshot_analysis,
            )
        else:
            critic_verdict = _llm_validate(
                llm, "validate_critic", campaign,
                has_screenshot=has_screenshot,
                screenshot_analysis=screenshot_analysis,
            )

    if primary_verdict is not None and critic_verdict is not None:
        final_status, final_conf, final_notes = _resolve_consensus(primary_verdict, critic_verdict)

        vision_tag = ""
        if screenshot_analysis is not None:
            vision_tag = (
                f" [Analise visual: {screenshot_analysis.page_type_visual}, "
                f"confianca={screenshot_analysis.visual_confidence:.2f}]"
            )

        primary_campaign = campaign.model_copy(deep=True)
        primary_campaign.status = primary_verdict.status
        primary_campaign.confidence_final = primary_verdict.confidence
        primary_campaign.validation_notes = primary_verdict.reasoning

        critic_campaign = campaign.model_copy(deep=True)
        critic_campaign.status = critic_verdict.status
        critic_campaign.confidence_final = critic_verdict.confidence
        critic_campaign.validation_notes = critic_verdict.reasoning

        final_campaign = campaign.model_copy(deep=True)
        final_campaign.status = final_status  # type: ignore[assignment]
        final_campaign.confidence_final = final_conf
        final_campaign.validation_notes = f"{final_notes}{vision_tag}"

        return primary_campaign, critic_campaign, final_campaign

    # Fallback: deterministic scoring (screenshot_analysis boosts score if available)
    logger.info("Using deterministic fallback for validation of %s", campaign.campaign_id)
    effective_has_screenshot = has_screenshot
    if screenshot_analysis is not None and screenshot_analysis.has_promotional_content:
        effective_has_screenshot = True

    primary_campaign = _fallback_validate(campaign, rules, has_screenshot=effective_has_screenshot)

    critic_rules = dict(rules)
    critic_rules["base_score"] = float(rules.get("base_score", 0.2)) - 0.08
    critic_rules["missing_visual_penalty"] = float(rules.get("missing_visual_penalty", 0.4)) + 0.1
    critic_rules["unclear_benefit_penalty"] = float(rules.get("unclear_benefit_penalty", 0.25)) + 0.05
    critic_campaign = _fallback_validate(campaign, critic_rules, has_screenshot=effective_has_screenshot)

    final_campaign = primary_campaign.model_copy(deep=True)
    if primary_campaign.status != critic_campaign.status:
        final_campaign.status = "review"
        final_campaign.confidence_final = round(
            (primary_campaign.confidence_final + critic_campaign.confidence_final) / 2, 3
        )
        final_campaign.validation_notes = (
            f"Divergencia entre validadores: primary={primary_campaign.status}, "
            f"critic={critic_campaign.status}. Encaminhada para revisao humana. (fallback deterministico)"
        )

    return primary_campaign, critic_campaign, final_campaign

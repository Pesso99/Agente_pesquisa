"""Tests for screenshot vision analysis and critic debate features."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.models import Campaign, ScreenshotAnalysis, ValidationVerdict
from app.scoring import (
    _build_critic_debate_prompt,
    _build_validation_prompt,
    validate_campaign_two_pass,
)


@pytest.fixture()
def sample_campaign() -> Campaign:
    return Campaign(
        campaign_id="camp_vis_001",
        institution_id="itau",
        campaign_name="Itau Cashback Debito",
        campaign_type="cashback",
        source_url="https://itau.com.br/promocoes",
        status="review",
        confidence_final=0.0,
        evidence_refs=["obs_test_001"],
        benefit="Cashback de 10% em compras no debito",
        source_type="official_site",
        end_date="30/06/2026",
    )


@pytest.fixture()
def vision_promo() -> ScreenshotAnalysis:
    return ScreenshotAnalysis(
        has_promotional_content=True,
        visual_confidence=0.88,
        visual_elements_found=["banner_promocional", "valor_desconto", "cta_button"],
        page_type_visual="promotional",
        reasoning="Banner grande com cashback 10% e botao 'participe agora'.",
    )


@pytest.fixture()
def vision_institutional() -> ScreenshotAnalysis:
    return ScreenshotAnalysis(
        has_promotional_content=False,
        visual_confidence=0.72,
        visual_elements_found=["logo_institucional"],
        page_type_visual="institutional",
        reasoning="Pagina com menu institucional, sem elementos promocionais.",
    )


class TestScreenshotAnalysisModel:
    def test_promo_screenshot(self, vision_promo: ScreenshotAnalysis) -> None:
        assert vision_promo.has_promotional_content is True
        assert vision_promo.page_type_visual == "promotional"
        assert len(vision_promo.visual_elements_found) == 3

    def test_institutional_screenshot(self, vision_institutional: ScreenshotAnalysis) -> None:
        assert vision_institutional.has_promotional_content is False
        assert vision_institutional.page_type_visual == "institutional"


class TestValidationPromptWithVision:
    def test_prompt_without_vision(self, sample_campaign: Campaign) -> None:
        prompt = _build_validation_prompt(sample_campaign, has_screenshot=True)
        assert "Analise visual do screenshot" not in prompt
        assert "Tem screenshot: sim" in prompt

    def test_prompt_with_vision(self, sample_campaign: Campaign, vision_promo: ScreenshotAnalysis) -> None:
        prompt = _build_validation_prompt(
            sample_campaign,
            has_screenshot=True,
            screenshot_analysis=vision_promo,
        )
        assert "Analise visual do screenshot" in prompt
        assert "Conteudo promocional detectado: sim" in prompt
        assert "promotional" in prompt
        assert "banner_promocional" in prompt


class TestCriticDebatePrompt:
    def test_debate_prompt_includes_primary_verdict(self, sample_campaign: Campaign) -> None:
        primary = ValidationVerdict(
            status="validated",
            confidence=0.88,
            reasoning="Campanha clara com evidencia forte.",
            concerns=["Sem regulamento formal"],
        )
        prompt = _build_critic_debate_prompt(
            sample_campaign,
            has_screenshot=True,
            primary_verdict=primary,
        )
        assert "Veredicto do validador primario" in prompt
        assert "validated" in prompt
        assert "0.88" in prompt
        assert "Campanha clara com evidencia forte." in prompt
        assert "Sem regulamento formal" in prompt
        assert "Avalie criticamente" in prompt

    def test_debate_prompt_includes_vision(
        self, sample_campaign: Campaign, vision_promo: ScreenshotAnalysis
    ) -> None:
        primary = ValidationVerdict(
            status="validated",
            confidence=0.85,
            reasoning="Evidencia visual e textual coerente.",
            concerns=[],
        )
        prompt = _build_critic_debate_prompt(
            sample_campaign,
            has_screenshot=True,
            primary_verdict=primary,
            screenshot_analysis=vision_promo,
        )
        assert "Analise visual do screenshot" in prompt
        assert "Veredicto do validador primario" in prompt


class TestTwoPassWithVision:
    def test_consensus_with_vision(
        self, sample_campaign: Campaign, vision_promo: ScreenshotAnalysis
    ) -> None:
        mock_primary = ValidationVerdict(
            status="validated",
            confidence=0.90,
            reasoning="Campanha com screenshot promocional confirmado.",
            concerns=[],
        )
        mock_critic = ValidationVerdict(
            status="validated",
            confidence=0.84,
            reasoning="Concordo, evidencia visual forte.",
            concerns=["Sem regulamento"],
        )
        mock_llm = MagicMock()
        mock_llm.call.side_effect = [mock_primary, mock_critic]

        primary, critic, final = validate_campaign_two_pass(
            sample_campaign, {},
            has_screenshot=True,
            llm=mock_llm,
            screenshot_analysis=vision_promo,
        )
        assert final.status == "validated"
        assert "Analise visual" in (final.validation_notes or "")
        assert "promotional" in (final.validation_notes or "")

    def test_vision_institutional_lowers_confidence(
        self, sample_campaign: Campaign, vision_institutional: ScreenshotAnalysis
    ) -> None:
        mock_primary = ValidationVerdict(
            status="validated_with_reservations",
            confidence=0.70,
            reasoning="Texto parece campanha mas visual e institucional.",
            concerns=["Visual contradiz texto"],
        )
        mock_critic = ValidationVerdict(
            status="review",
            confidence=0.40,
            reasoning="Screenshot mostra pagina institucional, nao campanha.",
            concerns=["Evidencia visual contradiz", "Sem banner"],
        )
        mock_llm = MagicMock()
        mock_llm.call.side_effect = [mock_primary, mock_critic]

        primary, critic, final = validate_campaign_two_pass(
            sample_campaign, {},
            has_screenshot=True,
            llm=mock_llm,
            screenshot_analysis=vision_institutional,
        )
        assert final.status == "review"
        assert "institutional" in (final.validation_notes or "")


class TestDebateFlow:
    def test_critic_receives_primary_verdict(self, sample_campaign: Campaign) -> None:
        """Verify that the critic call receives the primary verdict in the prompt."""
        mock_primary = ValidationVerdict(
            status="validated",
            confidence=0.88,
            reasoning="Campanha clara.",
            concerns=[],
        )
        mock_critic = ValidationVerdict(
            status="validated_with_reservations",
            confidence=0.75,
            reasoning="Concordo parcialmente, falta regulamento.",
            concerns=["Sem regulamento"],
        )
        mock_llm = MagicMock()
        mock_llm.call.side_effect = [mock_primary, mock_critic]

        validate_campaign_two_pass(
            sample_campaign, {},
            has_screenshot=True,
            llm=mock_llm,
        )

        assert mock_llm.call.call_count == 2
        first_call_args = mock_llm.call.call_args_list[0]
        assert first_call_args[0][0] == "validate"

        second_call_args = mock_llm.call.call_args_list[1]
        assert second_call_args[0][0] == "validate_critic"
        critic_prompt = second_call_args[0][1]
        assert "Veredicto do validador primario" in critic_prompt
        assert "validated" in critic_prompt
        assert "Campanha clara." in critic_prompt

    def test_critic_independent_when_primary_fails(self, sample_campaign: Campaign) -> None:
        """If primary LLM fails, critic should run independently."""
        mock_llm = MagicMock()
        mock_llm.call.side_effect = [
            RuntimeError("Primary LLM failed"),
            ValidationVerdict(
                status="review",
                confidence=0.50,
                reasoning="Avaliacao independente.",
                concerns=["Sem contexto do primario"],
            ),
        ]

        rules = {
            "base_score": 0.2,
            "official_source_bonus": 0.25,
            "has_screenshot_bonus": 0.2,
            "clear_benefit_bonus": 0.15,
            "clear_deadline_bonus": 0.1,
            "missing_visual_penalty": 0.4,
            "unclear_benefit_penalty": 0.25,
            "max_score": 1.0,
            "min_score": 0.0,
        }
        primary, critic, final = validate_campaign_two_pass(
            sample_campaign, rules,
            has_screenshot=True,
            llm=mock_llm,
        )
        assert final.status in {"validated", "validated_with_reservations", "review", "discarded"}
        assert 0.0 <= final.confidence_final <= 1.0

    def test_fallback_with_promo_vision_boosts(
        self, sample_campaign: Campaign, vision_promo: ScreenshotAnalysis
    ) -> None:
        """In deterministic fallback, promotional screenshot_analysis should help."""
        rules = {
            "base_score": 0.2,
            "official_source_bonus": 0.25,
            "has_screenshot_bonus": 0.2,
            "clear_benefit_bonus": 0.15,
            "clear_deadline_bonus": 0.1,
            "missing_visual_penalty": 0.4,
            "unclear_benefit_penalty": 0.25,
            "max_score": 1.0,
            "min_score": 0.0,
        }
        _, _, final_without = validate_campaign_two_pass(
            sample_campaign, rules,
            has_screenshot=False,
            llm=None,
            screenshot_analysis=None,
        )
        _, _, final_with = validate_campaign_two_pass(
            sample_campaign, rules,
            has_screenshot=False,
            llm=None,
            screenshot_analysis=vision_promo,
        )
        assert final_with.confidence_final >= final_without.confidence_final

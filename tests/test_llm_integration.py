"""Tests for LLM integration with mocked OpenAI calls."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.models import (
    Campaign,
    Candidate,
    ExtractionResult,
    Observation,
    PageClassification,
    ValidationVerdict,
)


@pytest.fixture()
def sample_candidate() -> Candidate:
    return Candidate(
        candidate_id="cand_test_001",
        institution_id="itau",
        source_type="official_site",
        source_url="https://itau.com.br/promocoes",
        headline="Itau cashback 10%",
        discovered_at="2026-04-10T07:05:00-03:00",
        confidence_initial=0.8,
    )


@pytest.fixture()
def sample_observation() -> Observation:
    return Observation(
        observation_id="obs_test_001",
        candidate_id="cand_test_001",
        captured_at="2026-04-10T07:10:00-03:00",
        source_url="https://itau.com.br/promocoes",
        page_title="Itau - Promocoes e Ofertas",
        visible_claims=["Cashback de 10% em compras no debito", "Valido ate 30/06/2026"],
        artifacts=[{"type": "screenshot_full", "path": "data/artifacts/screenshots/obs_test_001.png"}],
    )


@pytest.fixture()
def sample_campaign() -> Campaign:
    return Campaign(
        campaign_id="camp_test_001",
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


class TestExtractionResult:
    def test_campaign_detected(self) -> None:
        result = ExtractionResult(
            is_campaign=True,
            campaign_name="Itau Cashback 10%",
            campaign_type="cashback",
            benefit="Cashback de 10% em compras no debito",
            audience="clientes pessoa fisica",
            start_date=None,
            end_date="30/06/2026",
            regulation_url=None,
            confidence_reasoning="Pagina oficial com oferta clara de cashback e prazo definido.",
        )
        assert result.is_campaign is True
        assert result.campaign_type == "cashback"
        assert result.end_date == "30/06/2026"

    def test_not_campaign(self) -> None:
        result = ExtractionResult(
            is_campaign=False,
            confidence_reasoning="Pagina institucional sobre governanca corporativa.",
        )
        assert result.is_campaign is False
        assert result.campaign_name is None


class TestValidationVerdict:
    def test_validated(self) -> None:
        verdict = ValidationVerdict(
            status="validated",
            confidence=0.92,
            reasoning="Fonte oficial, screenshot com banner promocional, beneficio claro.",
            concerns=["Data de fim nao confirmada em regulamento"],
        )
        assert verdict.status == "validated"
        assert verdict.confidence == 0.92
        assert len(verdict.concerns) == 1

    def test_discarded(self) -> None:
        verdict = ValidationVerdict(
            status="discarded",
            confidence=0.15,
            reasoning="Pagina institucional sem oferta concreta.",
            concerns=["Sem beneficio", "Sem prazo", "Sem evidencia visual"],
        )
        assert verdict.status == "discarded"


class TestPageClassification:
    def test_campaign_like(self) -> None:
        result = PageClassification(
            label="campaign_like",
            reasoning="Pagina contem oferta promocional com cashback e prazo.",
        )
        assert result.label == "campaign_like"

    def test_institutional(self) -> None:
        result = PageClassification(
            label="institutional",
            reasoning="Pagina sobre governanca e sustentabilidade.",
        )
        assert result.label == "institutional"


class TestScoringWithLLM:
    def test_two_pass_consensus(self, sample_campaign: Campaign) -> None:
        mock_primary = ValidationVerdict(
            status="validated",
            confidence=0.88,
            reasoning="Campanha clara com evidencia.",
            concerns=[],
        )
        mock_critic = ValidationVerdict(
            status="validated",
            confidence=0.82,
            reasoning="Confirmado, campanha real.",
            concerns=["Sem regulamento formal"],
        )
        mock_llm = MagicMock()
        mock_llm.call.side_effect = [mock_primary, mock_critic]

        from app.scoring import validate_campaign_two_pass

        primary, critic, final = validate_campaign_two_pass(
            sample_campaign,
            {},
            has_screenshot=True,
            llm=mock_llm,
        )
        assert primary.status == "validated"
        assert critic.status == "validated"
        assert final.status == "validated"
        assert final.confidence_final == pytest.approx(0.85, abs=0.01)

    def test_two_pass_divergence(self, sample_campaign: Campaign) -> None:
        mock_primary = ValidationVerdict(
            status="validated",
            confidence=0.85,
            reasoning="Parece campanha valida.",
            concerns=[],
        )
        mock_critic = ValidationVerdict(
            status="review",
            confidence=0.45,
            reasoning="Beneficio vago, precisa de mais evidencia.",
            concerns=["Beneficio generico", "Sem regulamento"],
        )
        mock_llm = MagicMock()
        mock_llm.call.side_effect = [mock_primary, mock_critic]

        from app.scoring import validate_campaign_two_pass

        primary, critic, final = validate_campaign_two_pass(
            sample_campaign,
            {},
            has_screenshot=True,
            llm=mock_llm,
        )
        assert primary.status == "validated"
        assert critic.status == "review"
        assert final.status == "review"

    def test_fallback_without_llm(self, sample_campaign: Campaign) -> None:
        from app.scoring import validate_campaign_two_pass

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
            sample_campaign,
            rules,
            has_screenshot=True,
            llm=None,
        )
        assert final.status in {"validated", "validated_with_reservations", "review", "discarded"}
        assert 0.0 <= final.confidence_final <= 1.0


class TestQualityGateWithLLM:
    @patch("app.quality_gate._classify_source_quality_llm")
    def test_llm_classification_used(self, mock_llm_fn: MagicMock) -> None:
        mock_llm_fn.return_value = "campaign_like"
        from app.quality_gate import classify_source_quality

        result = classify_source_quality(
            url="https://itau.com.br/promocoes",
            page_title="Itau Promocoes",
            visible_claims=["Cashback de 10%"],
            raw_text="Aproveite cashback de 10% em compras no debito.",
        )
        assert result == "campaign_like"
        mock_llm_fn.assert_called_once()

    @patch("app.quality_gate._classify_source_quality_llm")
    def test_fallback_on_llm_failure(self, mock_llm_fn: MagicMock) -> None:
        mock_llm_fn.return_value = None
        from app.quality_gate import classify_source_quality

        result = classify_source_quality(
            url="https://itau.com.br/sobre",
            page_title="Sobre o Itau",
            visible_claims=["Governanca corporativa"],
            raw_text="O Itau Unibanco e uma das maiores instituicoes financeiras do Brasil. Carreiras. Sustentabilidade.",
        )
        assert result in {"campaign_like", "institutional", "login_wall", "error_page", "blank_or_broken"}
        mock_llm_fn.assert_called_once()


class TestLLMClient:
    def test_system_prompt_loading(self) -> None:
        from app.llm_client import _load_system_prompt

        prompt = _load_system_prompt("extract")
        assert "agente extrator" in prompt.lower()
        assert "is_campaign" in prompt

    def test_model_resolution(self) -> None:
        from app.llm_client import _resolve_model

        model = _resolve_model("extract")
        assert "gpt-5.4" in model

    def test_missing_prompt_raises(self) -> None:
        from app.llm_client import _load_system_prompt

        with pytest.raises(FileNotFoundError):
            _load_system_prompt("nonexistent_agent_xyz")

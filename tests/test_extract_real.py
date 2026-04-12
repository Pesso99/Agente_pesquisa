from app.models import Candidate, Observation
from app.quality_gate import QualityAssessment
from app.orchestrator import extract_campaigns


def test_extract_card_cashback_campaign() -> None:
    candidate = Candidate(
        candidate_id="cand_extract_1",
        institution_id="nubank",
        source_type="official_site",
        source_url="https://nubank.com.br/ultravioleta/cartao-black",
        headline="Cartao Ultravioleta com cashback",
        discovered_at="2026-04-10T09:00:00-03:00",
        confidence_initial=0.8,
    )
    observation = Observation(
        observation_id="obs_extract_1",
        candidate_id="cand_extract_1",
        captured_at="2026-04-10T09:01:00-03:00",
        source_url="https://nubank.com.br/ultravioleta/cartao-black",
        page_title="Cartao de credito Ultravioleta | Nubank",
        visible_claims=[
            "Campanha com cashback",
            "Regulamento e vigencia disponiveis",
        ],
        artifacts=[],
    )

    campaigns = extract_campaigns(
        "extract_test_job",
        [candidate],
        [observation],
        quality_by_obs={
            observation.observation_id: QualityAssessment(
                source_quality_label="campaign_like",
                capture_quality_score=0.9,
                blocking_reasons=[],
                should_block=False,
            )
        },
        runtime_db=None,
        trace_id="extract_test_trace",
    )
    assert len(campaigns) == 1
    assert campaigns[0].benefit is not None
    assert campaigns[0].campaign_type in {"cashback", "cartao", "investimentos", "geral"}


def test_extract_rejects_institutional_page() -> None:
    candidate = Candidate(
        candidate_id="cand_extract_2",
        institution_id="itau",
        source_type="official_site",
        source_url="https://www.itau.com.br/emprestimos-financiamentos/sistema-de-informacoes-de-credito",
        headline="Sistema de Informacoes de Credito",
        discovered_at="2026-04-10T09:00:00-03:00",
        confidence_initial=0.7,
    )
    observation = Observation(
        observation_id="obs_extract_2",
        candidate_id="cand_extract_2",
        captured_at="2026-04-10T09:01:00-03:00",
        source_url=candidate.source_url,
        page_title="Sistema de Informacoes de Credito - SCR | Itau",
        visible_claims=["Conteudo institucional"],
        artifacts=[],
    )

    campaigns = extract_campaigns(
        "extract_test_job_2",
        [candidate],
        [observation],
        quality_by_obs={
            observation.observation_id: QualityAssessment(
                source_quality_label="institutional",
                capture_quality_score=0.95,
                blocking_reasons=["blocked_source_label:institutional"],
                should_block=True,
            )
        },
        runtime_db=None,
        trace_id="extract_test_trace_2",
    )
    assert campaigns == []

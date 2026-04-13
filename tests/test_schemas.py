from app.models import Campaign, Candidate, Handoff, Observation, Report, ReportSection
from app.validators import validate_model_against_schema


def test_candidate_schema_validation() -> None:
    candidate = Candidate(
        candidate_id="cand_001",
        institution_id="itau",
        source_type="official_site",
        source_url="https://itau.com.br/promocoes",
        headline="Itau campanha promocional",
        discovered_at="2026-04-10T07:05:00-03:00",
        confidence_initial=0.8,
    )
    validate_model_against_schema(candidate)


def test_observation_schema_validation() -> None:
    observation = Observation(
        observation_id="obs_001",
        candidate_id="cand_001",
        captured_at="2026-04-10T07:10:00-03:00",
        source_url="https://itau.com.br/promocoes",
        visible_claims=["Oferta com cashback"],
        artifacts=[{"type": "screenshot_full", "path": "data/artifacts/screenshots/obs_001.png"}],
        instagram_modal_dismissed=None,
        instagram_block_reason=None,
    )
    validate_model_against_schema(observation)


def test_campaign_schema_validation() -> None:
    campaign = Campaign(
        campaign_id="camp_001",
        institution_id="itau",
        campaign_name="Itau cashback",
        campaign_type="cashback",
        source_url="https://itau.com.br/promocoes",
        status="validated",
        confidence_final=0.9,
        evidence_refs=["obs_001"],
    )
    validate_model_against_schema(campaign)


def test_report_schema_validation() -> None:
    report = Report(
        report_id="report_001",
        generated_at="2026-04-10T08:00:00-03:00",
        summary="Resumo curto.",
        sections=[ReportSection(title="Confirmadas", items=[{"campaign_id": "camp_001"}])],
    )
    validate_model_against_schema(report)


def test_handoff_schema_validation() -> None:
    handoff = Handoff(
        job_id="job_001",
        trace_id="job_001:trace",
        task="capture_candidate",
        source_agent="discover",
        target_agent="capture",
        input_refs=["data/candidates/cand_001.json"],
        created_at="2026-04-10T07:05:00-03:00",
        attempt=1,
        source_quality_label="campaign_like",
        capture_quality_score=0.8,
        blocking_reasons=[],
        instagram_modal_dismissed=True,
        instagram_block_reason=None,
    )
    validate_model_against_schema(handoff)

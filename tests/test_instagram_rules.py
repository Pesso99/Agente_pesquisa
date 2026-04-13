from __future__ import annotations

from app.models import Campaign, Observation
from app.orchestrator import validate_campaigns


def _obs_with_screenshot(observation_id: str, source_url: str) -> Observation:
    return Observation(
        observation_id=observation_id,
        candidate_id="cand_ig",
        captured_at="2026-04-11T10:00:00-03:00",
        source_url=source_url,
        page_title="Post instagram",
        visible_claims=["Campanha com cashback"],
        artifacts=[{"type": "screenshot_full", "path": f"data/artifacts/screenshots/{observation_id}.png"}],
    )


def test_instagram_without_official_confirmation_goes_to_review() -> None:
    campaign = Campaign(
        campaign_id="camp_inst_1",
        institution_id="btg",
        campaign_name="Post campanha",
        campaign_type="cashback",
        source_url="https://www.instagram.com/p/ABC123/",
        status="review",
        confidence_final=0.0,
        evidence_refs=["obs_inst_1"],
        source_type="social_official",
        benefit="Oferta com cashback",
        channels=["instagram_publico"],
        regulation_url=None,
    )
    rules = {
        "base_score": 0.5,
        "official_source_bonus": 0.3,
        "official_social_bonus": 0.3,
        "has_screenshot_bonus": 0.2,
        "clear_benefit_bonus": 0.2,
        "clear_deadline_bonus": 0.1,
        "third_party_penalty": 0.2,
        "missing_visual_penalty": 0.2,
        "unclear_benefit_penalty": 0.1,
        "max_score": 1.0,
        "min_score": 0.0,
    }

    out = validate_campaigns(
        "job_inst_review",
        [campaign],
        [_obs_with_screenshot("obs_inst_1", campaign.source_url)],
        rules,
        instagram_require_official_confirmation=True,
        runtime_db=None,
    )
    assert out[0].status == "review"
    assert "needs_official_confirmation" in (out[0].validation_notes or "")


def test_historical_seed_never_promoted_to_validated() -> None:
    campaign = Campaign(
        campaign_id="camp_hist_1",
        institution_id="btg",
        campaign_name="Campanha historica",
        campaign_type="cashback",
        source_url="https://cloud.btgpactual.com/campanha-historica",
        status="review",
        confidence_final=0.0,
        evidence_refs=["obs_hist_1"],
        source_type="official_site",
        benefit="Oferta com cashback",
        channels=["site_oficial", "historical_seed"],
    )
    rules = {
        "base_score": 0.7,
        "official_source_bonus": 0.2,
        "official_social_bonus": 0.1,
        "has_screenshot_bonus": 0.15,
        "clear_benefit_bonus": 0.15,
        "clear_deadline_bonus": 0.05,
        "third_party_penalty": 0.2,
        "missing_visual_penalty": 0.2,
        "unclear_benefit_penalty": 0.1,
        "max_score": 1.0,
        "min_score": 0.0,
    }

    out = validate_campaigns(
        "job_hist_review",
        [campaign],
        [_obs_with_screenshot("obs_hist_1", campaign.source_url)],
        rules,
        instagram_require_official_confirmation=True,
        runtime_db=None,
    )
    assert out[0].status == "review"
    notes = out[0].validation_notes or ""
    assert "historical_seed_requires_current_evidence" in notes or "review" in out[0].status

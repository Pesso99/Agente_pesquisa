from app.models import Campaign
from app.scoring import validate_campaign_two_pass


def test_validator_divergence_goes_to_review() -> None:
    campaign = Campaign(
        campaign_id="camp_x",
        institution_id="itau",
        campaign_name="Oferta oficial cartao",
        campaign_type="cartao",
        source_url="https://itau.com.br/cartao",
        status="review",
        confidence_final=0.0,
        evidence_refs=["obs_1"],
        source_type="official_site",
        benefit="Cashback em compras",
    )
    rules = {
        "base_score": 0.2,
        "official_source_bonus": 0.25,
        "has_screenshot_bonus": 0.2,
        "clear_benefit_bonus": 0.15,
        "clear_deadline_bonus": 0.1,
        "missing_visual_penalty": 0.4,
        "unclear_benefit_penalty": 0.25,
        "min_score": 0.0,
        "max_score": 1.0,
    }

    primary, critic, final = validate_campaign_two_pass(campaign, rules, has_screenshot=False)
    assert primary.status in {"review", "discarded", "validated_with_reservations", "validated"}
    if primary.status != critic.status:
        assert final.status == "review"


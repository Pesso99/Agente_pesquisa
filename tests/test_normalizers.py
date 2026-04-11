from app.models import Campaign
from app.normalizers import (
    normalize_campaign,
    normalize_campaign_type,
    normalize_date_text,
    normalize_institution_id,
)


def test_normalize_institution_id() -> None:
    assert normalize_institution_id("Banco Inter") == "banco_inter"


def test_normalize_campaign_type() -> None:
    assert normalize_campaign_type("Cashback em cartao premium") == "cashback"
    assert normalize_campaign_type("Campanha geral sem chave") == "geral"


def test_normalize_date_text() -> None:
    assert normalize_date_text("30/04/2026") == "2026-04-30"
    assert normalize_date_text("data invalida") is None


def test_normalize_campaign_object() -> None:
    campaign = Campaign(
        campaign_id="camp_1",
        institution_id="Banco Inter",
        campaign_name="CDB especial",
        campaign_type="promo",
        source_url="https://inter.co",
        status="review",
        confidence_final=0.5,
        evidence_refs=["obs_1"],
        start_date="01/04/2026",
        end_date="30/04/2026",
    )
    normalized = normalize_campaign(campaign)
    assert normalized.institution_id == "banco_inter"
    assert normalized.campaign_type == "renda_fixa"
    assert normalized.start_date == "2026-04-01"
    assert normalized.end_date == "2026-04-30"


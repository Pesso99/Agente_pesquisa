from app.deduper import build_full_catalog_for_report, dedupe_campaigns
from app.models import Campaign


def _campaign(
    campaign_id: str,
    name: str,
    benefit: str,
    confidence: float,
    institution: str = "itau",
) -> Campaign:
    return Campaign(
        campaign_id=campaign_id,
        institution_id=institution,
        campaign_name=name,
        campaign_type="geral",
        source_url="https://itau.com.br/promocao",
        status="review",
        confidence_final=confidence,
        evidence_refs=[f"obs_{campaign_id}"],
        benefit=benefit,
    )


def test_dedupe_groups_similar_campaigns() -> None:
    a = _campaign("camp_1", "Campanha cashback abril", "cashback 10%", 0.7)
    b = _campaign("camp_2", "Campanha cashback abril 2026", "cashback 10 por cento", 0.8)
    c = _campaign("camp_3", "CDB promocional", "CDB 120% CDI", 0.9)

    uniques, groups = dedupe_campaigns([a, b, c], threshold=0.8)
    assert len(uniques) == 2
    assert any("camp_1" in ids and "camp_2" in ids for ids in groups.values())


def test_dedupe_respects_institution() -> None:
    a = _campaign("camp_1", "Campanha cashback abril", "cashback 10%", 0.7, institution="itau")
    b = _campaign("camp_2", "Campanha cashback abril", "cashback 10%", 0.8, institution="nubank")

    uniques, _ = dedupe_campaigns([a, b], threshold=0.8)
    assert len(uniques) == 2


def test_build_full_catalog_prefers_cycle_order(monkeypatch) -> None:
    on_disk = [
        _campaign("old_1", "Campanha cashback abril", "cashback 10%", 0.5),
        _campaign("old_2", "Outra coisa", "beneficio x", 0.5),
    ]
    cycle = [_campaign("new_1", "Campanha cashback abril", "cashback 10%", 0.82)]

    monkeypatch.setattr("app.deduper.load_campaigns_from_disk", lambda: on_disk)

    merged, _ = build_full_catalog_for_report(cycle)
    assert len(merged) == 2
    similar = [c for c in merged if "cashback abril" in c.campaign_name]
    assert len(similar) == 1
    assert similar[0].campaign_id == "new_1"


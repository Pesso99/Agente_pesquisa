from __future__ import annotations

from app.orchestrator import discover_candidates


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text


def test_discover_uses_templates_and_historical_seeds(monkeypatch) -> None:
    institution = {
        "institution_id": "btg",
        "display_name": "BTG",
        "priority": 1,
        "official_domains": ["btgpactual.com"],
        "official_socials": {"instagram": "https://www.instagram.com/btgpactual/"},
        "search_terms": ["BTG promocao"],
        "source_templates": [
            {
                "template": "https://cloud.{domain}",
                "paths": ["/campanhas"],
                "source_type": "official_site",
                "confidence": 0.91,
            }
        ],
        "discovery_seeds": [
            {
                "url": "https://content.btgpactual.com/blog/investimentos/campanha-teste",
                "source_type": "official_site",
                "confidence": 0.9,
                "is_historical": True,
                "expected_label": "campaign_like",
            }
        ],
    }
    routing = {
        "discovery_to_capture_min_confidence": 0.6,
        "max_candidates_total": 6,
        "max_candidates_per_institution": 6,
        "include_instagram_sources": True,
    }

    def fake_safe_get(url: str):
        return _Resp("<html><title>Campanha BTG</title><h1>Promocao</h1></html>")

    monkeypatch.setattr("app.orchestrator._safe_get", fake_safe_get)

    candidates = discover_candidates(
        "discover_templates_test",
        [institution],
        routing,
        historical_seeds=[
            {
                "institution_id": "btg",
                "url": "https://cloud.btgpactual.com/campanha-historica",
                "source_type": "official_site",
                "is_historical": True,
                "expected_label": "campaign_like",
                "confidence": 0.95,
            }
        ],
        runtime_db=None,
    )

    urls = [cand.source_url for cand in candidates]
    assert any("cloud.btgpactual.com" in url for url in urls)
    assert any("content.btgpactual.com" in url for url in urls)
    assert any("instagram.com" in url for url in urls)
    assert any("is_historical=true" in (cand.notes or "") for cand in candidates)


def test_discover_guarantees_min_per_institution(monkeypatch) -> None:
    """All institutions get at least min_candidates_per_institution candidates."""

    def fake_safe_get(url: str):
        return _Resp("<html><title>Promo</title><h1>Promocao</h1></html>")

    monkeypatch.setattr("app.orchestrator._safe_get", fake_safe_get)

    institutions = [
        {
            "institution_id": f"inst_{i}",
            "display_name": f"Inst {i}",
            "priority": 1,
            "official_domains": [f"inst{i}.com.br"],
            "official_socials": {},
            "search_terms": [f"inst{i} promo"],
            "source_templates": [],
            "discovery_seeds": [],
        }
        for i in range(8)
    ]
    routing = {
        "discovery_to_capture_min_confidence": 0.6,
        "min_candidates_per_institution": 2,
        "max_candidates_per_institution": 5,
        "max_candidates_total": 20,
        "include_instagram_sources": False,
    }

    candidates = discover_candidates(
        "min_coverage_test",
        institutions,
        routing,
        runtime_db=None,
    )

    per_inst: dict[str, int] = {}
    for c in candidates:
        per_inst[c.institution_id] = per_inst.get(c.institution_id, 0) + 1

    assert len(per_inst) == 8, f"Expected 8 institutions, got {len(per_inst)}: {per_inst}"
    for iid, count in per_inst.items():
        assert count >= 2, f"{iid} has {count} candidates, expected >= 2"

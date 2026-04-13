import re
from pathlib import Path

from app.models import Campaign
from app.reporter import build_report, render_dashboard_html, render_html, render_markdown


def _campaign(status: str, score: float, suffix: str) -> Campaign:
    return Campaign(
        campaign_id=f"camp_{suffix}",
        institution_id="itau",
        campaign_name=f"Campanha {suffix}",
        campaign_type="cashback",
        source_url="https://itau.com.br/promocoes",
        status=status,  # type: ignore[arg-type]
        confidence_final=score,
        evidence_refs=[f"obs_{suffix}"],
        benefit="Cashback progressivo",
        validation_notes="ok",
    )


def test_report_generation_markdown_and_html() -> None:
    campaigns = [
        _campaign("validated", 0.92, "a"),
        _campaign("review", 0.6, "b"),
    ]
    settings = {
        "timezone": "America/Sao_Paulo",
        "max_items_per_section": 15,
        "include_review_section": True,
        "include_discarded_section": False,
    }
    report = build_report(campaigns, settings, report_id="report_test")
    markdown_text = render_markdown(report)
    html_text = render_html(markdown_text)

    assert "Relatorio completo" in markdown_text
    assert "Campanhas ativas confirmadas" in markdown_text
    assert "Panorama de beneficios" in markdown_text
    assert "Cashback" in markdown_text
    assert "<html" in html_text.lower()
    assert "Campanha a" in markdown_text


def test_report_includes_novidades_section() -> None:
    c1 = _campaign("validated", 0.92, "a")
    c2 = _campaign("review", 0.6, "b")
    settings = {
        "timezone": "America/Sao_Paulo",
        "max_items_per_section": 15,
        "include_review_section": True,
        "include_discarded_section": False,
    }
    report = build_report(
        [c1, c2],
        settings,
        report_id="report_test",
        new_cycle_campaign_ids={"camp_a"},
    )
    titles = [s.title for s in report.sections]
    assert "Novidades neste ciclo" in titles
    nov = next(s for s in report.sections if s.title == "Novidades neste ciclo")
    assert len(nov.items) == 1
    assert nov.items[0]["campaign_id"] == "camp_a"


def test_dashboard_does_not_duplicate_novidade_validated_campaign() -> None:
    c1 = _campaign("validated", 0.92, "a")
    c2 = _campaign("review", 0.6, "b")
    settings = {
        "timezone": "America/Sao_Paulo",
        "max_items_per_section": 15,
        "include_review_section": True,
        "include_discarded_section": False,
    }
    report = build_report(
        [c1, c2],
        settings,
        report_id="report_test",
        new_cycle_campaign_ids={"camp_a"},
    )
    html = render_dashboard_html(report, Path("."))
    assert html.count("Campanha a") == 1


def test_dashboard_metrics_include_novidades_without_duplicate_cards() -> None:
    c1 = _campaign("validated", 0.92, "a")
    c2 = _campaign("review", 0.6, "b")
    settings = {
        "timezone": "America/Sao_Paulo",
        "max_items_per_section": 15,
        "include_review_section": True,
        "include_discarded_section": False,
    }
    report = build_report(
        [c1, c2],
        settings,
        report_id="report_test",
        new_cycle_campaign_ids={"camp_a"},
    )
    html = render_dashboard_html(report, Path("."))
    assert html.count("Campanha a") == 1

    total_match = re.search(r"Total encontradas</div>\s*<div[^>]*>(\d+)</div>", html)
    active_match = re.search(r"Ativas confirmadas</div>\s*<div[^>]*>(\d+)</div>", html)
    assert total_match is not None
    assert active_match is not None
    assert total_match.group(1) == "2"
    assert active_match.group(1) == "1"


def test_report_uses_default_limit_when_max_items_is_zero() -> None:
    campaigns = [_campaign("review", 0.6, str(i)) for i in range(20)]
    settings = {
        "timezone": "America/Sao_Paulo",
        "max_items_per_section": 0,
        "include_review_section": True,
        "include_discarded_section": False,
    }
    report = build_report(campaigns, settings, report_id="report_test")
    review_section = next(s for s in report.sections if s.title == "Campanhas em revisao")
    assert len(review_section.items) == 15


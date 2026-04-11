from app.models import Campaign
from app.reporter import build_report, render_html, render_markdown


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

    assert "Monitor diario" in markdown_text
    assert "Novas campanhas confirmadas" in markdown_text
    assert "<html" in html_text.lower()
    assert "Campanha a" in markdown_text


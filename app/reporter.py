from __future__ import annotations

from pathlib import Path

import markdown as md

from app.io_utils import iso_now_tz, write_json
from app.models import Campaign, Report, ReportSection


def _campaign_item(campaign: Campaign) -> dict:
    return {
        "campaign_id": campaign.campaign_id,
        "institution_id": campaign.institution_id,
        "campaign_name": campaign.campaign_name,
        "campaign_type": campaign.campaign_type,
        "benefit": campaign.benefit,
        "status": campaign.status,
        "confidence_final": campaign.confidence_final,
        "source_url": campaign.source_url,
        "evidence_refs": campaign.evidence_refs,
        "validation_notes": campaign.validation_notes,
        "end_date": campaign.end_date,
        "channels": campaign.channels,
    }


def build_report(campaigns: list[Campaign], settings: dict, report_id: str) -> Report:
    validated = [c for c in campaigns if c.status in {"validated", "validated_with_reservations"}]
    review = [c for c in campaigns if c.status == "review"]
    discarded = [c for c in campaigns if c.status == "discarded"]

    sections: list[ReportSection] = [
        ReportSection(
            title="Novas campanhas confirmadas",
            items=[_campaign_item(c) for c in validated[: settings.get("max_items_per_section", 15)]],
        )
    ]

    if settings.get("include_review_section", True):
        sections.append(
            ReportSection(
                title="Campanhas em revisao",
                items=[_campaign_item(c) for c in review[: settings.get("max_items_per_section", 15)]],
            )
        )

    if settings.get("include_discarded_section", False):
        sections.append(
            ReportSection(
                title="Campanhas descartadas",
                items=[_campaign_item(c) for c in discarded[: settings.get("max_items_per_section", 15)]],
            )
        )

    summary = (
        f"Foram identificadas {len(campaigns)} campanhas relevantes, "
        f"com {len(validated)} confirmadas, {len(review)} em revisao e {len(discarded)} descartadas."
    )

    return Report(
        report_id=report_id,
        generated_at=iso_now_tz(settings.get("timezone", "America/Sao_Paulo")),
        summary=summary,
        sections=sections,
    )


def render_markdown(report: Report) -> str:
    lines = [
        "# Monitor diario - promocoes e campanhas financeiras",
        "",
        f"**Data:** {report.generated_at}",
        f"**Resumo executivo:** {report.summary}",
        "",
    ]

    for section in report.sections:
        lines.append(f"## {section.title}")
        lines.append("")
        if not section.items:
            lines.append("- Nenhum item nesta secao.")
            lines.append("")
            continue
        for item in section.items:
            lines.append(f"### {item['institution_id']} - {item['campaign_name']}")
            lines.append(f"- Beneficio: {item.get('benefit') or 'null'}")
            lines.append(f"- Tipo: {item['campaign_type']}")
            lines.append(f"- Status: {item['status']}")
            lines.append(f"- Score: {item['confidence_final']:.2f}")
            lines.append(f"- Fonte: {item['source_url']}")
            lines.append(f"- Evidencias: {', '.join(item.get('evidence_refs', [])) or 'sem evidencia'}")
            if item.get("validation_notes"):
                lines.append(f"- Notas: {item['validation_notes']}")
            lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_html(markdown_text: str) -> str:
    body = md.markdown(markdown_text, extensions=["tables"])
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Monitor diario</title>
  <style>
    body {{ font-family: 'Segoe UI', Tahoma, sans-serif; margin: 2rem; line-height: 1.5; color: #1f2937; }}
    h1, h2, h3 {{ color: #0f172a; }}
    h1 {{ border-bottom: 2px solid #cbd5e1; padding-bottom: 0.5rem; }}
    code {{ background: #e2e8f0; padding: 0 0.2rem; }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""


def save_report_files(report: Report, reports_dir: Path) -> dict[str, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    stem = report.report_id
    json_path = reports_dir / f"{stem}.json"
    md_path = reports_dir / f"{stem}.md"
    html_path = reports_dir / f"{stem}.html"

    markdown_text = render_markdown(report)
    html_text = render_html(markdown_text)

    write_json(json_path, report.model_dump(mode="json"))
    md_path.write_text(markdown_text, encoding="utf-8")
    html_path.write_text(html_text, encoding="utf-8")

    return {"json": json_path, "markdown": md_path, "html": html_path}


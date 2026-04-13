from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import markdown as md
from dateutil import parser as dt_parser

from app.io_utils import iso_now_tz, write_json
from app.models import Campaign, Report, ReportSection

if TYPE_CHECKING:
    from app.runtime_db import RuntimeDB

logger = logging.getLogger(__name__)

_BENEFIT_TYPE_LABELS: dict[str, str] = {
    "cashback": "Cashback",
    "renda_fixa": "Renda Fixa Promocional",
    "investimentos": "Investimentos",
    "cartao": "Cartao de Credito",
    "credito": "Credito",
    "consorcio": "Consorcio",
    "pix": "Pix",
    "geral": "Outros / Geral",
}


def _temporal_tag(campaign: Campaign) -> str:
    """Classify campaign temporal status based on end_date."""
    if not campaign.end_date:
        return "sem_prazo"
    try:
        end = dt_parser.parse(campaign.end_date, dayfirst=True, fuzzy=True).date()
    except (ValueError, TypeError, OverflowError):
        return "sem_prazo"
    today = date.today()
    if end < today:
        return "possivelmente_encerrada"
    if end <= today + timedelta(days=7):
        return "encerrando_em_breve"
    return "vigente"


_TEMPORAL_DISPLAY: dict[str, str] = {
    "vigente": "Vigente",
    "encerrando_em_breve": "Encerrando em breve",
    "possivelmente_encerrada": "Possivelmente encerrada",
    "sem_prazo": "Sem prazo definido",
}


def _extract_screenshot_path(evidence_refs: list[str]) -> str | None:
    for ref in evidence_refs:
        if ref.endswith(".png") and "screenshot" in ref:
            return ref
    return None


def _campaign_item(campaign: Campaign) -> dict:
    temporal = _temporal_tag(campaign)
    return {
        "campaign_id": campaign.campaign_id,
        "institution_id": campaign.institution_id,
        "campaign_name": campaign.campaign_name,
        "campaign_type": campaign.campaign_type,
        "benefit": campaign.benefit,
        "audience": campaign.audience,
        "status": campaign.status,
        "confidence_final": campaign.confidence_final,
        "source_url": campaign.source_url,
        "evidence_refs": campaign.evidence_refs,
        "screenshot_path": _extract_screenshot_path(campaign.evidence_refs),
        "validation_notes": campaign.validation_notes,
        "start_date": campaign.start_date,
        "end_date": campaign.end_date,
        "channels": campaign.channels,
        "temporal_tag": temporal,
        "temporal_display": _TEMPORAL_DISPLAY.get(temporal, temporal),
    }


def _build_benefit_panorama(campaigns: list[Campaign]) -> ReportSection:
    """Group campaigns by benefit type for a panoramic view."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for c in campaigns:
        if c.status == "discarded":
            continue
        ctype = c.campaign_type or "geral"
        groups[ctype].append({
            "institution_id": c.institution_id,
            "campaign_name": c.campaign_name,
            "benefit": c.benefit,
            "status": c.status,
            "temporal_tag": _temporal_tag(c),
            "temporal_display": _TEMPORAL_DISPLAY.get(_temporal_tag(c), ""),
        })
    items: list[dict] = []
    for ctype in sorted(groups, key=lambda k: -len(groups[k])):
        label = _BENEFIT_TYPE_LABELS.get(ctype, ctype.replace("_", " ").title())
        items.append({
            "benefit_type": ctype,
            "benefit_type_label": label,
            "count": len(groups[ctype]),
            "campaigns": groups[ctype],
        })
    return ReportSection(title="Panorama de beneficios", items=items)


def _generate_editorial_summary(campaigns: list[Campaign]) -> str | None:
    """Generate an editorial summary using the report LLM agent."""
    if not campaigns:
        return None
    try:
        from app.llm_client import AgentLLM

        llm = AgentLLM()
        validated = [c for c in campaigns if c.status in {"validated", "validated_with_reservations"}]
        review = [c for c in campaigns if c.status == "review"]
        discarded = [c for c in campaigns if c.status == "discarded"]

        benefit_counts: dict[str, int] = defaultdict(int)
        for c in campaigns:
            if c.status != "discarded":
                benefit_counts[c.campaign_type or "geral"] += 1

        benefit_summary = ", ".join(
            f"{_BENEFIT_TYPE_LABELS.get(k, k)}: {v}"
            for k, v in sorted(benefit_counts.items(), key=lambda x: -x[1])
        )

        lines = [
            f"Total de campanhas no ciclo: {len(campaigns)}",
            f"Confirmadas: {len(validated)} | Em revisao: {len(review)} | Descartadas: {len(discarded)}",
            f"Beneficios identificados: {benefit_summary or 'nenhum'}",
            "",
        ]
        for c in campaigns:
            temporal = _temporal_tag(c)
            lines.append(
                f"- [{c.status}] {c.institution_id}: {c.campaign_name} "
                f"(tipo={c.campaign_type}, beneficio={c.benefit or 'n/a'}, "
                f"vigencia={_TEMPORAL_DISPLAY.get(temporal, temporal)}, "
                f"score={c.confidence_final:.2f}, fonte={c.source_url})"
            )
            if c.validation_notes:
                lines.append(f"  Notas: {c.validation_notes}")

        prompt = "\n".join(lines)
        result = llm.call("report", prompt, max_tokens=300)
        if isinstance(result, str) and result.strip():
            logger.info("Editorial summary generated by LLM (%d chars)", len(result))
            return result.strip()
        return None
    except Exception as exc:
        logger.warning("LLM editorial summary failed: %s", exc)
        return None


def _build_historical_insights(
    campaigns: list[Campaign],
    db: RuntimeDB,
    *,
    cycle_campaigns: list[Campaign] | None = None,
) -> ReportSection | None:
    """Build a section with historical insights from feedback data."""
    stats = db.get_feedback_stats()
    if stats["total"] == 0:
        return None

    scope = cycle_campaigns if cycle_campaigns is not None else campaigns
    history_matches = sum(1 for c in scope if c.history_match_id)
    patterns = db.get_learned_patterns()
    top_patterns: list[dict] = []
    for p in patterns[:12]:
        top_patterns.append({
            "type": p["pattern_type"],
            "key": p["pattern_key"],
            "value": round(p["pattern_value"], 3),
            "samples": p["sample_count"],
        })

    items: list[dict] = [{
        "feedback_total": stats["total"],
        "confirmed": stats["confirmed"],
        "denied": stats["denied"],
        "uncertain": stats["uncertain"],
        "accuracy": stats.get("accuracy"),
        "history_matches_in_cycle": history_matches,
        "top_patterns": top_patterns,
    }]

    return ReportSection(title="Insights historicos", items=items)


def build_report(
    campaigns: list[Campaign],
    settings: dict,
    report_id: str,
    *,
    runtime_db: RuntimeDB | None = None,
    new_cycle_campaign_ids: set[str] | None = None,
    cycle_campaigns_for_insights: list[Campaign] | None = None,
) -> Report:
    validated = [c for c in campaigns if c.status in {"validated", "validated_with_reservations"}]
    review = [c for c in campaigns if c.status == "review"]
    discarded = [c for c in campaigns if c.status == "discarded"]
    max_items = settings.get("max_items_per_section", 15)
    if max_items is None or int(max_items) <= 0:
        max_items = 15

    sections: list[ReportSection] = []

    if new_cycle_campaign_ids:
        new_items = [_campaign_item(c) for c in campaigns if c.campaign_id in new_cycle_campaign_ids]
        if new_items:
            sections.append(ReportSection(title="Novidades neste ciclo", items=new_items))

    non_discarded = [c for c in campaigns if c.status != "discarded"]
    if non_discarded:
        sections.append(_build_benefit_panorama(campaigns))

    sections.append(
        ReportSection(
            title="Campanhas ativas confirmadas",
            items=[_campaign_item(c) for c in validated[:max_items]],
        )
    )

    if settings.get("include_review_section", True):
        sections.append(
            ReportSection(
                title="Campanhas em revisao",
                items=[_campaign_item(c) for c in review[:max_items]],
            )
        )

    if settings.get("include_discarded_section", False):
        sections.append(
            ReportSection(
                title="Campanhas descartadas",
                items=[_campaign_item(c) for c in discarded[:max_items]],
            )
        )

    if runtime_db:
        insights = _build_historical_insights(
            campaigns, runtime_db, cycle_campaigns=cycle_campaigns_for_insights
        )
        if insights:
            sections.append(insights)

    n_new = (
        sum(1 for c in campaigns if new_cycle_campaign_ids and c.campaign_id in new_cycle_campaign_ids)
        if new_cycle_campaign_ids
        else 0
    )
    fallback_summary = (
        f"Catalogo completo (deduplicado): {len(campaigns)} campanhas"
        + (f", sendo {n_new} novidade(s) neste ciclo" if n_new else "")
        + ". "
        f"Distribuicao: {len(validated)} confirmadas, {len(review)} em revisao e {len(discarded)} descartadas."
    )

    summary = _generate_editorial_summary(campaigns) or fallback_summary

    return Report(
        report_id=report_id,
        generated_at=iso_now_tz(settings.get("timezone", "America/Sao_Paulo")),
        summary=summary,
        sections=sections,
    )


def _render_panorama_section(section: ReportSection) -> list[str]:
    """Render the benefit panorama section."""
    lines = [f"## {section.title}", ""]
    if not section.items:
        lines += ["- Nenhum beneficio identificado.", ""]
        return lines
    for group in section.items:
        label = group.get("benefit_type_label", "?")
        count = group.get("count", 0)
        lines.append(f"### {label} ({count} campanha{'s' if count != 1 else ''})")
        lines.append("")
        for c in group.get("campaigns", []):
            temporal = c.get("temporal_display", "")
            status_icon = "V" if c["status"] == "validated" else "?" if c["status"] == "review" else "~"
            benefit_text = c.get("benefit") or "beneficio nao detalhado"
            lines.append(
                f"- [{status_icon}] **{c['institution_id']}** - {c['campaign_name']} "
                f"| {benefit_text} | _{temporal}_"
            )
        lines.append("")
    return lines


def _render_campaign_section(section: ReportSection) -> list[str]:
    """Render a standard campaign section (confirmadas, revisao, descartadas)."""
    lines = [f"## {section.title}", ""]
    if not section.items:
        lines += ["- Nenhum item nesta secao.", ""]
        return lines
    for item in section.items:
        temporal = item.get("temporal_display", "")
        temporal_suffix = f" | _{temporal}_" if temporal else ""
        lines.append(f"### {item['institution_id']} - {item['campaign_name']}{temporal_suffix}")
        lines.append(f"- Beneficio: {item.get('benefit') or 'nao especificado'}")
        lines.append(f"- Tipo: {item['campaign_type']}")
        lines.append(f"- Status: {item['status']}")
        lines.append(f"- Score: {item['confidence_final']:.2f}")
        if item.get("start_date") or item.get("end_date"):
            lines.append(f"- Vigencia: {item.get('start_date') or '?'} a {item.get('end_date') or '?'}")
        lines.append(f"- Fonte: {item['source_url']}")
        lines.append(f"- Evidencias: {', '.join(item.get('evidence_refs', [])) or 'sem evidencia'}")
        if item.get("validation_notes"):
            lines.append(f"- Notas: {item['validation_notes']}")
        lines.append("")
    return lines


def _render_insights_section(section: ReportSection) -> list[str]:
    """Render historical insights section in markdown."""
    lines = [f"## {section.title}", ""]
    if not section.items:
        lines += ["- Sem dados historicos disponiveis.", ""]
        return lines
    data = section.items[0]
    lines.append(f"- **Feedbacks registrados:** {data.get('feedback_total', 0)}")
    lines.append(f"  - Confirmadas: {data.get('confirmed', 0)}")
    lines.append(f"  - Negadas: {data.get('denied', 0)}")
    lines.append(f"  - Incertas: {data.get('uncertain', 0)}")
    accuracy = data.get("accuracy")
    if accuracy is not None:
        lines.append(f"- **Taxa de acerto do pipeline:** {accuracy:.1%}")
    lines.append(f"- **Campanhas com match historico neste ciclo:** {data.get('history_matches_in_cycle', 0)}")
    patterns = data.get("top_patterns", [])
    if patterns:
        lines.append("")
        lines.append("### Padroes aprendidos (top)")
        for p in patterns:
            direction = "+" if p["value"] > 0 else ""
            lines.append(f"- `{p['type']}` **{p['key']}**: {direction}{p['value']:.3f} (n={p['samples']})")
    lines.append("")
    return lines


def render_markdown(report: Report) -> str:
    lines = [
        "# Relatorio completo - promocoes e campanhas financeiras",
        "",
        f"**Data:** {report.generated_at}",
        f"**Resumo executivo:** {report.summary}",
        "",
    ]

    for section in report.sections:
        if section.title == "Panorama de beneficios":
            lines += _render_panorama_section(section)
        elif section.title == "Insights historicos":
            lines += _render_insights_section(section)
        else:
            lines += _render_campaign_section(section)

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


_STATUS_LABELS: dict[str, tuple[str, str]] = {
    "validated": ("Confirmada", "#16a34a"),
    "validated_with_reservations": ("Confirmada c/ reservas", "#ca8a04"),
    "review": ("Em revisao", "#2563eb"),
    "discarded": ("Descartada", "#dc2626"),
}


def _html_escape(text: str | None) -> str:
    if not text:
        return ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _score_bar_html(score: float) -> str:
    pct = int(score * 100)
    color = "#16a34a" if score >= 0.85 else "#ca8a04" if score >= 0.7 else "#dc2626"
    return (
        f'<div style="display:flex;align-items:center;gap:6px">'
        f'<div style="flex:1;height:8px;background:#e5e7eb;border-radius:4px;max-width:120px">'
        f'<div style="width:{pct}%;height:100%;background:{color};border-radius:4px"></div>'
        f'</div>'
        f'<span style="font-size:0.85rem;font-weight:600;color:{color}">{score:.0%}</span>'
        f'</div>'
    )


def _build_institution_table(report: Report, all_institutions: list[str]) -> str:
    inst_data: dict[str, dict[str, int]] = {i: {"ativas": 0, "revisao": 0, "descartadas": 0, "tipos": set()} for i in all_institutions}
    for section in report.sections:
        for item in section.items:
            iid = item.get("institution_id")
            if not iid or iid not in inst_data:
                if iid:
                    inst_data[iid] = {"ativas": 0, "revisao": 0, "descartadas": 0, "tipos": set()}
                else:
                    continue
            status = item.get("status", "")
            if status in ("validated", "validated_with_reservations"):
                inst_data[iid]["ativas"] += 1
            elif status == "review":
                inst_data[iid]["revisao"] += 1
            elif status == "discarded":
                inst_data[iid]["descartadas"] += 1
            ctype = item.get("campaign_type")
            if ctype:
                inst_data[iid]["tipos"].add(_BENEFIT_TYPE_LABELS.get(ctype, ctype))
    rows = []
    for iid in sorted(inst_data, key=lambda k: -inst_data[k]["ativas"]):
        d = inst_data[iid]
        tipos_str = ", ".join(sorted(d["tipos"])) if d["tipos"] else "-"
        total = d["ativas"] + d["revisao"]
        row_bg = "" if total > 0 else ' style="color:#9ca3af"'
        rows.append(
            f"<tr{row_bg}>"
            f"<td style='font-weight:600'>{_html_escape(iid.title())}</td>"
            f"<td style='text-align:center'>{d['ativas']}</td>"
            f"<td style='text-align:center'>{d['revisao']}</td>"
            f"<td style='text-align:center'>{d['descartadas']}</td>"
            f"<td>{_html_escape(tipos_str)}</td>"
            f"</tr>"
        )
    return (
        "<table style='width:100%;border-collapse:collapse;margin:1rem 0'>"
        "<thead><tr style='border-bottom:2px solid #cbd5e1;text-align:left'>"
        "<th style='padding:8px'>Instituicao</th>"
        "<th style='padding:8px;text-align:center'>Ativas</th>"
        "<th style='padding:8px;text-align:center'>Em revisao</th>"
        "<th style='padding:8px;text-align:center'>Descartadas</th>"
        "<th style='padding:8px'>Tipos de beneficio</th>"
        "</tr></thead><tbody>"
        + "\n".join(rows)
        + "</tbody></table>"
    )


def _build_campaign_card(item: dict, reports_dir: Path) -> str:
    status_label, status_color = _STATUS_LABELS.get(
        item.get("status", ""), ("?", "#6b7280")
    )
    benefit = _html_escape(item.get("benefit") or "Nao especificado")
    audience = _html_escape(item.get("audience") or "Nao especificado")
    temporal = _html_escape(item.get("temporal_display") or "")
    ctype_raw = item.get("campaign_type", "geral")
    ctype = _html_escape(_BENEFIT_TYPE_LABELS.get(ctype_raw, ctype_raw))
    source_url = _html_escape(item.get("source_url", ""))
    vigencia_parts = []
    if item.get("start_date"):
        vigencia_parts.append(f"Inicio: {_html_escape(item['start_date'])}")
    if item.get("end_date"):
        vigencia_parts.append(f"Fim: {_html_escape(item['end_date'])}")
    if not vigencia_parts:
        vigencia_parts.append("Sem datas definidas")
    vigencia = " &mdash; ".join(vigencia_parts)

    screenshot_html = ""
    screenshot_path = item.get("screenshot_path")
    if screenshot_path:
        rel = Path(screenshot_path)
        abs_path = Path("data") / "artifacts" / "screenshots" / rel.name if "screenshots" not in str(rel.parent) else rel
        img_src = f"../../{abs_path.as_posix()}"
        screenshot_html = (
            f'<div style="margin-top:12px;border:1px solid #e5e7eb;border-radius:6px;overflow:hidden">'
            f'<img src="{img_src}" alt="Screenshot" style="width:100%;max-height:300px;object-fit:cover;display:block">'
            f'</div>'
        )

    return f"""
    <div style="border:1px solid #e2e8f0;border-radius:10px;padding:20px;margin-bottom:16px;background:#fff;
                box-shadow:0 1px 3px rgba(0,0,0,0.06)">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px">
        <div>
          <span style="font-size:0.75rem;font-weight:700;text-transform:uppercase;color:#64748b">
            {_html_escape(item.get('institution_id', '').upper())}
          </span>
          <h3 style="margin:4px 0 0;font-size:1.1rem;color:#0f172a">
            {_html_escape(item.get('campaign_name', ''))}
          </h3>
        </div>
        <span style="font-size:0.75rem;font-weight:600;padding:3px 10px;border-radius:12px;
                      background:{status_color}18;color:{status_color};white-space:nowrap">
          {status_label}
        </span>
      </div>

      <table style="width:100%;font-size:0.9rem;border-collapse:collapse">
        <tr>
          <td style="padding:4px 12px 4px 0;color:#64748b;white-space:nowrap;vertical-align:top">Beneficio</td>
          <td style="padding:4px 0;font-weight:500">{benefit}</td>
        </tr>
        <tr>
          <td style="padding:4px 12px 4px 0;color:#64748b;white-space:nowrap;vertical-align:top">Publico-alvo</td>
          <td style="padding:4px 0">{audience}</td>
        </tr>
        <tr>
          <td style="padding:4px 12px 4px 0;color:#64748b;white-space:nowrap">Tipo</td>
          <td style="padding:4px 0">{ctype}</td>
        </tr>
        <tr>
          <td style="padding:4px 12px 4px 0;color:#64748b;white-space:nowrap">Vigencia</td>
          <td style="padding:4px 0">{vigencia} &nbsp;<em style="color:#64748b">({temporal})</em></td>
        </tr>
        <tr>
          <td style="padding:4px 12px 4px 0;color:#64748b;white-space:nowrap">Confianca</td>
          <td style="padding:4px 0">{_score_bar_html(item.get('confidence_final', 0))}</td>
        </tr>
        <tr>
          <td style="padding:4px 12px 4px 0;color:#64748b;white-space:nowrap">Fonte</td>
          <td style="padding:4px 0"><a href="{source_url}" target="_blank" style="color:#2563eb;text-decoration:none">{source_url}</a></td>
        </tr>
      </table>
      {screenshot_html}
    </div>"""


def _build_insights_html(section: ReportSection) -> str:
    """Render historical insights as an HTML block."""
    if not section.items:
        return '<p class="empty">Sem dados historicos disponiveis.</p>'
    data = section.items[0]
    accuracy = data.get("accuracy")
    accuracy_text = f"{accuracy:.1%}" if accuracy is not None else "N/A"
    patterns = data.get("top_patterns", [])

    patterns_rows = ""
    for p in patterns:
        direction = "+" if p["value"] > 0 else ""
        color = "#16a34a" if p["value"] > 0.1 else "#dc2626" if p["value"] < -0.1 else "#64748b"
        patterns_rows += (
            f'<tr><td style="padding:4px 8px;color:#64748b">{_html_escape(p["type"])}</td>'
            f'<td style="padding:4px 8px;font-weight:600">{_html_escape(p["key"])}</td>'
            f'<td style="padding:4px 8px;color:{color};font-weight:600">{direction}{p["value"]:.3f}</td>'
            f'<td style="padding:4px 8px;color:#64748b">n={p["samples"]}</td></tr>'
        )

    patterns_table = ""
    if patterns_rows:
        patterns_table = (
            '<table style="width:100%;border-collapse:collapse;margin-top:12px;font-size:0.85rem">'
            '<thead><tr style="border-bottom:1px solid #e2e8f0;text-align:left">'
            '<th style="padding:4px 8px">Tipo</th><th style="padding:4px 8px">Chave</th>'
            '<th style="padding:4px 8px">Valor</th><th style="padding:4px 8px">Amostras</th>'
            '</tr></thead><tbody>' + patterns_rows + '</tbody></table>'
        )

    return f"""
    <div style="border:1px solid #e2e8f0;border-radius:10px;padding:20px;margin-bottom:16px;background:#fff;
                box-shadow:0 1px 3px rgba(0,0,0,0.06)">
      <div style="display:flex;gap:24px;flex-wrap:wrap;margin-bottom:12px">
        <div><span style="color:#64748b;font-size:0.8rem">Feedbacks</span>
             <div style="font-size:1.3rem;font-weight:700">{data.get('feedback_total', 0)}</div></div>
        <div><span style="color:#64748b;font-size:0.8rem">Confirmadas</span>
             <div style="font-size:1.3rem;font-weight:700;color:#16a34a">{data.get('confirmed', 0)}</div></div>
        <div><span style="color:#64748b;font-size:0.8rem">Negadas</span>
             <div style="font-size:1.3rem;font-weight:700;color:#dc2626">{data.get('denied', 0)}</div></div>
        <div><span style="color:#64748b;font-size:0.8rem">Taxa de acerto</span>
             <div style="font-size:1.3rem;font-weight:700;color:#2563eb">{accuracy_text}</div></div>
        <div><span style="color:#64748b;font-size:0.8rem">Matches historicos</span>
             <div style="font-size:1.3rem;font-weight:700;color:#7c3aed">{data.get('history_matches_in_cycle', 0)}</div></div>
      </div>
      {patterns_table}
    </div>"""


def render_dashboard_html(report: Report, reports_dir: Path) -> str:
    all_items: list[dict] = []
    insights_section: ReportSection | None = None
    novidades_items: list[dict] = []
    for section in report.sections:
        if section.title == "Panorama de beneficios":
            continue
        if section.title == "Insights historicos":
            insights_section = section
            continue
        if section.title == "Novidades neste ciclo":
            novidades_items = list(section.items)
            continue
        all_items.extend(section.items)

    novidade_ids = {
        str(item.get("campaign_id"))
        for item in novidades_items
        if item.get("campaign_id")
    }
    non_novidade_items = [
        item for item in all_items
        if str(item.get("campaign_id")) not in novidade_ids
    ]

    metric_items_by_id: dict[str, dict] = {}
    metric_items_fallback: list[dict] = []
    for item in non_novidade_items + novidades_items:
        campaign_id = item.get("campaign_id")
        if campaign_id:
            metric_items_by_id.setdefault(str(campaign_id), item)
        else:
            metric_items_fallback.append(item)
    metric_items = list(metric_items_by_id.values()) + metric_items_fallback

    validated = [i for i in non_novidade_items if i.get("status") in ("validated", "validated_with_reservations")]
    review_items = [i for i in non_novidade_items if i.get("status") == "review"]
    discarded = [i for i in non_novidade_items if i.get("status") == "discarded"]
    active_count = len([i for i in metric_items if i.get("status") in ("validated", "validated_with_reservations")])
    review_count = len([i for i in metric_items if i.get("status") == "review"])
    discarded_count = len([i for i in metric_items if i.get("status") == "discarded"])
    total = active_count + review_count + discarded_count

    institutions_seen = {i.get("institution_id") for i in metric_items if i.get("institution_id")}
    inst_count = len(institutions_seen)

    benefit_counts: dict[str, int] = defaultdict(int)
    for i in all_items:
        if i.get("status") != "discarded":
            benefit_counts[i.get("campaign_type", "geral")] += 1
    benefit_summary_parts = []
    for k, v in sorted(benefit_counts.items(), key=lambda x: -x[1]):
        label = _BENEFIT_TYPE_LABELS.get(k, k)
        benefit_summary_parts.append(f"{label}: {v}")

    def _metric_card(label: str, value: str | int, color: str = "#0f172a") -> str:
        return (
            f'<div style="flex:1;min-width:140px;padding:16px 20px;background:#fff;border-radius:10px;'
            f'border:1px solid #e2e8f0;box-shadow:0 1px 2px rgba(0,0,0,0.04)">'
            f'<div style="font-size:0.8rem;color:#64748b;margin-bottom:4px">{label}</div>'
            f'<div style="font-size:1.6rem;font-weight:700;color:{color}">{value}</div>'
            f'</div>'
        )

    metrics_html = (
        '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:24px">'
        + _metric_card("Total encontradas", total)
        + _metric_card("Ativas confirmadas", active_count, "#16a34a")
        + _metric_card("Em revisao", review_count, "#2563eb")
        + _metric_card("Descartadas", discarded_count, "#dc2626")
        + _metric_card("Instituicoes", inst_count, "#7c3aed")
        + '</div>'
    )

    institution_table = _build_institution_table(report, list(institutions_seen))

    validated_cards = "\n".join(_build_campaign_card(i, reports_dir) for i in validated)
    review_cards = "\n".join(_build_campaign_card(i, reports_dir) for i in review_items)
    insights_html = _build_insights_html(insights_section) if insights_section else ""

    summary_escaped = _html_escape(report.summary).replace("\n", "<br>")

    novidades_html = ""
    if novidades_items:
        novidades_html = (
            f'<h2>Novidades neste ciclo ({len(novidades_items)})</h2>'
            + "\n".join(_build_campaign_card(i, reports_dir) for i in novidades_items)
        )

    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Relatorio completo &mdash; {_html_escape(report.generated_at[:10])}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
      margin: 0; padding: 24px 32px;
      background: #f8fafc; color: #1e293b; line-height: 1.5;
    }}
    h1 {{ font-size: 1.4rem; font-weight: 700; color: #0f172a; margin: 0 0 4px; }}
    h2 {{ font-size: 1.15rem; font-weight: 600; color: #334155; margin: 28px 0 12px; border-bottom: 2px solid #e2e8f0; padding-bottom: 6px; }}
    a {{ color: #2563eb; }}
    .subtitle {{ font-size: 0.85rem; color: #64748b; margin-bottom: 20px; }}
    .summary {{
      background: #fff; border-left: 4px solid #3b82f6; padding: 16px 20px;
      border-radius: 0 8px 8px 0; margin-bottom: 24px; font-size: 0.92rem; color: #334155;
    }}
    .empty {{ color: #94a3b8; font-style: italic; padding: 12px 0; }}
  </style>
</head>
<body>
  <h1>Relatorio completo de campanhas financeiras</h1>
  <div class="subtitle">{_html_escape(report.generated_at)} &mdash; {_html_escape(report.report_id)}</div>

  {metrics_html}

  <div class="summary">{summary_escaped}</div>

  {novidades_html}

  <h2>Comparativo por Instituicao</h2>
  {institution_table}

  <h2>Campanhas Ativas Confirmadas ({active_count})</h2>
  {validated_cards if validated_cards else '<p class="empty">Nenhuma campanha confirmada neste ciclo.</p>'}

  <h2>Campanhas em Revisao ({review_count})</h2>
  {review_cards if review_cards else '<p class="empty">Nenhuma campanha em revisao neste ciclo.</p>'}

  {('<h2>Insights Historicos</h2>' + insights_html) if insights_html else ''}

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
    html_text = render_dashboard_html(report, reports_dir)

    write_json(json_path, report.model_dump(mode="json"))
    md_path.write_text(markdown_text, encoding="utf-8")
    html_path.write_text(html_text, encoding="utf-8")

    return {"json": json_path, "markdown": md_path, "html": html_path}


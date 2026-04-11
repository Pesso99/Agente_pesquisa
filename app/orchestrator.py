from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import constants
from app.deduper import dedupe_campaigns
from app.emailer import send_email
from app.io_utils import (
    ensure_project_structure,
    iso_now_tz,
    list_json_files,
    read_json,
    stamp_for_id,
    write_json,
    write_model,
)
from app.models import Artifact, Campaign, Candidate, Handoff, Observation
from app.normalizers import normalize_campaign, normalize_campaign_type
from app.reporter import build_report, save_report_files
from app.scoring import validate_campaign as score_campaign
from app.validators import validate_model_against_schema


_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO5Wf/oAAAAASUVORK5CYII="
)


@dataclass
class ManualCycleResult:
    job_id: str
    candidates: list[Candidate]
    observations: list[Observation]
    campaigns: list[Campaign]
    dedupe_groups: dict[str, list[str]]
    report_paths: dict[str, Path]
    email_sent: bool


def _to_rel(path: Path) -> str:
    return path.relative_to(constants.ROOT_DIR).as_posix()


def _write_handoff(job_id: str, task: str, source: str, target: str, refs: list[str]) -> Path:
    handoff = Handoff(
        job_id=job_id,
        task=task,
        source_agent=source,
        target_agent=target,
        priority="normal",
        input_refs=refs,
        created_at=iso_now_tz(),
    )
    validate_model_against_schema(handoff)
    out = constants.JOBS_DIR / f"{job_id}_{task}_{target}.json"
    write_model(out, handoff)
    return out


def load_configs() -> dict[str, Any]:
    return {
        "institutions": read_json(constants.CONFIG_DIR / "institutions.json"),
        "routing": read_json(constants.CONFIG_DIR / "routing_rules.json"),
        "scoring": read_json(constants.CONFIG_DIR / "scoring_rules.json"),
        "report": read_json(constants.CONFIG_DIR / "report_settings.json"),
        "email": read_json(constants.CONFIG_DIR / "email_settings.json"),
    }


def discover_candidates(job_id: str, institutions: list[dict], routing_rules: dict) -> list[Candidate]:
    threshold = routing_rules.get("discovery_to_capture_min_confidence", 0.6)
    candidates: list[Candidate] = []
    stamp = stamp_for_id()
    index = 1

    for inst in sorted(institutions, key=lambda x: (x.get("priority", 99), x.get("institution_id", ""))):
        base_conf = 0.78 if inst.get("priority", 99) == 1 else 0.67
        if base_conf < threshold:
            continue
        source_url = f"https://{inst['official_domains'][0]}"
        headline = f"{inst['display_name']} - monitoramento de campanha promocional"
        candidate = Candidate(
            candidate_id=f"cand_{stamp}_{index:03d}",
            institution_id=inst["institution_id"],
            source_type="official_site",
            source_url=source_url,
            headline=headline,
            discovered_at=iso_now_tz(),
            confidence_initial=base_conf,
            summary=f"Busca inicial baseada em termos de {inst['display_name']}.",
            notes="Candidate gerado para ciclo manual v1.",
        )
        validate_model_against_schema(candidate)
        out = constants.CANDIDATES_DIR / f"{candidate.candidate_id}.json"
        write_model(out, candidate)
        candidates.append(candidate)
        index += 1

    _write_handoff(
        job_id,
        "discover_to_capture",
        "discover",
        "capture",
        [_to_rel(constants.CANDIDATES_DIR / f"{c.candidate_id}.json") for c in candidates],
    )
    return candidates


def capture_observations(job_id: str, candidates: list[Candidate]) -> list[Observation]:
    observations: list[Observation] = []
    stamp = stamp_for_id()

    for idx, candidate in enumerate(candidates, start=1):
        observation_id = f"obs_{stamp}_{idx:03d}"
        screenshot_path = constants.SCREENSHOTS_DIR / f"{observation_id}.png"
        raw_html_path = constants.RAW_HTML_DIR / f"{observation_id}.html"
        raw_text_path = constants.RAW_TEXT_DIR / f"{observation_id}.txt"

        screenshot_path.write_bytes(_PNG_1X1)
        raw_html_path.write_text(
            f"<html><body><h1>{candidate.headline}</h1><p>Fonte: {candidate.source_url}</p></body></html>",
            encoding="utf-8",
        )
        raw_text_path.write_text(
            f"{candidate.headline}\nFonte: {candidate.source_url}\nResumo: {candidate.summary or ''}",
            encoding="utf-8",
        )

        obs = Observation(
            observation_id=observation_id,
            candidate_id=candidate.candidate_id,
            captured_at=iso_now_tz(),
            source_url=candidate.source_url,
            page_title=candidate.headline,
            visible_claims=[
                candidate.headline,
                f"Oferta identificada para {candidate.institution_id}.",
            ],
            artifacts=[
                Artifact(type="screenshot_full", path=_to_rel(screenshot_path)),
                Artifact(type="raw_html", path=_to_rel(raw_html_path)),
                Artifact(type="raw_text", path=_to_rel(raw_text_path)),
            ],
            raw_html_path=_to_rel(raw_html_path),
            raw_text_path=_to_rel(raw_text_path),
        )
        validate_model_against_schema(obs)
        write_model(constants.OBSERVATIONS_DIR / f"{obs.observation_id}.json", obs)
        observations.append(obs)

    _write_handoff(
        job_id,
        "capture_to_extract",
        "capture",
        "extract",
        [_to_rel(constants.OBSERVATIONS_DIR / f"{o.observation_id}.json") for o in observations],
    )
    return observations


def _infer_benefit(text: str) -> str | None:
    lowered = text.lower()
    if "cashback" in lowered:
        return "Cashback promocional em canais elegiveis."
    if "cdb" in lowered:
        return "Taxa promocional de renda fixa."
    if "invest" in lowered:
        return "Condicao comercial melhor para investimentos."
    return "Beneficio identificado no anuncio, detalhes no regulamento."


def extract_campaigns(
    job_id: str, candidates: list[Candidate], observations: list[Observation]
) -> list[Campaign]:
    by_candidate = {c.candidate_id: c for c in candidates}
    campaigns: list[Campaign] = []
    stamp = stamp_for_id()

    for idx, obs in enumerate(observations, start=1):
        candidate = by_candidate[obs.candidate_id]
        campaign = Campaign(
            campaign_id=f"camp_{stamp}_{idx:03d}",
            institution_id=candidate.institution_id,
            campaign_name=candidate.headline,
            campaign_type=normalize_campaign_type(candidate.headline),
            benefit=_infer_benefit(candidate.headline),
            audience="clientes em geral",
            source_url=candidate.source_url,
            source_type=candidate.source_type,
            regulation_url=None,
            start_date=None,
            end_date=None,
            status="review",
            confidence_final=0.0,
            validation_notes="Ainda nao validada.",
            evidence_refs=[obs.observation_id] + [artifact.path for artifact in obs.artifacts],
            channels=["site_oficial" if candidate.source_type == "official_site" else "social"],
        )
        campaign = normalize_campaign(campaign)
        validate_model_against_schema(campaign)
        write_model(constants.CAMPAIGNS_DIR / f"{campaign.campaign_id}.json", campaign)
        campaigns.append(campaign)

    _write_handoff(
        job_id,
        "extract_to_validate",
        "extract",
        "validate",
        [_to_rel(constants.CAMPAIGNS_DIR / f"{c.campaign_id}.json") for c in campaigns],
    )
    return campaigns


def validate_campaigns(
    job_id: str,
    campaigns: list[Campaign],
    observations: list[Observation],
    scoring_rules: dict,
) -> list[Campaign]:
    by_observation = {o.observation_id: o for o in observations}
    validated: list[Campaign] = []

    for campaign in campaigns:
        has_screenshot = False
        for ref in campaign.evidence_refs:
            obs = by_observation.get(ref)
            if not obs:
                continue
            if any(artifact.type.startswith("screenshot") for artifact in obs.artifacts):
                has_screenshot = True
                break
        updated = score_campaign(campaign, scoring_rules, has_screenshot=has_screenshot)
        validate_model_against_schema(updated)
        write_model(constants.CAMPAIGNS_DIR / f"{updated.campaign_id}.json", updated)
        validated.append(updated)

    _write_handoff(
        job_id,
        "validate_to_report",
        "validate",
        "report",
        [_to_rel(constants.CAMPAIGNS_DIR / f"{c.campaign_id}.json") for c in validated],
    )
    return validated


def generate_report(job_id: str, campaigns: list[Campaign], report_settings: dict) -> dict[str, Path]:
    report = build_report(campaigns, report_settings, report_id=f"report_{job_id}")
    paths = save_report_files(report, constants.REPORTS_DIR)
    _write_handoff(
        job_id,
        "report_to_sender",
        "report",
        "sender",
        [_to_rel(paths["json"]), _to_rel(paths["markdown"]), _to_rel(paths["html"])],
    )
    return paths


def run_manual_cycle(
    job_id: str,
    *,
    send_report_email: bool = False,
    recipients: list[str] | None = None,
) -> ManualCycleResult:
    ensure_project_structure()
    cfg = load_configs()
    candidates = discover_candidates(job_id, cfg["institutions"], cfg["routing"])
    observations = capture_observations(job_id, candidates)
    extracted = extract_campaigns(job_id, candidates, observations)
    validated = validate_campaigns(job_id, extracted, observations, cfg["scoring"])
    unique_campaigns, groups = dedupe_campaigns(validated)
    report_paths = generate_report(job_id, unique_campaigns, cfg["report"])

    email_sent = False
    if send_report_email:
        recipient_list = recipients or cfg["email"].get("default_recipients", [])
        subject = f"{cfg['report'].get('subject_prefix', 'Monitor diario')} - {job_id}"
        send_email(
            html_path=str(report_paths["html"]),
            subject=subject,
            recipients=recipient_list,
            smtp_host=cfg["email"].get("smtp_host", "smtp.gmail.com"),
            smtp_port=int(cfg["email"].get("smtp_port", 587)),
            use_tls=bool(cfg["email"].get("use_tls", True)),
        )
        email_sent = True

    summary_path = constants.JOBS_DIR / f"{job_id}_summary.json"
    write_json(
        summary_path,
        {
            "job_id": job_id,
            "created_at": iso_now_tz(),
            "counts": {
                "candidates": len(candidates),
                "observations": len(observations),
                "campaigns_validated": len(validated),
                "campaigns_after_dedupe": len(unique_campaigns),
            },
            "dedupe_groups": groups,
            "report_files": {k: _to_rel(v) for k, v in report_paths.items()},
            "email_sent": email_sent,
        },
    )

    return ManualCycleResult(
        job_id=job_id,
        candidates=candidates,
        observations=observations,
        campaigns=unique_campaigns,
        dedupe_groups=groups,
        report_paths=report_paths,
        email_sent=email_sent,
    )


def load_campaigns_from_disk() -> list[Campaign]:
    campaigns: list[Campaign] = []
    for path in list_json_files(constants.CAMPAIGNS_DIR):
        campaigns.append(Campaign.model_validate(read_json(path)))
    return campaigns


from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

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


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 20
KEYWORDS = (
    "promoc",
    "campanh",
    "cashback",
    "oferta",
    "beneficio",
    "cdb",
    "invest",
    "cartao",
    "credito",
    "pix",
)
DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b")
BROWSER_CANDIDATES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
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


def _safe_get(url: str) -> requests.Response | None:
    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9"},
        )
        response.raise_for_status()
        return response
    except requests.RequestException:
        return None


def _keyword_score(*, text: str, search_terms: list[str]) -> float:
    lowered = text.lower()
    score = 0.0
    for keyword in KEYWORDS:
        if keyword in lowered:
            score += 0.08
    for term in search_terms:
        token = term.split()[0].lower()
        if token and token in lowered:
            score += 0.03
    return min(score, 0.55)


def _is_official_url(url: str, domains: list[str]) -> bool:
    host = urlparse(url).netloc.lower()
    return any(domain.lower() in host for domain in domains)


def _discover_from_page(
    *,
    base_url: str,
    html: str,
    domains: list[str],
    search_terms: list[str],
    max_links: int = 6,
) -> list[tuple[str, str, float]]:
    soup = BeautifulSoup(html, "lxml")
    items: list[tuple[str, str, float]] = []
    seen: set[str] = set()

    page_title = (soup.title.text or "").strip() if soup.title else ""
    main_text = " ".join(
        filter(
            None,
            [
                page_title,
                " ".join(h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2"])[:8]),
            ],
        )
    )
    if _keyword_score(text=main_text, search_terms=search_terms) >= 0.1:
        items.append((base_url, page_title or base_url, 0.72))
        seen.add(base_url)

    for link in soup.find_all("a", href=True):
        href = urljoin(base_url, link["href"]).strip()
        if not href.startswith("http"):
            continue
        if href in seen:
            continue
        if not _is_official_url(href, domains):
            continue

        anchor_text = link.get_text(" ", strip=True)
        joined = f"{href} {anchor_text}"
        score = _keyword_score(text=joined, search_terms=search_terms)
        if score < 0.12:
            continue

        confidence = min(0.6 + score, 0.93)
        title = anchor_text or href
        items.append((href, title, confidence))
        seen.add(href)
        if len(items) >= max_links:
            break
    return items


def discover_candidates(job_id: str, institutions: list[dict], routing_rules: dict) -> list[Candidate]:
    threshold = routing_rules.get("discovery_to_capture_min_confidence", 0.6)
    max_per_institution = max(1, int(routing_rules.get("max_candidates_per_institution", 2)))
    max_total = max(1, int(routing_rules.get("max_candidates_total", 8)))
    include_social_sources = bool(routing_rules.get("include_social_sources", False))
    candidates: list[Candidate] = []
    stamp = stamp_for_id()
    index = 1

    for inst in sorted(institutions, key=lambda x: (x.get("priority", 99), x.get("institution_id", ""))):
        if len(candidates) >= max_total:
            break
        domains = inst.get("official_domains", [])
        if not domains:
            continue
        seeds = [f"https://{domains[0]}"]
        seeds.extend(
            f"https://{domains[0]}/{suffix}"
            for suffix in ("promocoes", "promocao", "ofertas", "campanhas")
        )
        search_terms = inst.get("search_terms", [])
        candidate_rows: list[tuple[str, str, float, str]] = []
        saw_official_candidate = False

        for seed in seeds:
            response = _safe_get(seed)
            if not response:
                continue
            rows = _discover_from_page(
                base_url=seed,
                html=response.text,
                domains=domains,
                search_terms=search_terms,
                max_links=4,
            )
            for url, title, confidence in rows:
                candidate_rows.append((url, title, confidence, "official_site"))
                saw_official_candidate = True

        if include_social_sources:
            for social_url in (inst.get("official_socials") or {}).values():
                if isinstance(social_url, str) and social_url.startswith("http"):
                    conf = min(0.64, threshold + 0.03)
                    candidate_rows.append(
                        (social_url, f"{inst['display_name']} social oficial", conf, "social_official")
                    )

        deduped: dict[str, tuple[str, float, str]] = {}
        for url, title, conf, source_type in candidate_rows:
            previous = deduped.get(url)
            if previous is None or conf > previous[1]:
                deduped[url] = (title, conf, source_type)

        if not saw_official_candidate:
            fallback_url = f"https://{domains[0]}"
            deduped[fallback_url] = (
                f"{inst['display_name']} - pagina oficial",
                max(threshold + 0.02, 0.62),
                "official_site",
            )

        ranked_rows = sorted(
            deduped.items(),
            key=lambda item: ((item[1][2] == "official_site"), item[1][1]),
            reverse=True,
        )
        added_for_institution = 0
        for url, (title, confidence, source_type) in ranked_rows:
            if len(candidates) >= max_total:
                break
            if added_for_institution >= max_per_institution:
                break
            if confidence < threshold:
                continue
            candidate = Candidate(
                candidate_id=f"cand_{stamp}_{index:03d}",
                institution_id=inst["institution_id"],
                source_type=source_type,  # type: ignore[arg-type]
                source_url=url,
                headline=title[:160] if title else f"{inst['display_name']} campanha",
                discovered_at=iso_now_tz(),
                confidence_initial=round(confidence, 3),
                summary=f"Descoberta real em fonte {'oficial' if source_type != 'third_party' else 'terceira'}.",
                notes=f"Seed principal: https://{domains[0]}",
            )
            validate_model_against_schema(candidate)
            write_model(constants.CANDIDATES_DIR / f"{candidate.candidate_id}.json", candidate)
            candidates.append(candidate)
            index += 1
            added_for_institution += 1

    _write_handoff(
        job_id,
        "discover_to_capture",
        "discover",
        "capture",
        [_to_rel(constants.CANDIDATES_DIR / f"{c.candidate_id}.json") for c in candidates],
    )
    return candidates


def _extract_text_and_claims(html: str) -> tuple[str, list[str]]:
    soup = BeautifulSoup(html, "lxml")
    title = (soup.title.text or "").strip() if soup.title else ""
    heading_claims = [
        node.get_text(" ", strip=True)
        for node in soup.find_all(["h1", "h2", "h3"])
        if node.get_text(" ", strip=True)
    ]
    heading_claims = [claim for claim in heading_claims if len(claim) >= 8][:10]
    text = soup.get_text("\n", strip=True)
    return title, heading_claims


def _capture_by_requests(url: str) -> tuple[str | None, str | None, list[str], str | None]:
    response = _safe_get(url)
    if not response:
        return None, None, [], "Falha HTTP na captura."
    html = response.text
    title, claims = _extract_text_and_claims(html)
    text = BeautifulSoup(html, "lxml").get_text("\n", strip=True)
    return html, text, claims, title


def _find_browser_binary() -> Path | None:
    for candidate in BROWSER_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def _capture_screenshot_cli(
    browser_bin: Path, url: str, screenshot_path: Path, *, timeout_seconds: int
) -> tuple[bool, str | None]:
    command = [
        str(browser_bin),
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--window-size=1366,900",
        "--virtual-time-budget=15000",
        f"--screenshot={screenshot_path}",
        url,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except Exception as exc:  # noqa: PERF203
        return False, f"Falha ao executar browser headless: {type(exc).__name__}"

    if completed.returncode == 0 and screenshot_path.exists() and screenshot_path.stat().st_size > 120:
        return True, None

    stderr = (completed.stderr or "").strip()
    stdout = (completed.stdout or "").strip()
    reason = stderr or stdout or f"returncode={completed.returncode}"
    return False, f"Falha screenshot headless: {reason[:200]}"


def capture_observations(
    job_id: str,
    candidates: list[Candidate],
    *,
    capture_timeout_seconds: int = 12,
) -> list[Observation]:
    observations: list[Observation] = []
    stamp = stamp_for_id()
    browser_bin = _find_browser_binary()

    def build_observation(
        *,
        idx: int,
        candidate: Candidate,
        page_title: str | None,
        claims: list[str],
        html: str | None,
        text: str | None,
        screenshot_written: bool,
        capture_note: str | None = None,
    ) -> Observation:
        observation_id = f"obs_{stamp}_{idx:03d}"
        screenshot_path = constants.SCREENSHOTS_DIR / f"{observation_id}.png"
        raw_html_path = constants.RAW_HTML_DIR / f"{observation_id}.html"
        raw_text_path = constants.RAW_TEXT_DIR / f"{observation_id}.txt"

        artifacts = []
        if screenshot_written and screenshot_path.exists():
            artifacts.append(Artifact(type="screenshot_full", path=_to_rel(screenshot_path)))

        if html:
            raw_html_path.write_text(html, encoding="utf-8")
            artifacts.append(Artifact(type="raw_html", path=_to_rel(raw_html_path)))
        if text:
            raw_text_path.write_text(text, encoding="utf-8")
            artifacts.append(Artifact(type="raw_text", path=_to_rel(raw_text_path)))

        if capture_note:
            claims = claims + [capture_note]
        if not claims:
            claims = [candidate.headline]

        obs = Observation(
            observation_id=observation_id,
            candidate_id=candidate.candidate_id,
            captured_at=iso_now_tz(),
            source_url=candidate.source_url,
            page_title=page_title or candidate.headline,
            visible_claims=claims[:12],
            artifacts=artifacts,
            raw_html_path=_to_rel(raw_html_path) if html else None,
            raw_text_path=_to_rel(raw_text_path) if text else None,
        )
        validate_model_against_schema(obs)
        write_model(constants.OBSERVATIONS_DIR / f"{obs.observation_id}.json", obs)
        return obs

    for idx, candidate in enumerate(candidates, start=1):
        html, text, claims, page_title = _capture_by_requests(candidate.source_url)
        observation_id = f"obs_{stamp}_{idx:03d}"
        screenshot_path = constants.SCREENSHOTS_DIR / f"{observation_id}.png"
        screenshot_written = False
        capture_note = None

        if browser_bin is not None and candidate.source_type == "official_site":
            screenshot_written, err = _capture_screenshot_cli(
                browser_bin,
                candidate.source_url,
                screenshot_path,
                timeout_seconds=max(4, capture_timeout_seconds),
            )
            if (not screenshot_written) and html and "http" in candidate.source_url:
                # Retry curto para paginas pesadas que carregam com atraso.
                screenshot_written_retry, err_retry = _capture_screenshot_cli(
                    browser_bin,
                    candidate.source_url,
                    screenshot_path,
                    timeout_seconds=min(30, max(8, capture_timeout_seconds * 2)),
                )
                if screenshot_written_retry:
                    screenshot_written = True
                    err = None
                elif err_retry:
                    err = err_retry
            if err:
                capture_note = err
        elif candidate.source_type == "social_official":
            capture_note = "Screenshot skip para social oficial no modo headless v1."
        else:
            capture_note = "Nenhum browser (Chrome/Edge) encontrado para screenshot."

        obs = build_observation(
            idx=idx,
            candidate=candidate,
            page_title=page_title,
            claims=claims,
            html=html,
            text=text,
            screenshot_written=screenshot_written,
            capture_note=capture_note,
        )
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
        return "Oferta com cashback para produtos elegiveis."
    if "cdb" in lowered or "cdi" in lowered:
        return "Oferta promocional de renda fixa."
    if "cartao" in lowered:
        return "Oferta associada a cartao e beneficios."
    if "pix" in lowered:
        return "Oferta promocional associada a Pix."
    if "invest" in lowered:
        return "Oferta associada a investimentos."
    return None


def _extract_dates(text: str) -> tuple[str | None, str | None]:
    matches = DATE_RE.findall(text)
    if not matches:
        return None, None
    start = matches[0] if len(matches) >= 1 else None
    end = matches[1] if len(matches) >= 2 else None
    return start, end


def _raw_text_from_observation(obs: Observation) -> str:
    if not obs.raw_text_path:
        return ""
    path = constants.ROOT_DIR / obs.raw_text_path
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")[:6000]
    except OSError:
        return ""


def extract_campaigns(
    job_id: str, candidates: list[Candidate], observations: list[Observation]
) -> list[Campaign]:
    by_candidate = {c.candidate_id: c for c in candidates}
    campaigns: list[Campaign] = []
    stamp = stamp_for_id()

    for idx, obs in enumerate(observations, start=1):
        candidate = by_candidate[obs.candidate_id]
        combined_claims = " | ".join(obs.visible_claims[:8])
        raw_text = _raw_text_from_observation(obs)
        combined_text = f"{candidate.headline} {obs.page_title or ''} {combined_claims} {raw_text}".strip()
        benefit = _infer_benefit(combined_text)
        start_date, end_date = _extract_dates(combined_text)
        campaign_name = (obs.page_title or candidate.headline or candidate.source_url)[:180]

        campaign = Campaign(
            campaign_id=f"camp_{stamp}_{idx:03d}",
            institution_id=candidate.institution_id,
            campaign_name=campaign_name,
            campaign_type=normalize_campaign_type(combined_text),
            benefit=benefit,
            audience="clientes em geral",
            source_url=candidate.source_url,
            source_type=candidate.source_type,
            regulation_url=None,
            start_date=start_date,
            end_date=end_date,
            status="review",
            confidence_final=0.0,
            validation_notes="Ainda nao validada.",
            evidence_refs=[obs.observation_id] + [artifact.path for artifact in obs.artifacts],
            channels=["site_oficial" if candidate.source_type == "official_site" else "social_oficial"],
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
    routing_overrides: dict[str, Any] | None = None,
) -> ManualCycleResult:
    ensure_project_structure()
    cfg = load_configs()
    routing = dict(cfg["routing"])
    if routing_overrides:
        routing.update(routing_overrides)

    candidates = discover_candidates(job_id, cfg["institutions"], routing)
    observations = capture_observations(
        job_id,
        candidates,
        capture_timeout_seconds=int(routing.get("capture_timeout_seconds", 12)),
    )
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
            "browser_binary": str(_find_browser_binary()) if _find_browser_binary() else None,
            "routing_used": routing,
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

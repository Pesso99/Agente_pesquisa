from __future__ import annotations

import hashlib
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from app import constants
from app.deduper import dedupe_campaigns
from app.emailer import send_email
from app.io_utils import ensure_project_structure, iso_now_tz, list_json_files, read_json, stamp_for_id, write_json, write_model
from app.models import Artifact, Campaign, Candidate, Handoff, Observation
from app.normalizers import normalize_campaign, normalize_campaign_type, normalize_institution_id
from app.quality_gate import QualityAssessment, assess_observation_quality
from app.reporter import build_report, save_report_files
from app.runtime_db import RuntimeDB
from app.scoring import validate_campaign_two_pass
from app.validators import validate_model_against_schema

T = TypeVar("T")
REQUEST_TIMEOUT = 20
KEYWORDS = ("promoc", "campanh", "cashback", "oferta", "desconto", "bonus", "cupom", "anuidade", "milha", "ponto", "cdb", "cdi", "rendimento")
DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b")
CDI_PERCENT_RE = re.compile(r"\d+\s*%\s*do\s*cdi")
MONEY_RE = re.compile(r"r\$\s*\d+", re.IGNORECASE)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
BROWSER_CANDIDATES = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)

CAMPAIGN_SIGNAL_TERMS = (
    "promocao",
    "campanha",
    "cashback",
    "desconto",
    "oferta por tempo limitado",
    "participe",
    "regulamento",
    "cupom",
    "ganhe",
    "acumule pontos",
    "anuidade gratis",
    "isencao de anuidade",
    "milhas",
    "bonus",
)

INSTITUTIONAL_NEGATIVE_TERMS = (
    "sistema de informacoes de credito",
    "politica de privacidade",
    "termos de uso",
    "ouvidoria",
    "fale conosco",
    "tarifas",
    "acessibilidade",
    "sustentabilidade",
    "governanca",
    "carreiras",
    "imprensa",
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


def _canon_url(url: str) -> str:
    parsed = urlparse(url)
    clean = parsed._replace(fragment="", query="")
    return urlunparse(clean).rstrip("/")


def _fingerprint(inst_id: str, url: str, title: str) -> str:
    normalized = f"{normalize_institution_id(inst_id)}|{_canon_url(url)}|{re.sub(r'[^a-z0-9 ]+', '', title.lower())}"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _fold_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", value)
    without_accents = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_accents.lower()).strip()


def _safe_get(url: str) -> requests.Response | None:
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9"})
        resp.raise_for_status()
        return resp
    except requests.RequestException:
        return None


def _is_official(url: str, domains: list[str]) -> bool:
    host = urlparse(url).netloc.lower()
    return any(domain.lower() in host for domain in domains)


def _is_instagram(url: str) -> bool:
    return "instagram.com" in urlparse(url).netloc.lower()


def _discover_rows(base_url: str, html: str, domains: list[str], search_terms: list[str], max_links: int = 5) -> list[tuple[str, str, float]]:
    soup = BeautifulSoup(html, "lxml")
    rows: list[tuple[str, str, float]] = []
    seen: set[str] = set()
    title = (soup.title.text or "").strip() if soup.title else ""
    heading_text = " ".join(h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2"])[:8])
    if any(k in f"{title} {heading_text}".lower() for k in KEYWORDS):
        rows.append((base_url, title or base_url, 0.72))
        seen.add(base_url)
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"]).strip()
        if not href.startswith("http") or href in seen or not _is_official(href, domains):
            continue
        text = f"{href} {a.get_text(' ', strip=True)}".lower()
        kw = sum(1 for k in KEYWORDS if k in text)
        if kw == 0:
            continue
        conf = min(0.6 + 0.06 * kw + 0.03 * sum(1 for t in search_terms if t.lower() in text), 0.93)
        rows.append((href, a.get_text(" ", strip=True) or href, conf))
        seen.add(href)
        if len(rows) >= max_links:
            break
    return rows


def _find_browser() -> Path | None:
    for path in BROWSER_CANDIDATES:
        if path.exists():
            return path
    return None


def _screenshot(browser_bin: Path, url: str, out_png: Path, timeout_s: int) -> tuple[bool, str | None]:
    cmd = [str(browser_bin), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--window-size=1366,900", "--virtual-time-budget=15000", f"--screenshot={out_png}", url]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    except Exception as exc:  # noqa: PERF203
        return False, f"browser_error:{type(exc).__name__}"
    if cp.returncode == 0 and out_png.exists() and out_png.stat().st_size > 120:
        return True, None
    err = (cp.stderr or cp.stdout or str(cp.returncode)).strip()
    return False, f"screenshot_failed:{err[:180]}"


def _write_handoff(
    job_id: str,
    *,
    trace_id: str,
    task: str,
    source_agent: str,
    target_agent: str,
    input_refs: list[str],
    runtime_db: RuntimeDB | None = None,
    attempt: int = 1,
    source_quality_label: str = "campaign_like",
    capture_quality_score: float = 1.0,
    blocking_reasons: list[str] | None = None,
) -> None:
    handoff = Handoff(
        job_id=job_id,
        trace_id=trace_id,
        task=task,
        source_agent=source_agent,
        target_agent=target_agent,
        input_refs=input_refs,
        created_at=iso_now_tz(),
        attempt=attempt,
        source_quality_label=source_quality_label,
        capture_quality_score=capture_quality_score,
        blocking_reasons=blocking_reasons or [],
        priority="normal",
    )
    validate_model_against_schema(handoff)
    out = constants.JOBS_DIR / f"{job_id}_{task}_{target_agent}.json"
    write_model(out, handoff)
    if runtime_db:
        runtime_db.add_handoff(handoff)


def load_configs() -> dict[str, Any]:
    models_path = constants.CONFIG_DIR / "agent_models.json"
    return {
        "institutions": read_json(constants.CONFIG_DIR / "institutions.json"),
        "routing": read_json(constants.CONFIG_DIR / "routing_rules.json"),
        "scoring": read_json(constants.CONFIG_DIR / "scoring_rules.json"),
        "report": read_json(constants.CONFIG_DIR / "report_settings.json"),
        "email": read_json(constants.CONFIG_DIR / "email_settings.json"),
        "agent_models": read_json(models_path) if models_path.exists() else {},
    }


def discover_candidates(job_id: str, institutions: list[dict], routing: dict, *, runtime_db: RuntimeDB | None = None, trace_id: str | None = None, attempt: int = 1) -> list[Candidate]:
    threshold = float(routing.get("discovery_to_capture_min_confidence", 0.6))
    max_total = max(1, int(routing.get("max_candidates_total", 8)))
    max_per_inst = max(1, int(routing.get("max_candidates_per_institution", 2)))
    include_instagram = bool(routing.get("include_instagram_sources", True))
    trace = trace_id or f"{job_id}:discover"
    stamp = stamp_for_id()
    output: list[Candidate] = []
    idx = 1
    for inst in sorted(institutions, key=lambda x: (x.get("priority", 99), x.get("institution_id", ""))):
        if len(output) >= max_total:
            break
        domains = inst.get("official_domains", [])
        if not domains:
            continue
        seeds = [f"https://{domains[0]}", f"https://{domains[0]}/promocoes", f"https://{domains[0]}/ofertas", f"https://{domains[0]}/campanhas", f"https://{domains[0]}/cartao"]
        rows: dict[str, tuple[str, float, str]] = {}
        for seed in seeds:
            resp = _safe_get(seed)
            if not resp:
                continue
            for url, title, conf in _discover_rows(seed, resp.text, domains, inst.get("search_terms", [])):
                key = _canon_url(url)
                prev = rows.get(key)
                if prev is None or conf > prev[1]:
                    rows[key] = (title, conf, "official_site")
        if include_instagram:
            for social in (inst.get("official_socials") or {}).values():
                if isinstance(social, str) and social.startswith("http") and _is_instagram(social):
                    rows[_canon_url(social)] = (f"{inst['display_name']} instagram oficial", 0.62, "social_official")
        if not rows:
            fallback = f"https://{domains[0]}"
            rows[_canon_url(fallback)] = (f"{inst['display_name']} - pagina oficial", max(0.62, threshold + 0.02), "official_site")
        added = 0
        for url, (title, conf, source_type) in sorted(rows.items(), key=lambda kv: (kv[1][2] == "official_site", kv[1][1]), reverse=True):
            if len(output) >= max_total or added >= max_per_inst:
                break
            if conf < threshold:
                continue
            fprint = _fingerprint(inst["institution_id"], url, title)
            if runtime_db and not runtime_db.register_fingerprint(fprint, job_id=job_id):
                continue
            cand = Candidate(
                candidate_id=f"cand_{stamp}_{idx:03d}",
                institution_id=inst["institution_id"],
                source_type=source_type,  # type: ignore[arg-type]
                source_url=url,
                headline=(title or f"{inst['display_name']} campanha")[:180],
                discovered_at=iso_now_tz(),
                confidence_initial=round(conf, 3),
                summary="discover oficial/landing/instagram",
                notes=f"fingerprint={fprint}",
            )
            validate_model_against_schema(cand)
            candidate_path = constants.CANDIDATES_DIR / f"{cand.candidate_id}.json"
            write_model(candidate_path, cand)
            output.append(cand)
            added += 1
            idx += 1
            if runtime_db:
                runtime_db.index_artifact(
                    job_id=job_id,
                    entity_type="candidate",
                    entity_id=cand.candidate_id,
                    artifact_type="candidate_json",
                    path=_to_rel(candidate_path),
                    meta={"source_type": cand.source_type},
                )
                runtime_db.add_agent_message(job_id=job_id, trace_id=trace, source_agent="discover", target_agent="capture", message_type="candidate_found", body={"candidate_id": cand.candidate_id, "url": cand.source_url})
    _write_handoff(job_id, trace_id=trace, task="discover_to_capture", source_agent="discover", target_agent="capture", input_refs=[_to_rel(constants.CANDIDATES_DIR / f"{x.candidate_id}.json") for x in output], runtime_db=runtime_db, attempt=attempt)
    return output


def capture_observations(
    job_id: str,
    candidates: list[Candidate],
    *,
    capture_timeout_seconds: int = 12,
    runtime_db: RuntimeDB | None = None,
    trace_id: str | None = None,
    attempt: int = 1,
    block_institutional: bool = True,
) -> tuple[list[Observation], dict[str, QualityAssessment]]:
    trace = trace_id or f"{job_id}:capture"
    stamp = stamp_for_id()
    browser = _find_browser()
    observations: list[Observation] = []
    quality_by_obs: dict[str, QualityAssessment] = {}
    for i, cand in enumerate(candidates, start=1):
        obs_id = f"obs_{stamp}_{i:03d}"
        png = constants.SCREENSHOTS_DIR / f"{obs_id}.png"
        html_path = constants.RAW_HTML_DIR / f"{obs_id}.html"
        text_path = constants.RAW_TEXT_DIR / f"{obs_id}.txt"
        resp = _safe_get(cand.source_url)
        html = resp.text if resp else None
        text = BeautifulSoup(html, "lxml").get_text("\n", strip=True) if html else None
        title = None
        claims: list[str] = []
        if html:
            soup = BeautifulSoup(html, "lxml")
            title = (soup.title.text or "").strip() if soup.title else None
            claims = [h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2", "h3"]) if h.get_text(" ", strip=True)][:12]
        screenshot_ok = False
        note = None
        if browser:
            screenshot_ok, shot_err = _screenshot(browser, cand.source_url, png, max(4, capture_timeout_seconds))
            if (not screenshot_ok) and html:
                retry_ok, retry_err = _screenshot(browser, cand.source_url, png, min(30, max(8, capture_timeout_seconds * 2)))
                screenshot_ok = retry_ok
                shot_err = None if retry_ok else retry_err
            note = shot_err
        else:
            note = "no_browser"
        artifacts: list[Artifact] = []
        if screenshot_ok and png.exists():
            artifacts.append(Artifact(type="screenshot_full", path=_to_rel(png)))
        if html:
            html_path.write_text(html, encoding="utf-8")
            artifacts.append(Artifact(type="raw_html", path=_to_rel(html_path)))
        if text:
            text_path.write_text(text, encoding="utf-8")
            artifacts.append(Artifact(type="raw_text", path=_to_rel(text_path)))
        visible_claims = claims or [cand.headline]
        if note:
            visible_claims = visible_claims + [note]
        obs = Observation(
            observation_id=obs_id,
            candidate_id=cand.candidate_id,
            captured_at=iso_now_tz(),
            source_url=cand.source_url,
            page_title=title or cand.headline,
            visible_claims=visible_claims[:12],
            artifacts=artifacts,
            raw_html_path=_to_rel(html_path) if html else None,
            raw_text_path=_to_rel(text_path) if text else None,
        )
        validate_model_against_schema(obs)
        observation_path = constants.OBSERVATIONS_DIR / f"{obs.observation_id}.json"
        write_model(observation_path, obs)
        observations.append(obs)
        qa = assess_observation_quality(cand, obs, block_institutional=block_institutional)
        quality_by_obs[obs.observation_id] = qa
        if runtime_db:
            runtime_db.index_artifact(
                job_id=job_id,
                entity_type="observation",
                entity_id=obs.observation_id,
                artifact_type="observation_json",
                path=_to_rel(observation_path),
                meta={"source_quality_label": qa.source_quality_label},
            )
            for art in artifacts:
                runtime_db.index_artifact(job_id=job_id, entity_type="observation", entity_id=obs.observation_id, artifact_type=art.type, path=art.path, meta={"source_url": cand.source_url})
            runtime_db.add_agent_message(job_id=job_id, trace_id=trace, source_agent="capture", target_agent="extract", message_type="observation_captured", body={"observation_id": obs.observation_id, "quality_label": qa.source_quality_label, "capture_quality_score": qa.capture_quality_score, "blocked": qa.should_block})
    avg_score = (sum(q.capture_quality_score for q in quality_by_obs.values()) / len(quality_by_obs)) if quality_by_obs else 0.0
    blocking = sorted({r for q in quality_by_obs.values() for r in q.blocking_reasons if r.startswith("blocked_source_label:")})
    _write_handoff(job_id, trace_id=trace, task="capture_to_extract", source_agent="capture", target_agent="extract", input_refs=[_to_rel(constants.OBSERVATIONS_DIR / f"{x.observation_id}.json") for x in observations], runtime_db=runtime_db, attempt=attempt, source_quality_label="campaign_like", capture_quality_score=round(avg_score, 3), blocking_reasons=blocking)
    return observations, quality_by_obs


def _infer_benefit(text: str) -> str | None:
    low = _fold_text(text)
    if "cashback" in low:
        return "Oferta com cashback para produtos elegiveis."
    if "isencao de anuidade" in low or "anuidade gratis" in low:
        return "Oferta com isencao de anuidade."
    if "acumule pontos" in low or "milhas" in low:
        return "Oferta com acumulo de pontos/milhas."
    if "cdb" in low or "cdi" in low or CDI_PERCENT_RE.search(low):
        return "Oferta promocional de renda fixa."
    if "desconto" in low or MONEY_RE.search(low):
        return "Oferta com desconto financeiro claro."
    return None


def _looks_like_campaign(text: str) -> bool:
    folded = _fold_text(text)
    positive_hits = sum(1 for token in CAMPAIGN_SIGNAL_TERMS if token in folded)
    negative_hits = sum(1 for token in INSTITUTIONAL_NEGATIVE_TERMS if token in folded)
    has_date = bool(DATE_RE.search(text))
    has_value = bool(CDI_PERCENT_RE.search(folded) or MONEY_RE.search(folded))

    if negative_hits >= 2 and positive_hits <= 1:
        return False
    if positive_hits >= 2:
        return True
    if positive_hits >= 1 and (has_date or has_value or "regulamento" in folded):
        return True
    return False


def extract_campaigns(
    job_id: str,
    candidates: list[Candidate],
    observations: list[Observation],
    quality_by_obs: dict[str, QualityAssessment] | None = None,
    *,
    runtime_db: RuntimeDB | None = None,
    trace_id: str | None = None,
    attempt: int = 1,
    min_capture_quality_score: float = 0.55,
) -> list[Campaign]:
    trace = trace_id or f"{job_id}:extract"
    by_candidate = {c.candidate_id: c for c in candidates}
    stamp = stamp_for_id()
    campaigns: list[Campaign] = []
    for i, obs in enumerate(observations, start=1):
        cand = by_candidate[obs.candidate_id]
        qa = (quality_by_obs or {}).get(obs.observation_id)
        if qa and qa.should_block:
            if runtime_db:
                runtime_db.add_dead_letter(job_id=job_id, stage="quality_gate", record_id=obs.observation_id, error_message="Observation blocked by quality gate.", payload={"source_quality_label": qa.source_quality_label, "blocking_reasons": qa.blocking_reasons, "source_url": obs.source_url})
            continue
        if qa and qa.capture_quality_score < min_capture_quality_score:
            if runtime_db:
                runtime_db.add_dead_letter(
                    job_id=job_id,
                    stage="capture_quality",
                    record_id=obs.observation_id,
                    error_message="Observation capture quality below threshold.",
                    payload={
                        "capture_quality_score": qa.capture_quality_score,
                        "min_capture_quality_score": min_capture_quality_score,
                        "source_url": obs.source_url,
                    },
                )
            continue
        raw_text = ""
        if obs.raw_text_path:
            p = constants.ROOT_DIR / obs.raw_text_path
            if p.exists():
                raw_text = p.read_text(encoding="utf-8")[:6000]
        combined = f"{cand.headline} {obs.page_title or ''} {' | '.join(obs.visible_claims[:10])} {raw_text}".strip()
        if not _looks_like_campaign(combined):
            if runtime_db:
                runtime_db.add_dead_letter(
                    job_id=job_id,
                    stage="extract_filter",
                    record_id=obs.observation_id,
                    error_message="Rejected by deterministic campaign filter.",
                    payload={
                        "source_url": obs.source_url,
                        "page_title": obs.page_title,
                        "source_quality_label": (qa.source_quality_label if qa else "unknown"),
                    },
                )
            continue
        dates = DATE_RE.findall(combined)
        benefit = _infer_benefit(combined)
        campaign = Campaign(
            campaign_id=f"camp_{stamp}_{i:03d}",
            institution_id=cand.institution_id,
            campaign_name=(obs.page_title or cand.headline or cand.source_url)[:180],
            campaign_type=normalize_campaign_type(combined),
            source_url=cand.source_url,
            status="review",
            confidence_final=0.0,
            evidence_refs=[obs.observation_id] + [a.path for a in obs.artifacts],
            benefit=benefit,
            audience="clientes em geral",
            source_type=cand.source_type,
            regulation_url=None,
            start_date=dates[0] if len(dates) >= 1 else None,
            end_date=dates[1] if len(dates) >= 2 else None,
            validation_notes="Ainda nao validada.",
            channels=["site_oficial" if cand.source_type == "official_site" else "instagram_publico"],
        )
        fp = _fingerprint(campaign.institution_id, campaign.source_url, campaign.campaign_name)
        if runtime_db and not runtime_db.register_fingerprint(fp, job_id=job_id, campaign_id=campaign.campaign_id):
            continue
        campaign = normalize_campaign(campaign)
        validate_model_against_schema(campaign)
        campaign_path = constants.CAMPAIGNS_DIR / f"{campaign.campaign_id}.json"
        write_model(campaign_path, campaign)
        campaigns.append(campaign)
        if runtime_db:
            runtime_db.index_artifact(
                job_id=job_id,
                entity_type="campaign",
                entity_id=campaign.campaign_id,
                artifact_type="campaign_json",
                path=_to_rel(campaign_path),
                meta={"status": campaign.status},
            )
    _write_handoff(job_id, trace_id=trace, task="extract_to_validate", source_agent="extract", target_agent="validate", input_refs=[_to_rel(constants.CAMPAIGNS_DIR / f"{x.campaign_id}.json") for x in campaigns], runtime_db=runtime_db, attempt=attempt)
    return campaigns


def validate_campaigns(job_id: str, campaigns: list[Campaign], observations: list[Observation], scoring_rules: dict, *, runtime_db: RuntimeDB | None = None, trace_id: str | None = None, attempt: int = 1) -> list[Campaign]:
    trace = trace_id or f"{job_id}:validate"
    by_obs = {o.observation_id: o for o in observations}
    out: list[Campaign] = []
    for camp in campaigns:
        has_screenshot = False
        for ref in camp.evidence_refs:
            obs = by_obs.get(ref)
            if obs and any(a.type.startswith("screenshot") for a in obs.artifacts):
                has_screenshot = True
                break
        primary, critic, final = validate_campaign_two_pass(camp, scoring_rules, has_screenshot=has_screenshot)
        validate_model_against_schema(final)
        write_model(constants.CAMPAIGNS_DIR / f"{final.campaign_id}.json", final)
        out.append(final)
        if runtime_db:
            runtime_db.add_agent_message(job_id=job_id, trace_id=trace, source_agent="validator_primary", target_agent="validator_critic", message_type=("validator_consensus" if primary.status == critic.status else "validator_divergence"), body={"campaign_id": camp.campaign_id, "primary_status": primary.status, "critic_status": critic.status, "final_status": final.status})
    _write_handoff(job_id, trace_id=trace, task="validate_to_report", source_agent="validate", target_agent="report", input_refs=[_to_rel(constants.CAMPAIGNS_DIR / f"{x.campaign_id}.json") for x in out], runtime_db=runtime_db, attempt=attempt)
    return out


def generate_report(job_id: str, campaigns: list[Campaign], report_settings: dict, *, runtime_db: RuntimeDB | None = None, trace_id: str | None = None, attempt: int = 1) -> dict[str, Path]:
    report = build_report(campaigns, report_settings, report_id=f"report_{job_id}")
    paths = save_report_files(report, constants.REPORTS_DIR)
    if runtime_db:
        runtime_db.index_artifact(job_id=job_id, entity_type="report", entity_id=report.report_id, artifact_type="report_json", path=_to_rel(paths["json"]), meta={})
        runtime_db.index_artifact(job_id=job_id, entity_type="report", entity_id=report.report_id, artifact_type="report_markdown", path=_to_rel(paths["markdown"]), meta={})
        runtime_db.index_artifact(job_id=job_id, entity_type="report", entity_id=report.report_id, artifact_type="report_html", path=_to_rel(paths["html"]), meta={})
    _write_handoff(job_id, trace_id=(trace_id or f"{job_id}:report"), task="report_to_sender", source_agent="report", target_agent="sender", input_refs=[_to_rel(paths["json"]), _to_rel(paths["markdown"]), _to_rel(paths["html"])], runtime_db=runtime_db, attempt=attempt)
    return paths


def _run_with_retry(runtime_db: RuntimeDB, job_id: str, stage: str, max_attempts: int, backoff_base: int, fn: Callable[[int], T]) -> T:
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = fn(attempt)
            runtime_db.log_run(job_id=job_id, stage=stage, attempt=attempt, status="success")
            return result
        except Exception as exc:  # noqa: PERF203
            last_exc = exc
            backoff = int(backoff_base * (2 ** (attempt - 1)))
            runtime_db.log_run(job_id=job_id, stage=stage, attempt=attempt, status="failed", error_message=str(exc), backoff_seconds=backoff)
            if attempt == max_attempts:
                runtime_db.add_dead_letter(job_id=job_id, stage=stage, error_message=str(exc), payload={"attempt": attempt})
            else:
                time.sleep(min(backoff, 2))
    raise RuntimeError(f"Stage {stage} failed after {max_attempts} attempts") from last_exc


def _can_send(runtime_db: RuntimeDB, job_id: str) -> bool:
    runtime_db.ensure_approval(job_id)
    return runtime_db.get_approval_status(job_id) == "approved"


def run_autonomous_cycle(job_id: str, *, send_report_email: bool = False, recipients: list[str] | None = None, routing_overrides: dict[str, Any] | None = None, autonomous: bool = True) -> ManualCycleResult:
    ensure_project_structure()
    cfg = load_configs()
    routing = dict(cfg["routing"])
    if routing_overrides:
        routing.update(routing_overrides)
    max_attempts = max(1, int(routing.get("max_stage_attempts", 3)))
    backoff = max(1, int(routing.get("retry_backoff_base_seconds", 2)))
    trace = f"{job_id}:{stamp_for_id()}"
    with RuntimeDB() as db:
        db.upsert_job(job_id, status="running", mode=("autonomous" if autonomous else "manual"), config=routing)
        db.ensure_approval(job_id)
        db.add_agent_message(job_id=job_id, trace_id=trace, source_agent="maestro", target_agent="all", message_type="cycle_start", body={"models": cfg.get("agent_models", {}), "routing": routing})
        try:
            candidates = _run_with_retry(db, job_id, "discover", max_attempts, backoff, lambda a: discover_candidates(job_id, cfg["institutions"], routing, runtime_db=db, trace_id=f"{trace}:discover", attempt=a))
            observations, quality = _run_with_retry(
                db,
                job_id,
                "capture",
                max_attempts,
                backoff,
                lambda a: capture_observations(
                    job_id,
                    candidates,
                    capture_timeout_seconds=int(routing.get("capture_timeout_seconds", 12)),
                    runtime_db=db,
                    trace_id=f"{trace}:capture",
                    attempt=a,
                    block_institutional=bool(routing.get("block_institutional_sources", True)),
                ),
            )
            extracted = _run_with_retry(
                db,
                job_id,
                "extract",
                max_attempts,
                backoff,
                lambda a: extract_campaigns(
                    job_id,
                    candidates,
                    observations,
                    quality_by_obs=quality,
                    runtime_db=db,
                    trace_id=f"{trace}:extract",
                    attempt=a,
                    min_capture_quality_score=float(routing.get("min_capture_quality_score_extract", 0.55)),
                ),
            )
            validated = _run_with_retry(db, job_id, "validate", max_attempts, backoff, lambda a: validate_campaigns(job_id, extracted, observations, cfg["scoring"], runtime_db=db, trace_id=f"{trace}:validate", attempt=a))
            unique_campaigns, groups = dedupe_campaigns(validated)
            report_paths = _run_with_retry(db, job_id, "report", max_attempts, backoff, lambda a: generate_report(job_id, unique_campaigns, cfg["report"], runtime_db=db, trace_id=f"{trace}:report", attempt=a))
            email_sent = False
            if send_report_email:
                if _can_send(db, job_id):
                    to = recipients or cfg["email"].get("default_recipients", [])
                    subject = f"{cfg['report'].get('subject_prefix', 'Monitor diario')} - {job_id}"
                    send_email(html_path=str(report_paths["html"]), subject=subject, recipients=to, smtp_host=cfg["email"].get("smtp_host", "smtp.gmail.com"), smtp_port=int(cfg["email"].get("smtp_port", 587)), use_tls=bool(cfg["email"].get("use_tls", True)))
                    email_sent = True
                    db.log_run(job_id=job_id, stage="sender", attempt=1, status="success")
                else:
                    db.log_run(job_id=job_id, stage="sender", attempt=1, status="blocked", error_message="approval_status != approved")
            useful = sum(1 for q in quality.values() if q.capture_quality_score >= 0.6)
            summary = {
                "job_id": job_id,
                "created_at": iso_now_tz(),
                "counts": {"candidates": len(candidates), "observations": len(observations), "campaigns_validated": len(validated), "campaigns_after_dedupe": len(unique_campaigns)},
                "quality": {"screenshot_useful_ratio": round((useful / len(quality)) if quality else 0.0, 3), "blocked_observations": sum(1 for q in quality.values() if q.should_block)},
                "dedupe_groups": groups,
                "report_files": {k: _to_rel(v) for k, v in report_paths.items()},
                "approval_status": db.get_approval_status(job_id),
                "email_sent": email_sent,
                "browser_binary": str(_find_browser()) if _find_browser() else None,
                "routing_used": routing,
            }
            write_json(constants.JOBS_DIR / f"{job_id}_summary.json", summary)
            db.set_job_status(job_id, "completed")
            db.add_agent_message(job_id=job_id, trace_id=trace, source_agent="maestro", target_agent="all", message_type="cycle_end", body={"status": "completed", "campaigns": len(unique_campaigns), "email_sent": email_sent})
            return ManualCycleResult(job_id=job_id, candidates=candidates, observations=observations, campaigns=unique_campaigns, dedupe_groups=groups, report_paths=report_paths, email_sent=email_sent)
        except Exception as exc:  # noqa: PERF203
            db.set_job_status(job_id, "failed", last_error=str(exc))
            db.add_dead_letter(job_id=job_id, stage="maestro", error_message=str(exc), payload={"trace_id": trace})
            raise


def run_manual_cycle(job_id: str, *, send_report_email: bool = False, recipients: list[str] | None = None, routing_overrides: dict[str, Any] | None = None) -> ManualCycleResult:
    return run_autonomous_cycle(job_id, send_report_email=send_report_email, recipients=recipients, routing_overrides=routing_overrides, autonomous=False)


def load_campaigns_from_disk() -> list[Campaign]:
    return [Campaign.model_validate(read_json(path)) for path in list_json_files(constants.CAMPAIGNS_DIR)]

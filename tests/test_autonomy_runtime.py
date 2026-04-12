from __future__ import annotations

from pathlib import Path

from app import constants
from app.models import Campaign, Candidate, Observation
from app.orchestrator import run_autonomous_cycle
from app.runtime_db import RuntimeDB


def test_autonomous_cycle_blocks_send_without_approval(monkeypatch) -> None:
    candidate = Candidate(
        candidate_id="cand_t1",
        institution_id="itau",
        source_type="official_site",
        source_url="https://itau.com.br/promocoes",
        headline="Oferta cartão",
        discovered_at="2026-04-10T10:00:00-03:00",
        confidence_initial=0.8,
    )
    observation = Observation(
        observation_id="obs_t1",
        candidate_id="cand_t1",
        captured_at="2026-04-10T10:01:00-03:00",
        source_url="https://itau.com.br/promocoes",
        visible_claims=["Oferta com cashback"],
        artifacts=[],
        page_title="Oferta cartão",
    )
    campaign = Campaign(
        campaign_id="camp_t1",
        institution_id="itau",
        campaign_name="Oferta cartão",
        campaign_type="cashback",
        source_url="https://itau.com.br/promocoes",
        status="validated_with_reservations",
        confidence_final=0.8,
        evidence_refs=["obs_t1"],
        source_type="official_site",
        benefit="Cashback",
    )

    def fake_discover(*args, **kwargs):
        return [candidate]

    def fake_capture(*args, **kwargs):
        return [observation], {}

    def fake_extract(*args, **kwargs):
        return [campaign]

    def fake_validate(*args, **kwargs):
        return [campaign]

    def fake_report(job_id, campaigns, settings, **kwargs):
        constants.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        md = constants.REPORTS_DIR / f"report_{job_id}.md"
        html = constants.REPORTS_DIR / f"report_{job_id}.html"
        js = constants.REPORTS_DIR / f"report_{job_id}.json"
        md.write_text("# ok\n", encoding="utf-8")
        html.write_text("<html>ok</html>", encoding="utf-8")
        js.write_text("{}", encoding="utf-8")
        return {"json": js, "markdown": md, "html": html}

    monkeypatch.setattr("app.orchestrator.discover_candidates", fake_discover)
    monkeypatch.setattr("app.orchestrator.capture_observations", fake_capture)
    monkeypatch.setattr("app.orchestrator.extract_campaigns", fake_extract)
    monkeypatch.setattr("app.orchestrator.validate_campaigns", fake_validate)
    monkeypatch.setattr("app.orchestrator.generate_report", fake_report)

    job_id = "autonomy_test_job"
    result = run_autonomous_cycle(job_id, send_report_email=True, autonomous=True)
    assert result.email_sent is False
    with RuntimeDB() as db:
        assert db.get_approval_status(job_id) == "pending"


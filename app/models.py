from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Artifact(BaseModel):
    type: str
    path: str


class Candidate(BaseModel):
    candidate_id: str
    institution_id: str
    source_type: Literal["official_site", "social_official", "search_result", "third_party"]
    source_url: str
    headline: str
    discovered_at: str
    confidence_initial: float = Field(ge=0, le=1)
    summary: str | None = None
    notes: str | None = None


class Observation(BaseModel):
    observation_id: str
    candidate_id: str
    captured_at: str
    source_url: str
    visible_claims: list[str]
    artifacts: list[Artifact]
    page_title: str | None = None
    raw_html_path: str | None = None
    raw_text_path: str | None = None


class Campaign(BaseModel):
    campaign_id: str
    institution_id: str
    campaign_name: str
    campaign_type: str
    source_url: str
    status: Literal["validated", "validated_with_reservations", "review", "discarded"]
    confidence_final: float = Field(ge=0, le=1)
    evidence_refs: list[str]
    benefit: str | None = None
    audience: str | None = None
    source_type: str | None = None
    regulation_url: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    validation_notes: str | None = None
    channels: list[str] = Field(default_factory=list)


class ReportSection(BaseModel):
    title: str
    items: list[dict]


class Report(BaseModel):
    report_id: str
    generated_at: str
    summary: str
    sections: list[ReportSection]


class Handoff(BaseModel):
    job_id: str
    task: str
    source_agent: str
    target_agent: str
    input_refs: list[str]
    created_at: str
    priority: str | None = None
    notes: str | None = None

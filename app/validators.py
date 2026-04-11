from __future__ import annotations

from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from app import constants
from app.io_utils import read_json
from app.models import Campaign, Candidate, Handoff, Observation, Report

MODEL_TO_SCHEMA = {
    Candidate: "candidate.schema.json",
    Observation: "observation.schema.json",
    Campaign: "campaign.schema.json",
    Report: "report.schema.json",
    Handoff: "handoff.schema.json",
}


def load_schema(schema_name: str) -> dict[str, Any]:
    schema_path = constants.SCHEMAS_DIR / schema_name
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema nao encontrado: {schema_path}")
    return read_json(schema_path)


def validate_json_against_schema(data: dict[str, Any], schema_name: str) -> None:
    schema = load_schema(schema_name)
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: e.path)
    if errors:
        details = "; ".join(error.message for error in errors)
        raise ValueError(f"JSON invalido para {schema_name}: {details}")


def validate_model_against_schema(model: Any) -> None:
    model_type = type(model)
    schema_name = MODEL_TO_SCHEMA.get(model_type)
    if not schema_name:
        raise ValueError(f"Schema nao mapeado para {model_type.__name__}")
    validate_json_against_schema(model.model_dump(mode="json", exclude_none=True), schema_name)


def validate_file(path: Path, schema_name: str) -> None:
    payload = read_json(path)
    validate_json_against_schema(payload, schema_name)

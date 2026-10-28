"""Tool catalogue as JSON Schemas, and the strict validator that sits between a model and any executor."""

from __future__ import annotations

import json
from datetime import date

from jsonschema import Draft202012Validator, FormatChecker

TOOLS: dict[str, dict] = {
    "get_weather": {"type": "object", "additionalProperties": False, "required": ["city"],
        "properties": {"city": {"type": "string", "minLength": 2, "maxLength": 60}, "unit": {"enum": ["celsius", "fahrenheit"]}}},
    "set_timer": {"type": "object", "additionalProperties": False, "required": ["minutes"],
        "properties": {"minutes": {"type": "integer", "minimum": 1, "maximum": 1440}, "label": {"type": "string", "maxLength": 40}}},
    "send_email": {"type": "object", "additionalProperties": False, "required": ["to", "subject"],
        "properties": {"to": {"type": "string", "format": "email"}, "subject": {"type": "string", "minLength": 1, "maxLength": 120}}},
    "search_flights": {"type": "object", "additionalProperties": False, "required": ["origin", "destination", "date"],
        "properties": {"origin": {"type": "string", "pattern": "^[A-Z]{3}$"}, "destination": {"type": "string", "pattern": "^[A-Z]{3}$"}, "date": {"type": "string", "format": "date"}}},
    "convert_currency": {"type": "object", "additionalProperties": False, "required": ["amount", "from_currency", "to_currency"],
        "properties": {"amount": {"type": "number", "exclusiveMinimum": 0}, "from_currency": {"type": "string", "pattern": "^[A-Z]{3}$"}, "to_currency": {"type": "string", "pattern": "^[A-Z]{3}$"}}},
    "create_event": {"type": "object", "additionalProperties": False, "required": ["title", "date"],
        "properties": {"title": {"type": "string", "minLength": 1, "maxLength": 80}, "date": {"type": "string", "format": "date"}}},
}  # fmt: skip
CALL_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["name", "arguments"],
    "properties": {"name": {"enum": sorted(TOOLS)}, "arguments": {"type": "object"}},
}  # fmt: skip
_CALL = Draft202012Validator(CALL_SCHEMA)
_ARGS = {n: Draft202012Validator(s, format_checker=FormatChecker()) for n, s in TOOLS.items()}


def _real_date(s: str) -> bool:
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def validate_call(call: dict) -> list[str]:
    """Errors for a parsed call (empty = valid). Dates must be real calendar dates, not just date-shaped."""
    errs = [e.message for e in _CALL.iter_errors(call)]
    if errs:
        return errs
    errs = [e.message for e in _ARGS[call["name"]].iter_errors(call["arguments"])]
    d = call["arguments"].get("date")
    if isinstance(d, str) and "date" in TOOLS[call["name"]]["properties"] and not _real_date(d):
        errs.append(f"{d!r} is not a calendar date")
    return errs


def parse_tool_call(text: str) -> tuple[dict | None, list[str]]:
    """Strict gate for raw model output: JSON object -> schema -> arguments. Never repairs, never guesses."""
    if len(text) > 4000:
        return None, ["output too long"]
    try:
        call = json.loads(text)
    except json.JSONDecodeError as e:
        return None, [f"not valid JSON: {e.msg}"]
    if not isinstance(call, dict):
        return None, ["JSON is not an object"]
    errs = validate_call(call)
    return (call if not errs else None), errs

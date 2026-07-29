"""Persona persistence + system-prompt rendering for the companion.

The persona is the **single source of truth** for the companion's voice
and behavior. It is user-private, only mutated via explicit user action,
and rendered into ``Persona.system_prompt_extras`` — the snippet the
chat pipeline prepends to every LLM system prompt (see design.md §7.1).
"""

import json
from typing import Any

from modules.companion import Persona
from sqlalchemy.orm import Session

# Required keys + per-key validation. Listed in the order they should
# appear in the rendered system prompt — order is part of the contract
# downstream prompt consumers reason about.
_REQUIRED_FIELDS: tuple[str, ...] = ("name", "personality", "speaking_style")
_OPTIONAL_FIELDS: tuple[str, ...] = ("appearance", "pronouns", "background", "boundaries")
_KNOWN_FIELDS: frozenset[str] = frozenset(_REQUIRED_FIELDS + _OPTIONAL_FIELDS)
_MAX_FIELD_LEN: int = 500


class PersonaValidationError(ValueError):
    """Raised when a persona payload is missing required fields or
    contains unknown keys. ``field`` is the offending field name when
    known; ``None`` for structural errors (e.g. not-a-dict)."""


def _validate_definition(definition: dict[str, Any]) -> dict[str, str]:
    if not isinstance(definition, dict):
        raise PersonaValidationError("persona definition must be an object")
    cleaned: dict[str, str] = {}
    for key, value in definition.items():
        if key not in _KNOWN_FIELDS:
            raise PersonaValidationError(f"unknown persona field: {key!r}", key)
        if not isinstance(value, str):
            raise PersonaValidationError(f"persona.{key} must be a string", key)
        stripped = value.strip()
        if not stripped:
            raise PersonaValidationError(f"persona.{key} must be non-empty", key)
        cleaned[key] = stripped[:_MAX_FIELD_LEN]
    for key in _REQUIRED_FIELDS:
        if key not in cleaned:
            raise PersonaValidationError(f"persona.{key} is required", key)
    return cleaned


def get_or_create_persona(db: Session, user_id: int) -> Persona:
    persona = db.query(Persona).filter(Persona.user_id == user_id).one_or_none()
    if persona is None:
        persona = Persona(user_id=user_id, definition_json="{}", system_prompt_extras="")
        db.add(persona)
        db.commit()
        db.refresh(persona)
    return persona


def update_persona(db: Session, user_id: int, definition: dict[str, Any]) -> Persona:
    cleaned = _validate_definition(definition)
    persona = get_or_create_persona(db, user_id)
    persona.definition_json = json.dumps(cleaned, ensure_ascii=False)
    persona.system_prompt_extras = render_extras(cleaned)
    persona.is_complete = True
    db.commit()
    db.refresh(persona)
    return persona


def build_system_prompt_extras(persona: Persona | None) -> str:
    """Return the rendered snippet to inject into the chat system prompt.

    Returns an empty string when the persona is missing or incomplete so
    callers can unconditionally prepend the value without a guard.
    """
    if persona is None or not persona.is_complete or not persona.system_prompt_extras:
        return ""
    return persona.system_prompt_extras


def render_extras(definition: dict[str, str]) -> str:
    """Render the persona fields into the prompt snippet injected into
    every chat turn. Field order is fixed (see ``_REQUIRED_FIELDS``) so
    downstream prompt-consumers see a stable shape."""
    lines = ["# Companion persona"]
    for key in _REQUIRED_FIELDS + _OPTIONAL_FIELDS:
        if key in definition:
            label = key.replace("_", " ").capitalize()
            lines.append(f"- **{label}**: {definition[key]}")
    return "\n".join(lines)

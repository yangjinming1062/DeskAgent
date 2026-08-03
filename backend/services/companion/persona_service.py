import json
from typing import Any

from components import safe_json_loads
from modules.companion import Persona
from sqlalchemy.orm import Session

from .memory_bootstrap import extract_user_profile
from .memory_bootstrap import record_user_profile

# Persona field order — part of the contract downstream prompt consumers
# reason about, since it dictates the rendered system-prompt snippet shape.
_REQUIRED_FIELDS: tuple[str, ...] = ("name", "personality", "speaking_style")
_OPTIONAL_FIELDS: tuple[str, ...] = ("appearance", "background", "biological_type", "gender")
_KNOWN_FIELDS: frozenset[str] = frozenset(_REQUIRED_FIELDS + _OPTIONAL_FIELDS)
_MAX_FIELD_LEN: int = 500

# Onboarding raw-answer fields, in question order. Stored as a draft in
# ``Persona.definition_json`` while ``is_complete`` is False; user_* reach
# Memory via ``update_persona`` server-side routing. ``voice`` rides the
# draft for breakpoint recovery but is not a persona field.
ONBOARDING_FIELDS: tuple[str, ...] = (
    "name",
    "species",
    "character_gender",
    "appearance",
    "role",
    "personality",
    "user_call_name",
    "user_gender",
    "user_age_bucket",
    "user_hobbies",
    "user_freeform",
    "voice",
)
_ONBOARDING_MAX_LEN: int = 2000


class PersonaValidationError(ValueError):
    """``field`` is the offending field name when known; ``None`` for
    structural errors (e.g. not-a-dict)."""


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
    """Look up the user's persona, or stage an insert for one if none
    exists. P1-16: does NOT ``db.commit()`` so the caller can keep the
    whole ``user_profile + persona`` write in a single transaction
    (ARCH §7.5 single-PUT dual-write contract). The previous version
    committed here, which would commit any unflushed Memory rows from
    ``record_user_profile`` and create a half-write state if the
    follow-up commit failed.
    """
    persona = db.query(Persona).filter(Persona.user_id == user_id).one_or_none()
    if persona is None:
        persona = Persona(user_id=user_id, definition_json="{}", system_prompt_extras="")
        db.add(persona)
        db.flush()
    return persona


def update_persona(db: Session, user_id: int, definition: dict[str, Any]) -> Persona:
    user_profile = extract_user_profile(definition)
    persona_def = {k: v for k, v in definition.items() if not k.startswith("user_")}
    cleaned = _validate_definition(persona_def)
    record_user_profile(db, user_id, user_profile)
    persona = get_or_create_persona(db, user_id)
    persona.definition_json = json.dumps(cleaned, ensure_ascii=False)
    persona.system_prompt_extras = render_extras(cleaned)
    persona.is_complete = True
    # P1-16: single commit lands both the Memory rows from
    # ``record_user_profile`` and the persona mutation atomically. The
    # prior implementation committed during ``get_or_create_persona``,
    # which would persist Memory even if this later commit failed.
    db.commit()
    db.refresh(persona)
    return persona


def build_system_prompt_extras(persona: Persona | None) -> str:
    """Empty string when persona is missing or incomplete so callers can
    unconditionally prepend the value without a guard."""
    if persona is None or not persona.is_complete or not persona.system_prompt_extras:
        return ""
    return persona.system_prompt_extras


def render_extras(definition: dict[str, str]) -> str:
    """Render the persona fields into the prompt snippet. Field order is
    fixed (see ``_REQUIRED_FIELDS``) so downstream prompt-consumers see
    a stable shape."""
    lines = ["# Companion persona"]
    for key in _REQUIRED_FIELDS + _OPTIONAL_FIELDS:
        if key in definition:
            label = key.replace("_", " ").capitalize()
            lines.append(f"- **{label}**: {definition[key]}")
    return "\n".join(lines)


def _load_draft(persona: Persona) -> dict[str, str]:
    draft = safe_json_loads(persona.definition_json or "{}", default={})
    return draft if isinstance(draft, dict) else {}


def get_onboarding_state(db: Session, user_id: int) -> dict[str, Any]:
    """``answers``: every field already submitted; ``next_field``: first
    unanswered in order (``None`` when all answered); ``complete`` mirrors
    ``Persona.is_complete`` so the desktop can skip onboarding on boot."""
    persona = get_or_create_persona(db, user_id)
    if persona.is_complete:
        return {"answers": {}, "next_field": None, "complete": True}
    draft = _load_draft(persona)
    next_field = next((f for f in ONBOARDING_FIELDS if not draft.get(f)), None)
    return {"answers": draft, "next_field": next_field, "complete": False}


def submit_onboarding_field(db: Session, user_id: int, field: str, value: str | None) -> dict[str, Any]:
    """Persist one onboarding answer incrementally. ``None``/empty clears
    the field (lets the user redo a question). Returns the post-submit
    state so the desktop gets ``next_field`` without a separate round-trip.

    P1-7 (backend audit): once the persona is finalized
    (``is_complete=True``) the draft is supposed to be frozen — the
    canonical persona lives in the explicit columns and the system
    prompt. The previous code would still accept \`onboarding.submit\`
    and silently rewrite \`definition_json\`; the avatar / system
    prompt would then read the polluted draft and the user would
    see "old persona, new image / new system prompt" without an
    error. Reject with \`PersonaValidationError\` (which the JSON-RPC
    handler maps to -32602 Invalid Params) so the desktop gets a
    clear "persona already finalized" message.
    """
    if field not in ONBOARDING_FIELDS:
        raise PersonaValidationError(f"unknown onboarding field: {field!r}", field)
    persona = get_or_create_persona(db, user_id)
    if persona.is_complete:
        raise PersonaValidationError(
            f"onboarding field {field!r} cannot be edited after persona is finalized; use PUT /api/companion/persona",
            field,
        )
    draft = _load_draft(persona)
    if value and value.strip():
        draft[field] = value.strip()[:_ONBOARDING_MAX_LEN]
    else:
        draft.pop(field, None)
    persona.definition_json = json.dumps(draft, ensure_ascii=False)
    db.commit()
    next_field = next((f for f in ONBOARDING_FIELDS if not draft.get(f)), None)
    return {"answers": draft, "next_field": next_field, "complete": False}

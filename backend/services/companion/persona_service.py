import json
from typing import Any

from components import safe_json_loads
from modules.companion import Persona
from services.llm import chat as default_chat
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .memory_bootstrap import extract_user_profile
from .memory_bootstrap import read_user_profile
from .memory_bootstrap import record_user_profile
from .personality_tagger import analyze_personality_tags
from .personality_tagger import ChatFn as _ChatFn

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
    "speaking_style",
    "user_call_name",
    "user_gender",
    "user_age_bucket",
    "user_hobbies",
    "user_freeform",
    "voice",
)
_ONBOARDING_MAX_LEN: int = 2000

# Fields collected in q-user / voice after is_complete=True; gating complete on these prevents skip-on-crash.
_POST_CHARACTER_FIELDS: tuple[str, ...] = (
    "user_call_name",
    "user_gender",
    "user_age_bucket",
    "user_hobbies",
    "user_freeform",
)

# Post-finalization fields that go to draft (not Memory); speaking_style lands here because q-user collects it.
_POST_FINALIZATION_DRAFT_FIELDS: frozenset[str] = frozenset({"voice", "speaking_style"})


class PersonaValidationError(ValueError):
    """``field`` is the offending field name when known; ``None`` for
    structural errors (e.g. not-a-dict)."""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


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
    exists. Does NOT ``db.commit()`` so the caller can keep the whole
    ``user_profile + persona`` write in a single transaction (ARCH §7.5
    single-PUT dual-write contract) — committing here would flush any
    uncommitted Memory rows from ``record_user_profile`` and create a
    half-write state if the follow-up commit failed.
    """
    persona = db.query(Persona).filter(Persona.user_id == user_id).one_or_none()
    if persona is None:
        persona = Persona(user_id=user_id, definition_json="{}", system_prompt_extras="")
        db.add(persona)
        db.flush()
    return persona


def update_persona(
    db: Session,
    user_id: int,
    definition: dict[str, Any],
) -> Persona:
    if not isinstance(definition, dict):
        raise PersonaValidationError("persona definition must be an object")
    user_profile = extract_user_profile(definition)
    persona_def = {k: v for k, v in definition.items() if not k.startswith("user_")}
    cleaned = _validate_definition(persona_def)
    record_user_profile(db, user_id, user_profile)
    persona = get_or_create_persona(db, user_id)
    persona.definition_json = json.dumps(cleaned, ensure_ascii=False)
    persona.system_prompt_extras = render_extras(cleaned)
    persona.is_complete = True
    # Retry on the partial-unique race; record_user_profile is idempotent.
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        record_user_profile(db, user_id, user_profile)
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
    """``answers``: every field already submitted; ``next_field``: first unanswered (``None`` when all answered).

    ``complete`` is gated on user_* + voice being answered, not just is_complete, so a crash mid-flow resumes rather than skips.
    """
    persona = get_or_create_persona(db, user_id)
    if persona.is_complete:
        draft = _load_draft(persona)
        user_profile = read_user_profile(db, user_id)
        missing_users = [k for k in _POST_CHARACTER_FIELDS if not user_profile.get(k)]
        voice_missing = not draft.get("voice")
        if missing_users or voice_missing:
            next_field = missing_users[0] if missing_users else "voice"
            # Merge draft + Memory so the desktop rehydrates every answered field in one shot.
            merged = {**draft, **user_profile}
            return {"answers": merged, "next_field": next_field, "complete": False}
        return {"answers": {}, "next_field": None, "complete": True}
    draft = _load_draft(persona)
    next_field = next((f for f in ONBOARDING_FIELDS if not draft.get(f)), None)
    return {"answers": draft, "next_field": next_field, "complete": False}


def submit_onboarding_field(db: Session, user_id: int, field: str, value: str | None) -> dict[str, Any]:
    """Persist one onboarding answer. After is_complete=True, only user_*/voice/speaking_style remain editable; character fields require PUT /persona."""
    if field not in ONBOARDING_FIELDS:
        raise PersonaValidationError(f"unknown onboarding field: {field!r}", field)
    persona = get_or_create_persona(db, user_id)
    if persona.is_complete:
        # Post-character fields accepted here; see single-PUT dual-write contract.
        if field.startswith("user_"):
            if value and value.strip():
                record_user_profile(
                    db,
                    user_id,
                    {field: value.strip()[:_ONBOARDING_MAX_LEN]},
                )
                db.commit()
            # Empty value: leave the Memory row alone so revocation stays the
            # only path that wipes a user_* entry (memory_forget).
            return {
                "answers": _load_draft(persona),
                "next_field": None,
                "complete": True,
            }
        if field in _POST_FINALIZATION_DRAFT_FIELDS:
            draft = _load_draft(persona)
            if value and value.strip():
                draft[field] = value.strip()[:_ONBOARDING_MAX_LEN]
            elif field == "speaking_style":
                # speaking_style is a required persona field — refusing to clear
                # it keeps definition_json + system_prompt_extras consistent.
                # Re-validate the would-be draft so the error references the
                # offending field rather than the whole persona.
                _validate_definition(draft)
                raise PersonaValidationError("speaking_style cannot be cleared after persona is finalized", field)
            else:
                draft.pop(field, None)
            persona.definition_json = json.dumps(draft, ensure_ascii=False)
            # Re-render extras so the rendered prompt reflects the post-edit
            # definition (system_prompt_extras was set once in update_persona
            # and would otherwise drift from definition_json).
            persona.system_prompt_extras = render_extras(draft)
            db.commit()
            return {"answers": draft, "next_field": None, "complete": True}
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

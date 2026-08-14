import json
from typing import Any

from components import SETTINGS, safe_json_loads
from modules.companion import AvatarAsset, Persona
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .memory_bootstrap import extract_user_profile, read_user_profile, record_user_profile

# Persona field order — part of the contract downstream prompt consumers
# reason about, since it dictates the rendered system-prompt snippet shape.
_REQUIRED_FIELDS: tuple[str, ...] = ("name", "personality", "speaking_style")
# Split from "appearance" so the locked-vs-outfit split is first-class:
# `assemblePersona` preserves `appearance_core` across edits; outfit stays editable.
_OPTIONAL_FIELDS: tuple[str, ...] = ("appearance_core", "appearance_outfit", "background", "biological_type", "gender")
_KNOWN_FIELDS: frozenset[str] = frozenset(_REQUIRED_FIELDS + _OPTIONAL_FIELDS)
_MAX_FIELD_LEN: int = 500

# Onboarding raw-answer fields, in question order: character sub-stage
# (name..speaking_style) → 形象确认 → voice → user sub-stage. Stored as a draft
# in ``Persona.definition_json`` while ``is_complete`` is False; user_* reach
# Memory via ``update_persona`` server-side routing. ``voice`` rides the
# draft for breakpoint recovery but is not a persona field.
#
# Note: ``appearance_outfit`` is intentionally NOT collected here — the seed
# image focuses on body silhouette. The initial outfit is derived async from
# the avatar prompt + appearance_core after portrait confirmation (see
# ``_schedule_onboarding_outfit_extraction`` in companion.py). Subsequent
# updates happen at wardrobe-equip time via ``update_outfit_field`` below,
# which swaps in the equipped item's ``outfit_description``. The field is
# LLM-normalized (see ``outfit_normalizer.py``), never raw user input.
ONBOARDING_FIELDS: tuple[str, ...] = (
    "name",
    "species",
    "character_gender",
    "appearance_core",
    "role",
    "personality",
    "speaking_style",
    "voice",
    "user_call_name",
    "user_gender",
    "user_age_bucket",
    "user_hobbies",
    "user_freeform",
)
_ONBOARDING_MAX_LEN: int = 2000

# ``voice`` splits ONBOARDING_FIELDS into the character sub-stage (before it)
# and the post-character fields (after it). Both sub-tuples derive from the
# single source of truth above so a field add/remove can't desync them.
_VOICE_FIELD_INDEX: int = ONBOARDING_FIELDS.index("voice")
_CHARACTER_ONBOARDING_FIELDS: tuple[str, ...] = ONBOARDING_FIELDS[:_VOICE_FIELD_INDEX]
# Gating is_complete on these prevents skip-on-crash resume.
_POST_CHARACTER_FIELDS: tuple[str, ...] = ONBOARDING_FIELDS[_VOICE_FIELD_INDEX + 1 :]


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


def update_persona(db: Session, user_id: int, definition: dict[str, Any]) -> Persona:
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
    persona.is_portrait_confirmed = False
    persona.portrait_confirmed_at = None
    # Retry on the partial-unique race; record_user_profile is idempotent.
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        record_user_profile(db, user_id, user_profile)
        db.commit()
    db.refresh(persona)
    return persona


def confirm_portrait(db: Session, user_id: int) -> Persona:
    persona = get_or_create_persona(db, user_id)
    persona.is_portrait_confirmed = True
    persona.portrait_confirmed_at = func.now()
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


def update_outfit_field(db: Session, user_id: int, outfit: str) -> None:
    """Swap ``appearance_outfit`` + re-render ``system_prompt_extras`` without full re-validation. Empty string clears the field."""
    persona = db.query(Persona).filter(Persona.user_id == user_id).one_or_none()
    if persona is None:
        return
    definition = _load_draft(persona)
    if outfit:
        definition["appearance_outfit"] = outfit[:_MAX_FIELD_LEN]
    else:
        definition.pop("appearance_outfit", None)
    persona.definition_json = json.dumps(definition, ensure_ascii=False)
    persona.system_prompt_extras = render_extras(definition)
    db.commit()


def _load_draft(persona: Persona) -> dict[str, str]:
    draft = safe_json_loads(persona.definition_json or "{}", default={})
    return draft if isinstance(draft, dict) else {}


def _portrait_next_field(db: Session, user_id: int) -> str:
    """Map avatar seed state to the portrait sub-stage; single mode skips right/back."""
    avatar = db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).one_or_none()
    if avatar is None or not bool(avatar.seed_front_url):
        return "portrait"
    # Single mode: stay on front even if stale right/back seeds exist (image-to-model only consumes `front`).
    if SETTINGS.fullbody_mode == "single" or not bool(avatar.seed_right_url):
        return "portrait-fullbody-front"
    if not bool(avatar.seed_back_url):
        return "portrait-fullbody-right"
    return "portrait-fullbody-back"


def _state(answers: dict, next_field: str | None, complete: bool) -> dict[str, Any]:
    return {"answers": answers, "next_field": next_field, "complete": complete, "fullbody_mode": SETTINGS.fullbody_mode}


def get_onboarding_state(db: Session, user_id: int) -> dict[str, Any]:
    """``answers``: every field already submitted; ``next_field``: first unanswered (``None`` when all answered).

    ``complete`` is gated on portrait confirmation, voice + user_* being answered, not just is_complete, so a crash mid-flow resumes rather than skips.
    ``voice`` outranks ``user_*`` because the voice sub-stage runs right after 形象确认, before the user sub-stage.
    ``fullbody_mode`` is included so the desktop knows whether to show the side/back phases.
    """
    persona = get_or_create_persona(db, user_id)
    if persona.is_complete:
        draft = _load_draft(persona)
        user_profile = read_user_profile(db, user_id)
        merged = {**draft, **user_profile}
        if not persona.is_portrait_confirmed:
            return _state(merged, _portrait_next_field(db, user_id), False)
        missing_users = [k for k in _POST_CHARACTER_FIELDS if not user_profile.get(k)]
        voice_missing = not draft.get("voice")
        if voice_missing or missing_users:
            # Merge draft + Memory so the desktop rehydrates every answered field in one shot.
            next_field = "voice" if voice_missing else missing_users[0]
            return _state(merged, next_field, False)
        return _state({}, None, True)
    draft = _load_draft(persona)
    missing_character = next((f for f in _CHARACTER_ONBOARDING_FIELDS if not draft.get(f)), None)
    if missing_character is not None:
        return _state(draft, missing_character, False)
    return _state(draft, _portrait_next_field(db, user_id), False)


def submit_onboarding_field(db: Session, user_id: int, field: str, value: str | None) -> dict[str, Any]:
    """Persist one onboarding answer. After is_complete=True, only user_*/voice remain editable; character fields (incl. speaking_style) require PUT /persona."""
    if field not in ONBOARDING_FIELDS:
        raise PersonaValidationError(f"unknown onboarding field: {field!r}", field)
    persona = get_or_create_persona(db, user_id)
    if persona.is_complete:
        # Post-character fields accepted here; see single-PUT dual-write contract.
        if field.startswith("user_"):
            if value and value.strip():
                record_user_profile(db, user_id, {field: value.strip()[:_ONBOARDING_MAX_LEN]})
                db.commit()
            # Empty value: leave the Memory row alone so revocation stays the
            # only path that wipes a user_* entry (memory_forget).
            return _state(_load_draft(persona), None, True)
        # voice is not a persona field, so the draft is the only thing that
        # moves here — system_prompt_extras stays as update_persona left it.
        if field == "voice":
            draft = _load_draft(persona)
            if value and value.strip():
                draft[field] = value.strip()[:_ONBOARDING_MAX_LEN]
            else:
                draft.pop(field, None)
            persona.definition_json = json.dumps(draft, ensure_ascii=False)
            db.commit()
            return _state(draft, None, True)
        raise PersonaValidationError(f"onboarding field {field!r} cannot be edited after persona is finalized; use PUT /api/companion/persona", field)
    draft = _load_draft(persona)
    if value and value.strip():
        draft[field] = value.strip()[:_ONBOARDING_MAX_LEN]
    else:
        draft.pop(field, None)
    persona.definition_json = json.dumps(draft, ensure_ascii=False)
    db.commit()
    missing_character = next((f for f in _CHARACTER_ONBOARDING_FIELDS if not draft.get(f)), None)
    next_field = missing_character if missing_character is not None else "portrait"
    return _state(draft, next_field, False)

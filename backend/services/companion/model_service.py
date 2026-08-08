import asyncio
import json
from pathlib import Path

from components import get_logger
from components import safe_json_loads
from components import SESSION_LOCAL
from components import SETTINGS
from modules.companion import AvatarAsset
from modules.companion import CompanionModel
from modules.companion import WardrobeItem
from modules.ws import WSEvent
from sqlalchemy.orm import Session

from ..llm import enhance_pbr_channels
from ..llm import PBR_KEYS
from ..tools.builtin import first_image_url
from ..tools.builtin import image_generation_tool
from .asset_store import build_signed_avatar_url
from .asset_store import build_signed_model_url
from .asset_store import save_companion_asset
from .asset_store import save_companion_model
from .asset_store import unlink_companion_asset
from .persona_service import get_or_create_persona
from .wardrobe_service import fetch_texture_bytes

logger = get_logger(__name__)


class ModelGenerationError(RuntimeError):
    """3D model generation failed."""


def _active_portrait(db: Session, user_id: int) -> str | None:
    a = db.query(AvatarAsset).filter(AvatarAsset.user_id == user_id, AvatarAsset.active.is_(True)).one_or_none()
    if a is None:
        return None
    # Prefer the dedicated full-body seed image; fall back to the avatar
    # (bust) for legacy rows that predate the avatar/seed split.
    url = a.seed_url or a.asset_url
    if url and url.startswith("companion-avatars/"):
        filename = url.split("/", 1)[1]
        file_id, _, ext = filename.partition(".")
        return build_signed_avatar_url(file_id, ext)
    return url


def get_active_model(db: Session, user_id: int) -> CompanionModel | None:
    return db.query(CompanionModel).filter(CompanionModel.user_id == user_id, CompanionModel.active.is_(True)).one_or_none()


def signed_model_url(model: CompanionModel | None) -> str | None:
    """Never mutates the row — an ORM write would leak the expiring URL into the next autoflush."""
    if model is None or not model.asset_url or not model.asset_url.startswith("companion-models/"):
        return None
    parts = model.asset_url.split("/", 2)
    if len(parts) != 3:
        return None
    return build_signed_model_url(int(parts[1]), parts[2])


# Maps persona biological_type → base model filename in data/base-models/.
SPECIES_MODEL_MAP: dict[str, str] = {
    "人类": "human.glb",
    "精灵": "elf.glb",
    "灵兽": "spirit_beast.glb",
    "机甲": "mecha.glb",
    "幻形": "shapeshifter.glb",
}


async def _load_base_model(db: Session, user_id: int, species_override: str | None = None) -> tuple[bytes, str]:
    """Disk read deferred to a thread — a multi-MB GLB would stall the event loop."""
    species = species_override
    if species is None:
        persona = get_or_create_persona(db, user_id)
        definition = safe_json_loads(persona.definition_json or "{}", default={})
        species = definition.get("biological_type", "人类") if isinstance(definition, dict) else "人类"

    model_file = SPECIES_MODEL_MAP.get(species, SPECIES_MODEL_MAP["人类"])
    local = Path(SETTINGS.companion_base_model_dir) / model_file

    if not local.exists():
        fallback = Path(SETTINGS.companion_base_model_dir) / "character.glb"
        if fallback.exists():
            logger.info("Species model not found, using generic fallback", extra={"species": species, "expected": str(local)})
            return await asyncio.to_thread(fallback.read_bytes), species
        raise ModelGenerationError(f"未找到 {species} 的基底模型（{local}）。请将 rigged GLB 放到该路径，或放入 {fallback} 作为通用兜底。")

    return await asyncio.to_thread(local.read_bytes), species


def _extract_morph_names_from_glb(glb_data: bytes) -> list[str]:
    """Parse a GLB file's JSON chunk to discover morph target names.
    Blender's glTF exporter stores them in meshes[].extras.targetNames."""
    if len(glb_data) < 20:
        return []
    magic = int.from_bytes(glb_data[0:4], "little")
    if magic != 0x46546C67:  # 'glTF'
        return []

    chunk_length = int.from_bytes(glb_data[12:16], "little")
    chunk_type = int.from_bytes(glb_data[16:20], "little")
    if chunk_type != 0x4E4F534A:  # 'JSON'
        return []
    # Truncated file: chunk_length is uint32 and may exceed the buffer.
    if 20 + chunk_length > len(glb_data):
        return []

    json_str = glb_data[20 : 20 + chunk_length].decode("utf-8", errors="ignore").rstrip("\x00 \n\r,")
    try:
        gltf = json.loads(json_str)
    except json.JSONDecodeError:
        return []

    names: set[str] = set()
    for mesh in gltf.get("meshes", []):
        names.update(mesh.get("extras", {}).get("targetNames", []))
        for prim in mesh.get("primitives", []):
            names.update(prim.get("extras", {}).get("targetNames", []))
    return sorted(names)


async def generate_companion_model(db: Session, *, user_id: int, species_override: str | None = None) -> CompanionModel:
    glb_data, species = await _load_base_model(db, user_id, species_override)
    morph_names = _extract_morph_names_from_glb(glb_data)

    db.query(CompanionModel).filter(CompanionModel.user_id == user_id, CompanionModel.active.is_(True)).update({"active": False})

    asset_url = save_companion_model(glb_data, user_id=user_id)
    model = CompanionModel(
        user_id=user_id,
        asset_url=asset_url,
        provider="base_texture",
        species=species,
        morph_params_json="{}",
        status="succeeded",
        has_rig=True,
        has_morph_targets=len(morph_names) > 0,
        active=True,
    )
    db.add(model)
    db.commit()
    db.refresh(model)

    _emit_model_ready(user_id, model.id, asset_url, species=species)

    asyncio.create_task(_generate_custom_textures(user_id))
    logger.info(
        "Base model served",
        extra={"user_id": user_id, "species": species, "morph_count": len(morph_names), "morphs": morph_names[:20]},
    )
    return model


# PBR channel order matches the JSON contract owned by
# ``prompt_engineer.enhance_pbr_channels``; we import rather than redeclare
# so the iteration order and the LLM contract can't drift apart.


async def _generate_one_pbr_channel(
    *,
    channel_key: str,
    prompt: str,
    user_id: int,
    reference_image: str | None,
) -> str | None:
    """Generate, download, and persist a single PBR texture channel.

    Returns the bare storage path on success; ``None`` on any failure
    (image-gen provider error, unreachable URL, disk write error). The
    caller runs all 4 channels concurrently and treats partial success
    as total failure to avoid the "albedo applied, normal still missing"
    half-PBR state.
    """
    try:
        result_json = await image_generation_tool(
            prompt=prompt,
            llm_config={},
            size="1024x1024",
            n=1,
            user_id=user_id,
            reference_image=reference_image,
        )
        src_url = first_image_url(result_json)
        if not src_url:
            return None

        data = await fetch_texture_bytes(src_url)
        if data is None:
            return None

        return save_companion_asset(data, user_id=user_id, label=f"custom_texture_{channel_key}", ext="png")
    except Exception:
        # Per-channel isolation: a transport blip on one channel must not
        # abort the other 3 concurrent generations.
        return None


async def _generate_custom_textures(user_id: int) -> None:
    try:
        with SESSION_LOCAL() as db:
            persona = get_or_create_persona(db, user_id)
            definition = safe_json_loads(persona.definition_json or "{}", default={})
            appearance = definition.get("appearance", "")
            portrait_url = _active_portrait(db, user_id)

            if not appearance and not portrait_url:
                return

            # LLM rewrites the persona's short appearance into 4 PBR-channel
            # prompts in one round-trip. The session stays open here because
            # ``enhance_pbr_channels`` reads the user's chat provider config
            # from the DB; close it before the long image-gen + download
            # phase. MissingLlmConfigError / transport errors propagate up —
            # the outer try/except logs and returns so the user's current
            # outfit stays intact.
            channels = await enhance_pbr_channels(
                db,
                user_id,
                base_description=appearance or "casual clothing",
            )

        results = await asyncio.gather(
            *(
                _generate_one_pbr_channel(
                    channel_key=key,
                    prompt=channels[key],
                    user_id=user_id,
                    # Vision reference is colour-consistency for albedo only;
                    # normal / roughness / metalness are channel maps and don't
                    # benefit from re-rendering through a portrait visual reference.
                    reference_image=portrait_url if key == "albedo" else None,
                )
                for key in PBR_KEYS
            )
        )
        # All-or-nothing: any channel failure discards the entire set so the GLB never
        # lands in an "albedo applied, normal still missing" half-PBR state. The
        # 1-3 channels that wrote files via ``save_companion_asset`` must be
        # unlinked here — no GC sweep exists, so partial failures otherwise leak
        # PNGs under companion-assets/<user_id>/.
        if any(r is None for r in results):
            logger.warning("Custom texture generation aborted (partial failure)", extra={"user_id": user_id})
            for storage_path in results:
                if storage_path is None:
                    continue
                if unlink_companion_asset(storage_path) is None:
                    logger.warning("Failed to unlink orphaned PBR asset", extra={"user_id": user_id, "path": storage_path})
            return
        albedo_url, normal_url, roughness_url, metalness_url = results

        with SESSION_LOCAL() as db:
            db.query(WardrobeItem).filter(WardrobeItem.user_id == user_id, WardrobeItem.equipped.is_(True)).update({"equipped": False})
            db.add(
                WardrobeItem(
                    user_id=user_id,
                    name="专属外观",
                    category="generated",
                    material_overrides_json="{}",
                    texture_url=albedo_url,
                    normal_url=normal_url,
                    roughness_url=roughness_url,
                    metalness_url=metalness_url,
                    prompt=appearance,
                    equipped=True,
                )
            )
            db.commit()

        emit_wardrobe_updated(user_id)
        logger.info("Custom texture (4 PBR channels) generated for user", extra={"user_id": user_id})
    except Exception:
        logger.warning("Custom texture generation failed", extra={"user_id": user_id}, exc_info=True)


# ── Shared helpers ───────────────────────────────────────────


def _emit_model_ready(user_id: int, model_id: int, asset_url: str, *, species: str | None = None) -> None:
    payload: dict = {"model_id": model_id}
    if species:
        payload["species"] = species
    parts = asset_url.split("/", 2)
    if len(parts) == 3:
        payload["asset_url"] = build_signed_model_url(int(parts[1]), parts[2])
    try:
        with SESSION_LOCAL() as db:
            db.add(WSEvent(user_id=user_id, event_type="model.ready", payload=json.dumps(payload, ensure_ascii=False)))
            db.commit()
    except Exception:
        logger.warning("Failed to emit model.ready event", exc_info=True)


def emit_wardrobe_updated(user_id: int) -> None:
    try:
        with SESSION_LOCAL() as db:
            db.add(WSEvent(user_id=user_id, event_type="wardrobe.updated", payload="{}"))
            db.commit()
    except Exception:
        logger.warning("Failed to emit wardrobe.updated event", exc_info=True)

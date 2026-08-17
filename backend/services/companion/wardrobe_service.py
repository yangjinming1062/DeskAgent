import asyncio
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from components import SESSION_LOCAL, download_capped, get_file_path, get_logger, parse_llm_json, safe_json_loads, save_file, temp_file_delete
from modules.companion import WardrobeItem, WardrobePreviewResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..llm import build_texture_prompt, chat, is_preset_species, resolve_fullbody_style
from ..tools.builtin import first_image_url, image_generation_tool
from .asset_store import build_data_uri, build_signed_asset_url, decompress_glb_if_needed, resolve_companion_model_path, save_companion_asset, unlink_companion_asset
from .avatar_service import get_active_avatar, load_avatar_bytes_as_data_uri
from .blender_tools import _vision_llm_call
from .garment_service import joint_names_from_gltf, run_garment_pipeline
from .model_service import get_active_model
from .outfit_normalizer import normalize_outfit
from .persona_service import get_or_create_persona, update_outfit_field
from .rig_type_selector import select_rig_type

logger = get_logger(__name__)

# Companion-assets URL fields on WardrobeItem for re-signing and unlinking.
_COMPANION_ASSET_URL_ATTRS: tuple[str, ...] = ("texture_url", "normal_url", "roughness_url", "metalness_url", "displacement_url", "mesh_url")

# Cache body model joint names to avoid re-reading multi-MB GLBs on each preview.
_BODY_JOINT_NAMES_CACHE: dict[str, list[str]] = {}

_VALID_SLOTS = {"outfit", "torso", "legs", "feet", "full_body", "head", "hands", "back"}
_SLOT_TEXTURE = "outfit"
_DEFAULT_SOCKET_BY_SLOT = {"head": "Head", "hands": "RightHand", "back": "Spine2"}
_PBR_CHANNELS = ["albedo", "normal", "roughness", "metalness", "displacement"]

_WARDROBE_KIND_CLASSIFIER_SYSTEM = """\
You classify user wardrobe-change intent into one of three pipelines and fill its assembly metadata.

- "texture": only color, material, fabric, pattern, or finish changes on the existing silhouette. No new shape. Examples: "换成红色", "换成丝绸质感", "变成豹纹".
- "garment": changes silhouette or replaces/adds a clothing piece. Examples: "穿一条洛丽塔蓬裙", "换成西装", "披一件斗篷", "换双马丁靴".
- "accessory": a rigid attachment that hangs on a bone socket — bags, hats, glasses, scarves, wings. Examples: "戴一顶贝雷帽", "背一个棕色皮质背包", "戴圆框眼镜".

Metadata:
- "slot" — mutual-exclusion slot (same slot replaces, different slots coexist):
  "outfit" for texture; garment: "torso"|"legs"|"feet"|"full_body"; accessory: "head"|"hands"|"back".
  A dress covering torso+legs is "full_body". Shoes/socks are "feet". Pants/skirt are "legs".
- "socket" — accessory only: the bone the item hangs on, chosen from the available bone list
  (e.g. a handbag → the hand bone; a hat → the head bone; a backpack → the upper-spine bone). null otherwise.
- "physics" — garment only: "cloth" when it has a flowing hem/loose drape (long skirts, capes, coats, dresses);
  "skin" when fitted (jackets, shirts, tight clothes, shoes). Accessories are always "skin".

Respond with a single JSON object:
{"kind": "texture"|"garment"|"accessory", "slot": <str>, "socket": <str|null>, "physics": "skin"|"cloth"}
No commentary.
"""


class WardrobeSourceExpiredError(Exception):
    """Raised when confirming a wardrobe preview whose temp-media source has expired or is missing."""


async def fetch_texture_bytes(url: str) -> bytes | None:
    """Resolve a generated-asset URL to bytes (local temp-media or remote)."""
    if "/api/media/files/" in url:
        fid = url.rsplit("/", 1)[-1].split("?")[0]
        res = get_file_path(fid)
        if not res:
            return None
        return await asyncio.to_thread(Path(res[0]).read_bytes)

    try:
        return await download_capped(url, max_bytes=50 * 1024 * 1024, timeout=120.0)
    except Exception:
        return None


def _iter_companion_asset_paths(item: WardrobeItem) -> Iterator[tuple[str, str, str]]:
    """Yield ``(attr_name, uid, filename)`` for every companion-assets URL on ``item`` (PBR channels + garment mesh)."""
    for attr in _COMPANION_ASSET_URL_ATTRS:
        url = getattr(item, attr, None)
        if not url:
            continue
        if url.startswith("companion-assets/"):
            parts = url.split("/", 2)
            if len(parts) != 3 or "/" in parts[2] or "\\" in parts[2]:
                continue
            yield attr, parts[1], parts[2]
        elif "/api/companion/asset/" in url:
            path_part = url.split("/api/companion/asset/", 1)[1].split("?")[0]
            parts = path_part.split("/", 1)
            if len(parts) == 2 and "/" not in parts[1] and "\\" not in parts[1]:
                yield attr, parts[0], parts[1]


def _re_sign_texture(item: WardrobeItem) -> None:
    """Re-sign all companion-assets URLs on the item (5-min TTL) and sanitize stale 404 URLs."""
    for attr, uid, filename in _iter_companion_asset_paths(item):
        setattr(item, attr, build_signed_asset_url(int(uid), filename))

    # If temp-media URL is present but file is expired, set attribute to None so client falls back cleanly
    for attr in _COMPANION_ASSET_URL_ATTRS:
        val = getattr(item, attr, None)
        if val and "/api/media/files/" in val:
            fid = val.rsplit("/", 1)[-1].split("?")[0]
            if get_file_path(fid) is None:
                setattr(item, attr, None)


async def check_and_recover_missing_texture(user_id: int, item: WardrobeItem) -> None:
    """Background task: If an equipped wardrobe item's texture is missing, regenerate PBR textures using its outfit_description."""
    if item.kind not in (None, "texture") and not item.texture_url:
        return
    desc = item.outfit_description or item.prompt or item.name
    if not desc:
        return

    try:
        from .model_service import emit_wardrobe_updated

        async with SESSION_LOCAL() as db:
            avatar = await get_active_avatar(db, user_id)
            ref_uri = None
            if avatar and avatar.seed_front_url:
                ref_uri = load_avatar_bytes_as_data_uri(avatar.seed_front_url)
            rig_type = await _resolve_rig_type(db, user_id)
            style = await _resolve_style(db, user_id)

            res_dict, _prompts = await _generate_pbr_channels(description=desc, feedback=None, rig_type=rig_type, reference_data_uri=ref_uri, user_id=user_id, style=style)

            async def _save_ch(ch: str, label: str) -> str | None:
                if ch not in res_dict:
                    return None
                fid = res_dict[ch][1]
                res = get_file_path(fid)
                if not res:
                    return None
                data = await asyncio.to_thread(Path(res[0]).read_bytes)
                return save_companion_asset(data, user_id=user_id, label=label, ext="png")

            t_url, n_url, r_url, m_url, d_url = await asyncio.gather(
                _save_ch("albedo", "wardrobe_texture"),
                _save_ch("normal", "wardrobe_normal"),
                _save_ch("roughness", "wardrobe_roughness"),
                _save_ch("metalness", "wardrobe_metalness"),
                _save_ch("displacement", "wardrobe_displacement"),
            )

            if t_url:
                await db.execute(
                    update(WardrobeItem)
                    .where(WardrobeItem.id == item.id)
                    .values(texture_url=t_url, normal_url=n_url, roughness_url=r_url, metalness_url=m_url, displacement_url=d_url)
                )
                await db.commit()
                logger.info("Successfully recovered and regenerated wardrobe texture from outfit description", extra={"user_id": user_id, "item_id": item.id})
                await emit_wardrobe_updated(user_id)
    except Exception as exc:
        logger.warning("Background regeneration of wardrobe texture failed", extra={"user_id": user_id, "item_id": item.id, "error": str(exc)})


async def list_wardrobe(db: AsyncSession, user_id: int) -> list[WardrobeItem]:
    items = (await db.execute(select(WardrobeItem).where(WardrobeItem.user_id == user_id).order_by(WardrobeItem.created_at))).scalars().all()
    for item in items:
        _re_sign_texture(item)
    return items


async def get_equipped_item(db: AsyncSession, user_id: int) -> WardrobeItem | None:
    """Return the most recently updated equipped item."""
    equipped = await _query_equipped(db, user_id)
    item = equipped[-1] if equipped else None
    if item:
        _re_sign_texture(item)
        if item.equipped and (item.kind in (None, "texture")) and not item.texture_url:
            asyncio.create_task(check_and_recover_missing_texture(user_id, item))
    return item


async def get_equipped_items(db: AsyncSession, user_id: int) -> list[WardrobeItem]:
    """All equipped items (multi-equip: up to one per slot), oldest first."""
    items = await _query_equipped(db, user_id)
    for item in items:
        _re_sign_texture(item)
        if item.equipped and (item.kind in (None, "texture")) and not item.texture_url:
            asyncio.create_task(check_and_recover_missing_texture(user_id, item))
    return items


async def _resolve_rig_type(db: AsyncSession, user_id: int) -> str:
    """Resolve the companion's rig type from active model or persona species."""
    model = await get_active_model(db, user_id)
    if model and model.rig_type:
        return model.rig_type

    persona = await get_or_create_persona(db, user_id)
    definition = safe_json_loads(persona.definition_json or "{}", default={})
    species = (definition.get("biological_type") or "").strip()

    if is_preset_species(species):
        return "biped"

    return await select_rig_type(chat, species or "人类", db=db, user_id=user_id)


async def _resolve_style(db: AsyncSession, user_id: int) -> str:
    """Resolve the render style from the active model; falls back to the
    species preset routing (a custom species without a model row gets the
    anime mainstream default)."""
    model = await get_active_model(db, user_id)
    if model and model.style:
        return model.style

    persona = await get_or_create_persona(db, user_id)
    definition = safe_json_loads(persona.definition_json or "{}", default={})
    species = (definition.get("biological_type") or "").strip()
    return resolve_fullbody_style(species)


@dataclass
class WardrobeRouting:
    kind: str  # texture | garment | accessory
    slot: str
    socket: str | None
    physics: str

    @classmethod
    def default(cls) -> "WardrobeRouting":
        """Classifier-failure fallback — the always-capable garment path."""
        return cls(kind="garment", slot="torso", socket=None, physics="skin")

    def assembly_json(self) -> str:
        return json.dumps(
            {
                "kind": self.kind,
                "slot": self.slot,
                "layer": 1,
                "socket": self.socket,
                "physics": self.physics,
                "materials": {"*": {"albedo": True, "normal": True, "roughness": True, "metalness": True, "displacement": True}},
            },
            ensure_ascii=False,
        )


def _resolve_socket(requested: str | None, slot: str, body_joint_names: list[str]) -> str | None:
    """Match socket bone name against body skeleton (exact or suffix match)."""
    if not body_joint_names:
        return None
    stripped = [j.split(":")[-1] for j in body_joint_names]
    for candidate in (requested, _DEFAULT_SOCKET_BY_SLOT.get(slot)):
        if not candidate:
            continue
        if candidate in body_joint_names:
            return candidate
        if candidate in stripped:
            return body_joint_names[stripped.index(candidate)]
    return None


async def _classify_wardrobe_kind(description: str, user_id: int, db: AsyncSession | None, body_joint_names: list[str]) -> WardrobeRouting:
    """Classify description into texture, garment, or accessory routing."""
    joint_hint = ("Available bones for socket: " + ", ".join(body_joint_names)) if body_joint_names else ""
    fallback = WardrobeRouting.default()
    try:
        raw = await _vision_llm_call(db, user_id, _WARDROBE_KIND_CLASSIFIER_SYSTEM, f"{description}\n\n{joint_hint}", [], response_format={"type": "json_object"})
        parsed = parse_llm_json(raw) or {}
        if not isinstance(parsed, dict) or parsed.get("kind") not in ("texture", "garment", "accessory"):
            return fallback

        kind = parsed["kind"]
        slot = parsed.get("slot")
        slot = slot if slot in _VALID_SLOTS else ("outfit" if kind == "texture" else "torso")
        physics = "cloth" if parsed.get("physics") == "cloth" and kind == "garment" else "skin"
        socket = _resolve_socket(parsed.get("socket"), slot, body_joint_names) if kind == "accessory" else None
        if kind == "accessory" and socket is None:
            # No resolvable socket → degrade to a garment in the nearest slot.
            kind, slot, physics = ("garment", slot if slot != "outfit" else "torso", "skin")
        return WardrobeRouting(kind=kind, slot=slot, socket=socket, physics=physics)
    except Exception as exc:
        logger.info("wardrobe kind classifier failed, defaulting to garment", extra={"error": str(exc)})

    return fallback


async def preview_wardrobe_outfit(
    db: AsyncSession, *, user_id: int, description: str, image_bytes: bytes | None = None, content_type: str | None = None, feedback: str | None = None, io_dir: Path | None = None
) -> WardrobePreviewResponse:
    """Route description and generate a wardrobe preview (texture or geometric).

    ``io_dir`` (render worker) hosts the geometric pipeline's Blender
    workspace under the host-visible per-job directory."""
    joints = await _body_joint_names(db, user_id)
    routing = await _classify_wardrobe_kind(description, user_id, db, joints)
    logger.info("wardrobe pipeline routed", extra={"user_id": user_id, "kind": routing.kind, "slot": routing.slot})

    if routing.kind == "texture":
        return await preview_wardrobe_texture(db, user_id=user_id, description=description, image_bytes=image_bytes, content_type=content_type, feedback=feedback)

    return await preview_garment(
        db, user_id=user_id, description=description, image_bytes=image_bytes, content_type=content_type, feedback=feedback, routing=routing, body_joint_names=joints, io_dir=io_dir
    )


def _read_model_json_chunk(asset_url: str) -> bytes:
    """Read glTF JSON chunk from a GLB without loading binary buffer payloads."""
    parts = asset_url.split("/", 2)
    if len(parts) != 3:
        raise RuntimeError(f"malformed model asset_url: {asset_url}")
    resolved = resolve_companion_model_path(int(parts[1]), parts[2])
    if resolved is None:
        raise RuntimeError(f"body model file not found: {asset_url}")
    with open(resolved[0], "rb") as f:
        f.read(12)  # magic + version + total length
        chunk_len = int.from_bytes(f.read(4), "little")
        f.read(4)  # chunk type ('JSON')
        return f.read(chunk_len)


async def _body_joint_names(db: AsyncSession, user_id: int) -> list[str]:
    """Extract active body model skin joint names."""
    model = await get_active_model(db, user_id)
    if model is None or not model.asset_url:
        return []
    cached = _BODY_JOINT_NAMES_CACHE.get(model.asset_url)
    if cached is not None:
        return cached
    try:
        chunk = await asyncio.to_thread(_read_model_json_chunk, model.asset_url)
        names = joint_names_from_gltf(json.loads(chunk))
    except Exception:
        return []
    _BODY_JOINT_NAMES_CACHE[model.asset_url] = names
    return names


async def _generate_pbr_channels(
    *, description: str, feedback: str | None, rig_type: str, reference_data_uri: str | None, user_id: int, style: str = "realistic"
) -> tuple[dict[str, tuple[str, str]], dict[str, str]]:
    """Generate 5-channel PBR textures concurrently; raises if albedo fails."""
    prompts = {ch: build_texture_prompt(description=description, feedback=feedback, rig_type=rig_type, channel=ch, style=style) for ch in _PBR_CHANNELS}

    async def _gen_one(ch: str) -> tuple[str, str] | None:
        try:
            result_json = await image_generation_tool(prompt=prompts[ch], reference_image=reference_data_uri, llm_config={}, size="1024x1024", n=1, user_id=user_id)
            src_url = first_image_url(result_json)
            if not src_url:
                return None
            if "/api/media/files/" in src_url:
                fid = src_url.rsplit("/", 1)[-1].split("?")[0]
                return src_url, fid
            fetched = await _download_texture_with_mime(src_url)
            if fetched is None:
                return None
            data, ct, ext = fetched
            fid, pub_url = save_file(data, session_id="", content_type=ct, ext=ext, meta_marker=f"wardrobe_preview:{user_id}")
            return pub_url, fid
        except Exception as exc:
            logger.warning("PBR texture channel generation failed", extra={"channel": ch, "error": str(exc)})
            return None

    results = await asyncio.gather(*[_gen_one(ch) for ch in _PBR_CHANNELS], return_exceptions=True)
    res_dict = {ch: res for ch, res in zip(_PBR_CHANNELS, results) if isinstance(res, tuple)}
    if "albedo" not in res_dict:
        raise RuntimeError("Texture generation failed: no URL in provider response for albedo channel")
    return res_dict, prompts


def _preview_response(res_dict: dict[str, tuple[str, str]], prompts: dict[str, str], **geometric: str | None) -> WardrobePreviewResponse:
    """Assemble WardrobePreviewResponse from PBR textures and optional geometric fields."""
    n_url, n_fid = res_dict.get("normal", (None, None))
    r_url, r_fid = res_dict.get("roughness", (None, None))
    m_url, m_fid = res_dict.get("metalness", (None, None))
    d_url, d_fid = res_dict.get("displacement", (None, None))
    return WardrobePreviewResponse(
        url=res_dict["albedo"][0],
        prompt=prompts["albedo"],
        file_id=res_dict["albedo"][1],
        normal_url=n_url,
        normal_file_id=n_fid,
        roughness_url=r_url,
        roughness_file_id=r_fid,
        metalness_url=m_url,
        metalness_file_id=m_fid,
        displacement_url=d_url,
        displacement_file_id=d_fid,
        **geometric,
    )


async def preview_wardrobe_texture(
    db: AsyncSession | None = None,
    *,
    user_id: int,
    description: str,
    image_bytes: bytes | None = None,
    content_type: str | None = None,
    feedback: str | None = None,
    rig_type: str | None = None,
) -> WardrobePreviewResponse:
    style = "realistic"
    if rig_type is None:
        if db is not None:
            rig_type = await _resolve_rig_type(db, user_id)
            style = await _resolve_style(db, user_id)
        else:
            async with SESSION_LOCAL() as probe_db:
                rig_type = await _resolve_rig_type(probe_db, user_id)
                style = await _resolve_style(probe_db, user_id)
    reference_data_uri = build_data_uri(image_bytes, content_type) if image_bytes else None
    res_dict, prompts = await _generate_pbr_channels(
        description=description, feedback=feedback, rig_type=rig_type, reference_data_uri=reference_data_uri, user_id=user_id, style=style
    )
    return _preview_response(res_dict, prompts)


async def _download_texture_with_mime(url: str) -> tuple[bytes, str, str] | None:
    """Download remote texture via SSRF-safe client and detect content type."""
    if "/api/media/files/" in url:
        # Already-resolved temp-media URLs don't go through here — handled above.
        return None
    try:
        content = await download_capped(url, max_bytes=50 * 1024 * 1024, timeout=120.0)
        ext = "png"
        raw_ct = "image/png"
        if content.startswith(b"\xff\xd8\xff"):
            raw_ct, ext = "image/jpeg", "jpg"
        elif content.startswith(b"RIFF") and b"WEBP" in content[:12]:
            raw_ct, ext = "image/webp", "webp"
        elif content.startswith(b"GIF8"):
            raw_ct, ext = "image/gif", "gif"
        return content, raw_ct, ext
    except Exception:
        return None


def _read_model_bytes(asset_url: str) -> bytes:
    """Read companion-models/<uid>/<file> GLB bytes from disk."""
    parts = asset_url.split("/", 2)
    if len(parts) != 3:
        raise RuntimeError(f"malformed model asset_url: {asset_url}")
    resolved = resolve_companion_model_path(int(parts[1]), parts[2])
    if resolved is None:
        raise RuntimeError(f"body model file not found: {asset_url}")
    return decompress_glb_if_needed(resolved[0].read_bytes())


async def preview_garment(
    db: AsyncSession,
    *,
    user_id: int,
    description: str,
    image_bytes: bytes | None = None,
    content_type: str | None = None,
    feedback: str | None = None,
    routing: WardrobeRouting,
    body_joint_names: list[str] | None = None,
    io_dir: Path | None = None,
) -> WardrobePreviewResponse:
    """Generate geometric unit (garment or accessory) via LLM-Blender pipeline."""
    model = await get_active_model(db, user_id)
    if model is None or not model.asset_url:
        raise RuntimeError("没有找到 3D 身体模型，请先生成身体模型")
    avatar = await get_active_avatar(db, user_id)
    if avatar is None or not avatar.seed_front_url:
        raise RuntimeError("没有找到种子图，无法为 LLM 提供身体参考")
    body_glb_bytes, body_preview_uri = await asyncio.gather(
        asyncio.to_thread(_read_model_bytes, model.asset_url), asyncio.to_thread(load_avatar_bytes_as_data_uri, avatar.seed_front_url)
    )

    reference_data_uri = build_data_uri(image_bytes, content_type) if image_bytes else None
    rig_type = model.rig_type or "biped"
    assembly = routing.assembly_json()
    # The geometry pipeline (minutes) and PBR fan-out (seconds) are independent.
    garment_task = asyncio.create_task(
        run_garment_pipeline(
            description=description,
            body_glb_bytes=body_glb_bytes,
            body_preview_uri=body_preview_uri,
            reference_uris=[reference_data_uri] if reference_data_uri else [],
            rig_type=rig_type,
            kind=routing.kind,
            socket=routing.socket,
            assembly_json=assembly,
            body_joint_names=body_joint_names,
            user_id=user_id,
            io_dir=io_dir,
        )
    )
    pbr_task = asyncio.create_task(
        _generate_pbr_channels(
            description=description, feedback=feedback, rig_type=rig_type, reference_data_uri=reference_data_uri, user_id=user_id, style=model.style or "realistic"
        )
    )
    # gather returns exceptions so the minute-long garment pipeline is not
    # cancelled on a seconds-scale PBR failure; the runner's ``finally: rmtree(io_dir)``
    # is responsible for its own tempdir cleanup regardless of which side raises.
    garment_result, pbr_result = await asyncio.gather(garment_task, pbr_task, return_exceptions=True)
    if isinstance(garment_result, BaseException):
        # Cancel PBR if still running so we don't leak its task; raise the garment
        # exception first since the minute-long pipeline is what the user is waiting on.
        if not pbr_task.done():
            pbr_task.cancel()
        raise garment_result
    if isinstance(pbr_result, BaseException):
        # PBR raised but garment succeeded — surface the PBR error (texture channels are
        # required for the preview to be usable).
        raise pbr_result
    glb_bytes = garment_result
    res_dict, prompts = pbr_result

    mesh_fid, mesh_url = save_file(glb_bytes, session_id="", content_type="model/gltf-binary", ext="glb", meta_marker=f"wardrobe_preview:{user_id}")
    return _preview_response(res_dict, prompts, mesh_url=mesh_url, mesh_file_id=mesh_fid, kind=routing.kind, assembly_json=assembly)


async def confirm_wardrobe_item(
    *,
    user_id: int,
    file_id: str,
    name: str,
    prompt: str | None = None,
    normal_file_id: str | None = None,
    roughness_file_id: str | None = None,
    metalness_file_id: str | None = None,
    displacement_file_id: str | None = None,
    mesh_file_id: str | None = None,
    assembly_json: str | None = None,
    equip: bool = True,
    origin: str = "user",
    gift_state: str | None = None,
    gift_reason: str | None = None,
    gift_message: str | None = None,
    persona_definition: dict[str, str] | None = None,
    vision_chain: list | None = None,
    db: AsyncSession | None = None,
) -> WardrobeItem:
    """Write a wardrobe item. Caller pre-resolves ``persona_definition`` and
    ``vision_chain`` in a short session so the LLM normalisation call does not
    hold a DB connection across its multi-second await. ``db`` is opened
    here only for the short write path (add/flush/commit/equip/sync) and
    must be closed by the caller (or pass ``None`` to let this function
    manage its own short session)."""
    res = get_file_path(file_id)
    if res is None:
        raise WardrobeSourceExpiredError(f"temp-media file expired for file_id {file_id}")
    path, _ = res
    try:
        data = await asyncio.to_thread(Path(path).read_bytes)
    except OSError as exc:
        raise WardrobeSourceExpiredError(f"temp-media file unreadable: {exc}") from exc

    texture_url = save_companion_asset(data, user_id=user_id, label="wardrobe_texture", ext="png")

    async def _resolve_channel(fid: str | None, label: str, ext: str = "png") -> str | None:
        if not fid:
            return None
        cp = get_file_path(fid)
        if cp is None:
            return None
        try:
            cdata = await asyncio.to_thread(Path(cp[0]).read_bytes)
            return save_companion_asset(cdata, user_id=user_id, label=label, ext=ext)
        except OSError:
            return None

    (normal_url, roughness_url, metalness_url, displacement_url, mesh_url) = await asyncio.gather(
        _resolve_channel(normal_file_id, "wardrobe_normal"),
        _resolve_channel(roughness_file_id, "wardrobe_roughness"),
        _resolve_channel(metalness_file_id, "wardrobe_metalness"),
        _resolve_channel(displacement_file_id, "wardrobe_displacement"),
        _resolve_channel(mesh_file_id, "wardrobe_mesh", ext="glb"),
    )
    # The garment GLB is required when requested — expiry/unreadability must 409,
    # not silently degrade to a texture row.
    if mesh_file_id and mesh_url is None:
        raise WardrobeSourceExpiredError(f"temp-media garment GLB expired or unreadable for file_id {mesh_file_id}")
    # Geometric units carry their kind in assembly_json (texture|garment|accessory);
    # mesh-less rows with a stray assembly payload degrade to texture.
    asm = safe_json_loads(assembly_json, default={}) if assembly_json else {}
    asm_kind = asm.get("kind") if isinstance(asm, dict) else None
    kind = asm_kind if mesh_url and asm_kind in ("garment", "accessory") else ("garment" if mesh_url else "texture")
    final_assembly = assembly_json or "{}"

    # LLM call uses db=None + caller-pre-resolved persona/vision_chain so the
    # multi-second generation does not hold a pool connection.
    outfit_desc = await normalize_outfit(chat, raw_input=prompt or name, persona_definition=persona_definition, user_id=user_id, db=None, vision_chain=vision_chain)

    item = WardrobeItem(
        user_id=user_id,
        name=name,
        category="generated",
        material_overrides_json="{}",
        texture_url=texture_url,
        normal_url=normal_url,
        roughness_url=roughness_url,
        metalness_url=metalness_url,
        displacement_url=displacement_url,
        prompt=prompt,
        outfit_description=outfit_desc,
        equipped=equip,
        kind=kind,
        mesh_url=mesh_url,
        assembly_json=final_assembly,
        origin=origin,
        gift_state=gift_state,
        gift_reason=gift_reason,
        gift_message=gift_message,
    )
    if db is None:
        async with SESSION_LOCAL() as write_db:
            return await _confirm_write(write_db, item, equip=equip, user_id=user_id)
    if equip:
        await _equip(db, item)
    db.add(item)
    await db.flush()
    if equip:
        await _sync_persona_outfit(db, user_id)
    else:
        await db.commit()
    _re_sign_texture(item)
    return item


async def _confirm_write(db: AsyncSession, item: WardrobeItem, *, equip: bool, user_id: int) -> WardrobeItem:
    """Short write path used when caller did not pass an open session."""
    if equip:
        await _equip(db, item)
    db.add(item)
    await db.flush()
    if equip:
        await _sync_persona_outfit(db, user_id)
    else:
        await db.commit()
    _re_sign_texture(item)
    return item


def slot_of(item: WardrobeItem) -> str:
    """Resolve mutual-exclusion slot from item kind and assembly metadata."""
    kind = getattr(item, "kind", None) or "texture"
    if kind == "texture" or not item.mesh_url:
        return _SLOT_TEXTURE
    asm = safe_json_loads(item.assembly_json or "{}", default={})
    slot = asm.get("slot") if isinstance(asm, dict) else None
    return slot if isinstance(slot, str) and slot in _VALID_SLOTS else "torso"


async def _query_equipped(db: AsyncSession, user_id: int) -> list[WardrobeItem]:
    """Equipped rows oldest-first, without read-path side effects (no re-signing)."""
    return (await db.execute(select(WardrobeItem).where(WardrobeItem.user_id == user_id, WardrobeItem.equipped.is_(True)).order_by(WardrobeItem.updated_at))).scalars().all()


async def _unequip_slot(db: AsyncSession, user_id: int, slot: str, *, exclude_id: int | None = None) -> None:
    """Unequip existing items occupying the same slot."""
    equipped = await _query_equipped(db, user_id)
    ids = [i.id for i in equipped if i.id != exclude_id and slot_of(i) == slot]
    if ids:
        await db.execute(update(WardrobeItem).where(WardrobeItem.id.in_(ids)).values(equipped=False))


async def _equip(db: AsyncSession, item: WardrobeItem) -> None:
    """Equip item with same-slot mutual exclusion and gift state resolution."""
    await _unequip_slot(db, item.user_id, slot_of(item), exclude_id=item.id)
    item.equipped = True
    if item.gift_state in ("pending", "declined"):
        item.gift_state = "accepted"


async def _sync_persona_outfit(db: AsyncSession, user_id: int) -> None:
    """Sync concatenated descriptions of all equipped items to Persona appearance."""
    equipped = await _query_equipped(db, user_id)
    desc = "；".join(i.outfit_description for i in equipped if i.outfit_description)
    await update_outfit_field(db, user_id, desc)


def discard_wardrobe_preview(file_id: str, *, user_id: int) -> bool:
    """Best-effort delete of an unconfirmed wardrobe preview from temp-media.

    The marker written by ``save_file(meta_marker=f"wardrobe_preview:{user_id}")``
    must match the caller's user_id — a cross-user DELETE on another user's
    preview is rejected with ``TempFileMarkerMismatch``."""
    return temp_file_delete(file_id, required_marker=f"wardrobe_preview:{user_id}")


async def equip_wardrobe_item(db: AsyncSession, user_id: int, item_id: int) -> WardrobeItem:
    # Check ownership before un-equipping — a bad item_id would otherwise strip the current outfit and 404.
    item = (await db.execute(select(WardrobeItem).where(WardrobeItem.user_id == user_id, WardrobeItem.id == item_id))).scalar_one_or_none()
    if item is None:
        raise ValueError("Wardrobe item not found")
    await _equip(db, item)
    await _sync_persona_outfit(db, user_id)
    _re_sign_texture(item)
    return item


async def decline_wardrobe_item(db: AsyncSession, user_id: int, item_id: int) -> WardrobeItem:
    item = (await db.execute(select(WardrobeItem).where(WardrobeItem.user_id == user_id, WardrobeItem.id == item_id))).scalar_one_or_none()
    if item is None:
        raise ValueError("Wardrobe item not found")
    # Only pending companion-origin gifts can be declined — guarding here
    # prevents accidentally stamping "declined" on a user-created or already
    # resolved item.
    if item.origin != "companion" or item.gift_state != "pending":
        raise ValueError("Wardrobe item is not a pending gift")
    item.gift_state = "declined"
    await db.commit()
    _re_sign_texture(item)
    return item


async def delete_wardrobe_item(db: AsyncSession, user_id: int, item_id: int) -> bool:
    # Capture paths before delete — nothing sweeps orphaned companion-assets.
    item = (await db.execute(select(WardrobeItem).where(WardrobeItem.user_id == user_id, WardrobeItem.id == item_id))).scalar_one_or_none()
    if item is None:
        return False
    paths = list(_iter_companion_asset_paths(item))
    was_equipped = item.equipped
    await db.delete(item)
    # Refresh the persona outfit field from the surviving equipped set.
    if was_equipped:
        await _sync_persona_outfit(db, user_id)
    else:
        await db.commit()
    for _attr, uid, filename in paths:
        if unlink_companion_asset(f"companion-assets/{uid}/{filename}") is None:
            logger.warning("Failed to unlink wardrobe asset", extra={"user_id": user_id, "path": f"companion-assets/{uid}/{filename}"})
    return True

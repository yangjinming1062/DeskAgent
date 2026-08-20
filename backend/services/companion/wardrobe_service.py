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

# WardrobeItem 上需要重新签名与删除的 companion-assets URL 字段
_COMPANION_ASSET_URL_ATTRS: tuple[str, ...] = ("texture_url", "normal_url", "roughness_url", "metalness_url", "displacement_url", "mesh_url")

# 缓存身体模型骨骼名，避免每次预览都重读数 MB 的 GLB
_BODY_JOINT_NAMES_CACHE: dict[str, list[str]] = {}
_TEXTURE_RECOVERY_TASKS: dict[tuple[int, int], asyncio.Task[None]] = {}

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
    """确认衣柜预览时，其 temp-media 源文件已过期或缺失。"""


async def fetch_texture_bytes(url: str) -> bytes | None:
    """把生成结果 URL（本地 temp-media 或远端）解析为字节。"""
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
    """逐一产出条目上每个 companion-assets URL 对应的 (字段名, uid, 文件名)。"""
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
    """重新签名条目上的所有 companion-assets URL，并清理已失效的 temp-media 链接。"""
    for attr, uid, filename in _iter_companion_asset_paths(item):
        setattr(item, attr, build_signed_asset_url(int(uid), filename))

    # temp-media 文件已过期时把字段置空，让客户端干净地降级
    for attr in _COMPANION_ASSET_URL_ATTRS:
        val = getattr(item, attr, None)
        if val and "/api/media/files/" in val:
            fid = val.rsplit("/", 1)[-1].split("?")[0]
            if get_file_path(fid) is None:
                setattr(item, attr, None)


async def check_and_recover_missing_texture(user_id: int, item: WardrobeItem) -> None:
    """后台任务：已装备条目贴图缺失时，用其 outfit_description 重新生成 PBR 贴图。"""
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
            if avatar and avatar.asset_url:
                ref_uri = load_avatar_bytes_as_data_uri(avatar.asset_url)
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


def _spawn_texture_recovery_once(user_id: int, item: WardrobeItem) -> None:
    key = (user_id, item.id)
    if key in _TEXTURE_RECOVERY_TASKS:
        return
    task = asyncio.create_task(check_and_recover_missing_texture(user_id, item))
    _TEXTURE_RECOVERY_TASKS[key] = task
    task.add_done_callback(lambda _task, _key=key: _TEXTURE_RECOVERY_TASKS.pop(_key, None))


async def list_wardrobe(db: AsyncSession, user_id: int) -> list[WardrobeItem]:
    """列出用户衣柜条目，并对资源 URL 重新签名。"""
    items = (await db.execute(select(WardrobeItem).where(WardrobeItem.user_id == user_id).order_by(WardrobeItem.created_at))).scalars().all()
    for item in items:
        _re_sign_texture(item)
    return items


async def get_equipped_item(db: AsyncSession, user_id: int) -> WardrobeItem | None:
    """返回最近更新的已装备条目。"""
    equipped = await _query_equipped(db, user_id)
    item = equipped[-1] if equipped else None
    if item:
        _re_sign_texture(item)
        if item.equipped and (item.kind in (None, "texture")) and not item.texture_url:
            _spawn_texture_recovery_once(user_id, item)
    return item


async def get_equipped_items(db: AsyncSession, user_id: int) -> list[WardrobeItem]:
    """返回全部已装备条目（每个槽位至多一件），按时间正序。"""
    items = await _query_equipped(db, user_id)
    for item in items:
        _re_sign_texture(item)
        if item.equipped and (item.kind in (None, "texture")) and not item.texture_url:
            _spawn_texture_recovery_once(user_id, item)
    return items


async def _resolve_rig_type(db: AsyncSession, user_id: int) -> str:
    """从激活模型或人设物种推断骨骼类型。"""
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
    """从激活模型解析渲染风格；无模型行时按物种预设回落到主流默认风格。"""
    model = await get_active_model(db, user_id)
    if model and model.style:
        return model.style

    persona = await get_or_create_persona(db, user_id)
    definition = safe_json_loads(persona.definition_json or "{}", default={})
    species = (definition.get("biological_type") or "").strip()
    return resolve_fullbody_style(species)


@dataclass
class WardrobeRouting:
    kind: str
    slot: str
    socket: str | None
    physics: str

    @classmethod
    def default(cls) -> "WardrobeRouting":
        """分类失败时的兜底——选择永远可用的服装管线。"""
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
    """把挂点骨骼名与身体骨架匹配（精确或后缀匹配）。"""
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
    """把换装描述分类为贴图 / 服装 / 挂件三条管线之一。"""
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
            # 找不到可用挂点，降级为最接近槽位的服装
            kind, slot, physics = ("garment", slot if slot != "outfit" else "torso", "skin")
        return WardrobeRouting(kind=kind, slot=slot, socket=socket, physics=physics)
    except Exception as exc:
        logger.info("wardrobe kind classifier failed, defaulting to garment", extra={"error": str(exc)})

    return fallback


async def preview_wardrobe_outfit(
    db: AsyncSession, *, user_id: int, description: str, image_bytes: bytes | None = None, content_type: str | None = None, feedback: str | None = None, io_dir: Path | None = None
) -> WardrobePreviewResponse:
    """按分类路由生成换装预览（贴图或几何体）。"""
    joints = await _body_joint_names(db, user_id)
    routing = await _classify_wardrobe_kind(description, user_id, db, joints)
    logger.info("wardrobe pipeline routed", extra={"user_id": user_id, "kind": routing.kind, "slot": routing.slot})

    if routing.kind == "texture":
        return await preview_wardrobe_texture(db, user_id=user_id, description=description, image_bytes=image_bytes, content_type=content_type, feedback=feedback)

    return await preview_garment(
        db, user_id=user_id, description=description, image_bytes=image_bytes, content_type=content_type, feedback=feedback, routing=routing, body_joint_names=joints, io_dir=io_dir
    )


def _read_model_json_chunk(asset_url: str) -> bytes:
    """只读取 GLB 的 glTF JSON 块，避免把二进制缓冲一并载入内存。"""
    parts = asset_url.split("/", 2)
    if len(parts) != 3:
        raise RuntimeError(f"malformed model asset_url: {asset_url}")
    resolved = resolve_companion_model_path(int(parts[1]), parts[2])
    if resolved is None:
        raise RuntimeError(f"body model file not found: {asset_url}")
    with open(resolved[0], "rb") as f:
        f.read(12)
        chunk_len = int.from_bytes(f.read(4), "little")
        f.read(4)
        return f.read(chunk_len)


async def _body_joint_names(db: AsyncSession, user_id: int) -> list[str]:
    """提取激活身体模型的蒙皮骨骼名。"""
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
    """并发生成 5 个 PBR 通道贴图；albedo 失败则抛错。"""
    prompts = {ch: build_texture_prompt(description=description, feedback=feedback, rig_type=rig_type, channel=ch, style=style) for ch in _PBR_CHANNELS}

    async def _gen_one(ch: str) -> tuple[str, str] | None:
        try:
            result_json = await image_generation_tool(prompt=prompts[ch], reference_image=reference_data_uri, llm_config={}, size="1024x1024", n=1, user_id=user_id)
            src_url = first_image_url(result_json)
            if not src_url:
                tool_err = (safe_json_loads(result_json, default={}) or {}).get("error") if isinstance(safe_json_loads(result_json, default={}), dict) else None
                logger.warning("PBR texture channel image generation returned no URL", extra={"channel": ch, "error": tool_err, "user_id": user_id})
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
    """把 PBR 贴图与可选的几何体字段组装成预览响应。"""
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
    """生成纯贴图换装预览（不改变几何体）。"""
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
    """经防 SSRF 的客户端下载远端贴图并识别内容类型。"""
    if "/api/media/files/" in url:
        # 已解析的 temp-media 地址由上游处理，不应走到这里
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
    """从磁盘读取身体模型的 GLB 字节。"""
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
    """经 LLM-Blender 管线生成几何单元（服装或挂件）。"""
    model = await get_active_model(db, user_id)
    if model is None or not model.asset_url:
        raise RuntimeError("没有找到 3D 身体模型，请先生成身体模型")
    avatar = await get_active_avatar(db, user_id)
    if avatar is None or not avatar.asset_url:
        raise RuntimeError("没有找到形象参考，无法为 LLM 提供身体参考")
    body_glb_bytes, body_preview_uri = await asyncio.gather(
        asyncio.to_thread(_read_model_bytes, model.asset_url), asyncio.to_thread(load_avatar_bytes_as_data_uri, avatar.asset_url)
    )

    reference_data_uri = build_data_uri(image_bytes, content_type) if image_bytes else None
    rig_type = model.rig_type or "biped"
    assembly = routing.assembly_json()
    # 几何管线（分钟级）与 PBR 扇出（秒级）互不依赖，可并行
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
    # gather 收集异常而非抛出，避免秒级的 PBR 失败取消掉分钟级的服装管线
    garment_result, pbr_result = await asyncio.gather(garment_task, pbr_task, return_exceptions=True)
    if isinstance(garment_result, BaseException):
        # 取消仍在跑的 PBR 任务以免泄漏；优先抛服装异常，因为那才是用户在等的长任务
        if not pbr_task.done():
            pbr_task.cancel()
        raise garment_result
    if isinstance(pbr_result, BaseException):
        # 贴图通道是预览可用的必要条件，故 PBR 失败也须上抛
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
    """落库一条衣柜条目；调用方须预先解析 persona_definition 与 vision_chain，使数秒的 LLM 规范化调用不占用数据库连接。"""
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
    # 请求了服装 GLB 就必须拿到：过期或不可读须报错，不能悄悄降级成贴图条目
    if mesh_file_id and mesh_url is None:
        raise WardrobeSourceExpiredError(f"temp-media garment GLB expired or unreadable for file_id {mesh_file_id}")
    # 几何单元的 kind 记录在 assembly_json 中；无 mesh 却带 assembly 的行一律降级为贴图
    asm = safe_json_loads(assembly_json, default={}) if assembly_json else {}
    asm_kind = asm.get("kind") if isinstance(asm, dict) else None
    kind = asm_kind if mesh_url and asm_kind in ("garment", "accessory") else ("garment" if mesh_url else "texture")
    final_assembly = assembly_json or "{}"

    # 传 db=None 并使用调用方预解析的人设/视觉链，使这次数秒的生成不占用连接池
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
    """调用方未提供已开会话时使用的短写路径。"""
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
    """由条目类型与装配元数据解析出互斥槽位。"""
    kind = getattr(item, "kind", None) or "texture"
    if kind == "texture" or not item.mesh_url:
        return _SLOT_TEXTURE
    asm = safe_json_loads(item.assembly_json or "{}", default={})
    slot = asm.get("slot") if isinstance(asm, dict) else None
    return slot if isinstance(slot, str) and slot in _VALID_SLOTS else "torso"


async def _query_equipped(db: AsyncSession, user_id: int) -> list[WardrobeItem]:
    """按时间正序返回已装备条目，且不带读路径副作用（不重新签名）。"""
    return (await db.execute(select(WardrobeItem).where(WardrobeItem.user_id == user_id, WardrobeItem.equipped.is_(True)).order_by(WardrobeItem.updated_at))).scalars().all()


async def _unequip_slot(db: AsyncSession, user_id: int, slot: str, *, exclude_id: int | None = None) -> None:
    """卸下占用同一槽位的其他条目。"""
    equipped = await _query_equipped(db, user_id)
    ids = [i.id for i in equipped if i.id != exclude_id and slot_of(i) == slot]
    if ids:
        await db.execute(update(WardrobeItem).where(WardrobeItem.id.in_(ids)).values(equipped=False))


async def _equip(db: AsyncSession, item: WardrobeItem) -> None:
    """装备条目，处理同槽位互斥与礼物状态流转。"""
    await _unequip_slot(db, item.user_id, slot_of(item), exclude_id=item.id)
    item.equipped = True
    if item.gift_state in ("pending", "declined"):
        item.gift_state = "accepted"


async def _sync_persona_outfit(db: AsyncSession, user_id: int) -> None:
    """把所有已装备条目的描述拼接同步到人设的着装字段。"""
    equipped = await _query_equipped(db, user_id)
    desc = "；".join(i.outfit_description for i in equipped if i.outfit_description)
    await update_outfit_field(db, user_id, desc)


def discard_wardrobe_preview(file_id: str, *, user_id: int) -> bool:
    """尽力删除未确认的换装预览；标记必须与调用方 user_id 一致，跨用户删除会被拒绝。"""
    return temp_file_delete(file_id, required_marker=f"wardrobe_preview:{user_id}")


async def equip_wardrobe_item(db: AsyncSession, user_id: int, item_id: int) -> WardrobeItem:
    """装备指定衣柜条目并同步人设着装。"""
    # 先校验归属再卸装：否则错误的 item_id 会先把当前装扮卸掉再返回 404
    item = (await db.execute(select(WardrobeItem).where(WardrobeItem.user_id == user_id, WardrobeItem.id == item_id))).scalar_one_or_none()
    if item is None:
        raise ValueError("Wardrobe item not found")
    await _equip(db, item)
    await _sync_persona_outfit(db, user_id)
    _re_sign_texture(item)
    return item


async def decline_wardrobe_item(db: AsyncSession, user_id: int, item_id: int) -> WardrobeItem:
    """拒收伙伴赠送的待确认礼物条目。"""
    item = (await db.execute(select(WardrobeItem).where(WardrobeItem.user_id == user_id, WardrobeItem.id == item_id))).scalar_one_or_none()
    if item is None:
        raise ValueError("Wardrobe item not found")
    # 只有伙伴赠送且待确认的条目可拒收，避免误把用户自建或已处理的条目标记为 declined
    if item.origin != "companion" or item.gift_state != "pending":
        raise ValueError("Wardrobe item is not a pending gift")
    item.gift_state = "declined"
    await db.commit()
    _re_sign_texture(item)
    return item


async def delete_wardrobe_item(db: AsyncSession, user_id: int, item_id: int) -> bool:
    """删除衣柜条目及其关联的资源文件。"""
    # 删行前先取出资源路径——没有其他机制会清扫孤儿 companion-assets 文件
    item = (await db.execute(select(WardrobeItem).where(WardrobeItem.user_id == user_id, WardrobeItem.id == item_id))).scalar_one_or_none()
    if item is None:
        return False
    paths = list(_iter_companion_asset_paths(item))
    was_equipped = item.equipped
    await db.delete(item)
    # 依据剩余的已装备集合刷新人设着装字段
    if was_equipped:
        await _sync_persona_outfit(db, user_id)
    else:
        await db.commit()
    for _attr, uid, filename in paths:
        if unlink_companion_asset(f"companion-assets/{uid}/{filename}") is None:
            logger.warning("Failed to unlink wardrobe asset", extra={"user_id": user_id, "path": f"companion-assets/{uid}/{filename}"})
    return True

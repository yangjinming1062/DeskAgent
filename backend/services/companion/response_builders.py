from components import safe_json_loads
from modules.companion import AvatarAsset, AvatarAssetResponse, CompanionModel, CompanionModelResponse

from .asset_store import get_companion_model_sha256
from .avatar_service import _re_sign_bare_path
from .pipeline import signed_model_url


def avatar_response(asset: AvatarAsset) -> AvatarAssetResponse:
    """把头像行转换为接口响应，并对全身样图路径重新签名。"""
    prompt_payload = safe_json_loads(asset.prompt_json, default={})
    payload = prompt_payload if isinstance(prompt_payload, dict) else {}
    raw_samples = payload.get("fullbody_samples")
    fullbody_samples = {
        style_id: signed for style_id, bare in (raw_samples.items() if isinstance(raw_samples, dict) else []) if isinstance(bare, str) and (signed := _re_sign_bare_path(bare))
    }
    return AvatarAssetResponse(
        id=asset.id,
        asset_url=asset.asset_url,
        seed_front_url=getattr(asset, "seed_front_url", None) or "",
        fullbody_style=str(payload.get("fullbody_style") or ""),
        fullbody_samples=fullbody_samples,
        prompt=payload.get("prompt", ""),
        status="succeeded",
    )


def model_response(model: CompanionModel) -> CompanionModelResponse:
    """把 3D 模型行转换为接口响应，补齐签名地址与内容哈希。"""
    content_hash = model.content_hash or None
    if not content_hash and model.asset_url:
        parts = model.asset_url.split("/", 2)
        if len(parts) == 3:
            try:
                content_hash = get_companion_model_sha256(int(parts[1]), parts[2])
            except Exception:
                pass

    return CompanionModelResponse(
        id=model.id,
        species=model.species,
        provider=model.provider,
        asset_url=signed_model_url(model) or model.asset_url,
        status=model.status,
        has_rig=model.has_rig,
        rig_type=model.rig_type,
        rig_naming=model.rig_naming,
        style=model.style or "realistic",
        content_hash=content_hash,
        clip_map=safe_json_loads(model.clip_map_json or "{}", default={}),
    )

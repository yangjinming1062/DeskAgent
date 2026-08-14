from components import safe_json_loads
from modules.companion import AvatarAsset, AvatarAssetResponse, CompanionModel, CompanionModelResponse, WardrobeItem, WardrobeItemResponse

from services.companion.model_service import signed_model_url
from services.companion.wardrobe_service import slot_of


def avatar_response(asset: AvatarAsset) -> AvatarAssetResponse:
    prompt_payload = safe_json_loads(asset.prompt_json, default={})
    return AvatarAssetResponse(
        id=asset.id,
        asset_url=asset.asset_url,
        seed_front_url=asset.seed_front_url or None,
        seed_right_url=asset.seed_right_url or None,
        seed_back_url=asset.seed_back_url or None,
        prompt=prompt_payload.get("prompt", "") if isinstance(prompt_payload, dict) else "",
        status="succeeded",
    )


def model_response(model: CompanionModel) -> CompanionModelResponse:
    return CompanionModelResponse(
        id=model.id,
        species=model.species,
        provider=model.provider,
        asset_url=signed_model_url(model) or model.asset_url,
        morph_params=safe_json_loads(model.morph_params_json or "{}", default={}),
        status=model.status,
        has_rig=model.has_rig,
        has_morph_targets=model.has_morph_targets,
        rig_type=model.rig_type,
        rig_naming=model.rig_naming,
    )


def wardrobe_response(item: WardrobeItem) -> WardrobeItemResponse:
    return WardrobeItemResponse(
        id=item.id,
        name=item.name,
        category=item.category,
        material_overrides_json=item.material_overrides_json,
        texture_url=item.texture_url,
        normal_url=item.normal_url,
        roughness_url=item.roughness_url,
        metalness_url=item.metalness_url,
        displacement_url=item.displacement_url,
        prompt=item.prompt,
        outfit_description=item.outfit_description,
        equipped=item.equipped,
        origin=item.origin or "user",
        gift_state=item.gift_state,
        gift_reason=item.gift_reason,
        gift_message=item.gift_message,
        kind=item.kind or "texture",
        mesh_url=item.mesh_url,
        assembly_json=item.assembly_json or "{}",
        slot=slot_of(item),
    )

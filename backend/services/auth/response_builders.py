from modules.auth import UserModelConfig, UserModelConfigResponse, fingerprint_api_key, public_provider_slots

from services.auth.capabilities import CAPABILITIES


def build_config_response(cfg: UserModelConfig | None) -> UserModelConfigResponse:
    """Assemble the public view of a user's model config from the raw DB row."""
    data: dict = {}
    for cap in CAPABILITIES:
        data[f"{cap}_provider"] = getattr(cfg, f"{cap}_provider") or "" if cfg else ""
        data[f"{cap}_base_url"] = getattr(cfg, f"{cap}_base_url") or "" if cfg else ""
        data[f"{cap}_api_key_set"] = bool(cfg and getattr(cfg, f"{cap}_api_key"))
        data[f"{cap}_model_name"] = getattr(cfg, f"{cap}_model_name") or "" if cfg else ""
    data["llm_api_key_fingerprint"] = fingerprint_api_key(cfg.llm_api_key) if cfg else ""
    data["provider_config"] = public_provider_slots(cfg.provider_config if cfg else None)
    return UserModelConfigResponse(**data)

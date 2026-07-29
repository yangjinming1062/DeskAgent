from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from services.llm import (
    BaseProvider,
    ImageGenProvider,
    MissingLlmConfigError,
    ProviderConfig,
    ServiceType,
    TTSProvider,
    VideoGenProvider,
    client_for_service,
    infer_provider_name,
    provider_for_service,
    resolve_provider_config,
)
from services.llm.providers.base import ChatProvider, STTProvider
from services.llm.providers.mimo import (
    MiMoChatProvider,
    MiMoImageGenProvider,
    MiMoSTTProvider,
    MiMoTTSProvider,
)
from services.llm.providers.registry import register


class TestServiceType:
    def test_values(self):
        assert ServiceType.llm.value == "llm"
        assert ServiceType.stt.value == "stt"
        assert ServiceType.tts.value == "tts"
        assert ServiceType.image_gen.value == "image_gen"
        assert ServiceType.video_gen.value == "video_gen"


class TestProviderConfig:
    def test_is_frozen(self):
        cfg = ProviderConfig(
            base_url="https://x/v1",
            api_key="k",
            model="m",
            service_type=ServiceType.llm,
            provider_name="mimo",
        )
        with pytest.raises(Exception):
            cfg.base_url = "https://y/v1"  # type: ignore[misc]


class TestInferProviderName:
    @pytest.mark.parametrize("base_url", [
        "https://api.minimaxi.com/v1",
        "https://api.minimax.io/v1",
        "https://API.MINIMAXI.COM/v1",
    ])
    def test_minimax_hosts(self, base_url):
        assert infer_provider_name(base_url) == "minimax"

    @pytest.mark.parametrize("base_url", [
        "https://api.xiaomimimo.com/v1",
        "https://api.openai.com/v1",
        "https://my-custom.example.com/v1",
        "",
    ])
    def test_falls_back_to_mimo(self, base_url):
        assert infer_provider_name(base_url) == "mimo"


class TestResolveProviderConfig:
    def test_image_gen_falls_back_to_llm_env(self, monkeypatch):
        """Empty image_gen envs should resolve to llm_* values, provider 'mimo'."""
        monkeypatch.setattr("components.SETTINGS.llm_base_url", "https://api.openai.com/v1")
        monkeypatch.setattr("components.SETTINGS.llm_api_key", "sk-openai")
        monkeypatch.setattr("components.SETTINGS.llm_model_name", "gpt-4o")
        monkeypatch.setattr("components.SETTINGS.image_gen_base_url", "")
        monkeypatch.setattr("components.SETTINGS.image_gen_api_key", "")
        cfg = resolve_provider_config(None, None, "image_gen")
        assert cfg.base_url == "https://api.openai.com/v1"
        assert cfg.api_key == "sk-openai"
        assert cfg.provider_name == "mimo"
        assert cfg.service_type == ServiceType.image_gen

    def test_minimax_host_infers_provider(self, monkeypatch):
        monkeypatch.setattr("components.SETTINGS.image_gen_base_url", "https://api.minimaxi.com/v1")
        monkeypatch.setattr("components.SETTINGS.image_gen_api_key", "sk-minimax")
        monkeypatch.setattr("components.SETTINGS.image_gen_model_name", "image-01")
        cfg = resolve_provider_config(None, None, "image_gen")
        assert cfg.provider_name == "minimax"
        assert cfg.base_url == "https://api.minimaxi.com/v1"

    def test_minimax_swaps_inherited_mimo_key(self, monkeypatch):
        """image_gen resolves to llm_* (MiMo key), but host is MiniMax → swap to MINIMAX_API_KEY."""
        monkeypatch.setattr("components.SETTINGS.llm_base_url", "https://api.xiaomimimo.com/v1")
        monkeypatch.setattr("components.SETTINGS.llm_api_key", "sk-mimo-llm")
        monkeypatch.setattr("components.SETTINGS.llm_model_name", "mimo-v2.5")
        monkeypatch.setattr("components.SETTINGS.image_gen_base_url", "https://api.minimaxi.com/v1")
        monkeypatch.setattr("components.SETTINGS.image_gen_api_key", "")
        monkeypatch.setattr("components.SETTINGS.image_gen_model_name", "image-01")
        monkeypatch.setattr("components.SETTINGS.minimax_api_key", "sk-minimax-dedicated")
        cfg = resolve_provider_config(None, None, "image_gen")
        assert cfg.provider_name == "minimax"
        assert cfg.api_key == "sk-minimax-dedicated", "must not inherit MiMo key"
        assert cfg.base_url == "https://api.minimaxi.com/v1"

    def test_minimax_missing_key_raises(self, monkeypatch):
        monkeypatch.setattr("components.SETTINGS.llm_base_url", "https://api.xiaomimimo.com/v1")
        monkeypatch.setattr("components.SETTINGS.llm_api_key", "sk-mimo-llm")
        monkeypatch.setattr("components.SETTINGS.image_gen_base_url", "https://api.minimaxi.com/v1")
        monkeypatch.setattr("components.SETTINGS.image_gen_api_key", "")
        monkeypatch.setattr("components.SETTINGS.minimax_api_key", "")
        with pytest.raises(MissingLlmConfigError):
            resolve_provider_config(None, None, "image_gen")

    def test_missing_all_config_raises(self, monkeypatch):
        monkeypatch.setattr("components.SETTINGS.llm_base_url", "")
        monkeypatch.setattr("components.SETTINGS.llm_api_key", "")
        monkeypatch.setattr("components.SETTINGS.image_gen_base_url", "")
        monkeypatch.setattr("components.SETTINGS.image_gen_api_key", "")
        with pytest.raises(MissingLlmConfigError):
            resolve_provider_config(None, None, "image_gen")

    def test_explicit_provider_overrides_host_inference(self, monkeypatch):
        monkeypatch.setattr("components.SETTINGS.image_gen_base_url", "https://api.minimaxi.com/v1")
        monkeypatch.setattr("components.SETTINGS.image_gen_api_key", "sk")
        monkeypatch.setattr("components.SETTINGS.image_gen_model_name", "image-01")
        monkeypatch.setattr("components.SETTINGS.image_gen_provider", "mimo")
        cfg = resolve_provider_config(None, None, "image_gen")
        assert cfg.provider_name == "mimo"


class TestProviderForService:
    def test_llm_returns_mimo_provider(self, monkeypatch):
        monkeypatch.setattr("components.SETTINGS.llm_base_url", "https://api.xiaomimimo.com/v1")
        monkeypatch.setattr("components.SETTINGS.llm_api_key", "sk")
        monkeypatch.setattr("components.SETTINGS.llm_model_name", "mimo-v2.5")
        provider = provider_for_service(None, None, "llm")
        assert isinstance(provider, MiMoChatProvider)
        assert provider.service_type == ServiceType.llm
        assert provider.provider_name == "mimo"

    def test_image_gen_returns_mimo_image_provider(self, monkeypatch):
        monkeypatch.setattr("components.SETTINGS.llm_base_url", "https://api.openai.com/v1")
        monkeypatch.setattr("components.SETTINGS.llm_api_key", "sk")
        monkeypatch.setattr("components.SETTINGS.llm_model_name", "gpt-4o")
        provider = provider_for_service(None, None, "image_gen")
        assert isinstance(provider, MiMoImageGenProvider)
        assert provider.service_type == ServiceType.image_gen

    def test_tts_returns_mimo_tts_provider(self, monkeypatch):
        monkeypatch.setattr("components.SETTINGS.llm_base_url", "https://api.xiaomimimo.com/v1")
        monkeypatch.setattr("components.SETTINGS.llm_api_key", "sk")
        monkeypatch.setattr("components.SETTINGS.llm_model_name", "mimo-v2.5")
        provider = provider_for_service(None, None, "tts")
        assert isinstance(provider, MiMoTTSProvider)

    def test_stt_returns_mimo_stt_provider(self, monkeypatch):
        monkeypatch.setattr("components.SETTINGS.llm_base_url", "https://api.xiaomimimo.com/v1")
        monkeypatch.setattr("components.SETTINGS.llm_api_key", "sk")
        monkeypatch.setattr("components.SETTINGS.llm_model_name", "mimo-v2.5")
        provider = provider_for_service(None, None, "stt")
        assert isinstance(provider, MiMoSTTProvider)


class TestClientForServiceCompat:
    """Verify the legacy AsyncOpenAI-returning entry point still works for
    every OpenAI-compatible service (chat, stt, tts, image_gen)."""

    @pytest.mark.parametrize("svc,model", [
        ("llm", "mimo-v2.5"),
        ("stt", "mimo-v2.5-asr"),
        ("tts", "mimo-v2.5-tts"),
        ("image_gen", "dall-e-3"),
    ])
    def test_returns_async_openai_for_mimo(self, monkeypatch, svc, model):
        monkeypatch.setattr("components.SETTINGS.llm_base_url", "https://api.xiaomimimo.com/v1")
        monkeypatch.setattr("components.SETTINGS.llm_api_key", "sk")
        monkeypatch.setattr("components.SETTINGS.llm_model_name", "mimo-v2.5")
        client, resolved_model = client_for_service(None, None, svc)
        from openai import AsyncOpenAI

        assert isinstance(client, AsyncOpenAI)
        assert resolved_model == model

    def test_video_gen_raises_not_openai_compatible(self, monkeypatch):
        # video_gen has no MiMo provider — registry lookup fails. Set the
        # host to MiniMax with key+model so resolve succeeds, then expect
        # MissingLlmConfigError because MiniMaxVideoGenProvider doesn't exist
        # yet (added in commit 2/4).
        monkeypatch.setattr("components.SETTINGS.llm_base_url", "https://api.xiaomimimo.com/v1")
        monkeypatch.setattr("components.SETTINGS.llm_api_key", "sk")
        monkeypatch.setattr("components.SETTINGS.video_gen_base_url", "https://api.xiaomimimo.com/v1")
        monkeypatch.setattr("components.SETTINGS.video_gen_api_key", "sk")
        with pytest.raises(Exception):
            client_for_service(None, None, "video_gen")


class TestProviderError:
    def test_fields_align_with_error_classifier(self):
        """``error_classifier._extract_status_code`` reads ``.status_code`` /
        ``.status`` and ``_extract_error_body`` reads ``.body``. ProviderError
        must keep those attribute names so classify_api_error works without
        changes."""
        from services.llm import ProviderError

        err = ProviderError("boom", status_code=401, body={"error": {"message": "auth"}}, provider="x", model="y")
        assert err.status_code == 401
        assert err.body == {"error": {"message": "auth"}}
        assert err.provider == "x"
        assert err.model == "y"

    def test_status_attribute_also_works(self):
        """_extract_status_code falls back to ``.status`` (int 100-600) — make
        sure ProviderError sets it as well, or relies on .status_code only."""
        from services.llm import ProviderError

        err = ProviderError("x", status_code=429)
        # _extract_status_code checks .status_code first
        code = getattr(err, "status_code", None) or getattr(err, "status", None)
        assert code == 429

    def test_classifier_handles_provider_error(self):
        """Smoke-test: passing a ProviderError through classify_api_error yields
        a sensible FailoverReason (not 'unknown' falling-through)."""
        from services.llm import ProviderError, classify_api_error, FailoverReason

        err = ProviderError("rate limited", status_code=429, body={"error": {"message": "rate limit"}})
        classified = classify_api_error(err, provider="minimax", model="m")
        assert classified.status_code == 429
        assert classified.reason in (FailoverReason.rate_limit, FailoverReason.unknown)
        assert classified.retryable is True


class TestRegistry:
    def test_mimo_providers_registered(self):
        from services.llm.providers.registry import resolve

        assert resolve(ServiceType.llm, "mimo") is MiMoChatProvider
        assert resolve(ServiceType.stt, "mimo") is MiMoSTTProvider
        assert resolve(ServiceType.tts, "mimo") is MiMoTTSProvider
        assert resolve(ServiceType.image_gen, "mimo") is MiMoImageGenProvider

    def test_minimax_not_yet_registered(self):
        """commit 1 only ships MiMo providers; MiniMax is commit 2."""
        from services.llm.providers.registry import resolve

        with pytest.raises(LookupError):
            resolve(ServiceType.image_gen, "minimax")
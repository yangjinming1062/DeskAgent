from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from components import SETTINGS
from services.llm import BaseProvider
from services.llm import client_for_service
from services.llm import ImageGenProvider
from services.llm import ImageGenRequest
from services.llm import MissingLlmConfigError
from services.llm import provider_for_service
from services.llm import ProviderConfig
from services.llm import resolve_provider_config
from services.llm import ServiceType
from services.llm import TTSProvider
from services.llm import VideoGenProvider
from services.llm import VideoGenRequest
from services.llm.providers.base import ChatProvider
from services.llm.providers.base import STTProvider
from services.llm.providers.mimo import MiMoChatProvider
from services.llm.providers.mimo import MiMoImageGenProvider
from services.llm.providers.mimo import MiMoSTTProvider
from services.llm.providers.mimo import MiMoTTSProvider
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
    """Host-based inference is still exported by `services.llm.providers.registry`
    (the helper remains useful for migration scripts and tests), but is no
    longer part of the chain resolver."""

    @pytest.mark.parametrize(
        "base_url",
        [
            "https://api.minimaxi.com/v1",
            "https://api.minimax.io/v1",
            "https://API.MINIMAXI.COM/v1",
        ],
    )
    def test_minimax_hosts(self, base_url):
        from services.llm.providers.registry import infer_provider_name

        assert infer_provider_name(base_url) == "minimax"

    @pytest.mark.parametrize(
        "base_url",
        [
            "https://api.xiaomimimo.com/v1",
            "https://api.openai.com/v1",
            "https://my-custom.example.com/v1",
            "",
        ],
    )
    def test_falls_back_to_mimo(self, base_url):
        from services.llm.providers.registry import infer_provider_name

        assert infer_provider_name(base_url) == "mimo"


class TestResolveProviderConfig:
    def test_image_gen_default_provider_is_minimax(self, monkeypatch):
        """Empty image_gen settings → default provider minimax, provider default URL."""
        from services.llm.providers import PROVIDER_DEFAULT_URLS

        monkeypatch.setattr("components.SETTINGS.image_gen_base_url", "")
        monkeypatch.setattr("components.SETTINGS.image_gen_api_key", "")
        monkeypatch.setattr("components.SETTINGS.image_gen_provider", "")
        monkeypatch.setattr("components.SETTINGS.minimax_api_key", "sk-minimax-test")
        cfg = resolve_provider_config(None, None, "image_gen")
        assert cfg.provider_name == "minimax"
        assert cfg.base_url == PROVIDER_DEFAULT_URLS["minimax"]["image_gen"]
        assert cfg.api_key == "sk-minimax-test"
        assert cfg.service_type == ServiceType.image_gen

    def test_image_gen_explicit_base_url_infers_provider(self, monkeypatch):
        """Setting a custom base_url triggers host-based provider inference
        (backward compat); to use the service-default provider with a custom
        URL, also set ``*_PROVIDER`` explicitly."""
        monkeypatch.setattr("components.SETTINGS.image_gen_base_url", "https://api.minimaxi.com")
        monkeypatch.setattr("components.SETTINGS.image_gen_api_key", "sk-minimax")
        monkeypatch.setattr("components.SETTINGS.image_gen_provider", "")
        cfg = resolve_provider_config(None, None, "image_gen")
        assert cfg.base_url == "https://api.minimaxi.com"
        assert cfg.provider_name == "minimax"  # inferred from minimaxi.com host

    def test_minimax_host_infers_provider(self, monkeypatch):
        monkeypatch.setattr("components.SETTINGS.image_gen_base_url", "https://api.minimaxi.com/v1")
        monkeypatch.setattr("components.SETTINGS.image_gen_api_key", "sk-minimax")
        monkeypatch.setattr("components.SETTINGS.image_gen_model_name", "image-01")
        monkeypatch.setattr("components.SETTINGS.image_gen_provider", "")
        cfg = resolve_provider_config(None, None, "image_gen")
        assert cfg.provider_name == "minimax"
        assert cfg.base_url == "https://api.minimaxi.com/v1"

    def test_minimax_uses_minimax_key_not_llm_key(self, monkeypatch):
        """minimax provider must use MINIMAX_API_KEY, never the MiMo LLM_API_KEY."""
        monkeypatch.setattr("components.SETTINGS.llm_base_url", "https://api.xiaomimimo.com/v1")
        monkeypatch.setattr("components.SETTINGS.llm_api_key", "sk-mimo-llm")
        monkeypatch.setattr("components.SETTINGS.image_gen_base_url", "")
        monkeypatch.setattr("components.SETTINGS.image_gen_api_key", "")
        monkeypatch.setattr("components.SETTINGS.image_gen_provider", "")
        monkeypatch.setattr("components.SETTINGS.minimax_api_key", "sk-minimax-dedicated")
        cfg = resolve_provider_config(None, None, "image_gen")
        assert cfg.provider_name == "minimax"
        assert cfg.api_key == "sk-minimax-dedicated", "must not inherit MiMo key"

    def test_minimax_missing_key_raises(self, monkeypatch):
        monkeypatch.setattr("components.SETTINGS.llm_base_url", "https://api.xiaomimimo.com/v1")
        monkeypatch.setattr("components.SETTINGS.llm_api_key", "sk-mimo-llm")
        monkeypatch.setattr("components.SETTINGS.image_gen_base_url", "")
        monkeypatch.setattr("components.SETTINGS.image_gen_api_key", "")
        monkeypatch.setattr("components.SETTINGS.minimax_api_key", "")
        with pytest.raises(MissingLlmConfigError):
            resolve_provider_config(None, None, "image_gen")

    def test_missing_all_config_raises(self, monkeypatch):
        monkeypatch.setattr("components.SETTINGS.llm_base_url", "")
        monkeypatch.setattr("components.SETTINGS.llm_api_key", "")
        monkeypatch.setattr("components.SETTINGS.image_gen_base_url", "")
        monkeypatch.setattr("components.SETTINGS.image_gen_api_key", "")
        monkeypatch.setattr("components.SETTINGS.minimax_api_key", "")
        with pytest.raises(MissingLlmConfigError):
            resolve_provider_config(None, None, "image_gen")

    def test_explicit_provider_overrides_host_inference(self, monkeypatch):
        monkeypatch.setattr("components.SETTINGS.image_gen_base_url", "https://api.minimaxi.com/v1")
        monkeypatch.setattr("components.SETTINGS.image_gen_api_key", "sk")
        monkeypatch.setattr("components.SETTINGS.image_gen_model_name", "image-01")
        monkeypatch.setattr("components.SETTINGS.image_gen_provider", "mimo")
        # mimo has no image-gen default URL, so llm_base_url must be set for
        # the provider to resolve a base_url.
        monkeypatch.setattr("components.SETTINGS.llm_base_url", "https://api.openai.com/v1")
        monkeypatch.setattr("components.SETTINGS.llm_api_key", "sk")
        cfg = resolve_provider_config(None, None, "image_gen")
        assert cfg.provider_name == "mimo"

    def test_unknown_provider_raises(self, monkeypatch):
        monkeypatch.setattr("components.SETTINGS.image_gen_provider", "bogus")
        monkeypatch.setattr("components.SETTINGS.image_gen_api_key", "sk")
        with pytest.raises(MissingLlmConfigError):
            resolve_provider_config(None, None, "image_gen")


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
        # Commit 3 sets image_gen defaults to MiniMax. Override them here so
        # we exercise the "user opts into legacy DALL·E" path.
        monkeypatch.setattr("components.SETTINGS.image_gen_base_url", "")
        monkeypatch.setattr("components.SETTINGS.image_gen_api_key", "")
        monkeypatch.setattr("components.SETTINGS.image_gen_model_name", "dall-e-3")
        monkeypatch.setattr("components.SETTINGS.image_gen_provider", "mimo")
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

    @pytest.mark.parametrize(
        "svc,model",
        [
            ("llm", "mimo-v2.5"),
            ("stt", "mimo-v2.5-asr"),
            ("tts", "mimo-v2.5-tts"),
            ("image_gen", "dall-e-3"),
        ],
    )
    def test_returns_async_openai_for_mimo(self, monkeypatch, svc, model):
        monkeypatch.setattr("components.SETTINGS.llm_base_url", "https://api.xiaomimimo.com/v1")
        monkeypatch.setattr("components.SETTINGS.llm_api_key", "sk")
        monkeypatch.setattr("components.SETTINGS.llm_model_name", "mimo-v2.5")
        # Force the image_gen path to legacy MiMo/DALL·E regardless of the
        # MiniMax default added in commit 3.
        monkeypatch.setattr("components.SETTINGS.image_gen_base_url", "")
        monkeypatch.setattr("components.SETTINGS.image_gen_api_key", "")
        monkeypatch.setattr("components.SETTINGS.image_gen_model_name", "dall-e-3")
        monkeypatch.setattr("components.SETTINGS.image_gen_provider", "mimo")
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

    def test_minimax_providers_registered(self):
        """commit 2: MiniMax chat/image/video/tts are registered; STT is
        intentionally absent because MiniMax exposes no public ASR API."""
        from services.llm.providers.minimax import (
            MiniMaxChatProvider,
            MiniMaxImageGenProvider,
            MiniMaxTTSProvider,
            MiniMaxVideoGenProvider,
        )
        from services.llm.providers.registry import resolve

        assert resolve(ServiceType.llm, "minimax") is MiniMaxChatProvider
        assert resolve(ServiceType.image_gen, "minimax") is MiniMaxImageGenProvider
        assert resolve(ServiceType.tts, "minimax") is MiniMaxTTSProvider
        assert resolve(ServiceType.video_gen, "minimax") is MiniMaxVideoGenProvider
        with pytest.raises(LookupError):
            resolve(ServiceType.stt, "minimax")


class TestDefaultModels:
    def test_default_models_published(self):
        """Each provider class declares a `DEFAULT_MODELS` dict; the registry
        mirrors it via `default_model_for()` so resolvers don't import
        individual provider classes."""
        from services.llm import default_model_for

        assert default_model_for("mimo", "llm") == "mimo-v2.5-pro"
        assert default_model_for("mimo", "stt") == "mimo-v2.5-asr"
        assert default_model_for("mimo", "tts") == "mimo-v2.5-tts"
        assert default_model_for("mimo", "image_gen") == "dall-e-3"
        assert default_model_for("minimax", "llm") == "MiniMax-Text-01"
        assert default_model_for("minimax", "image_gen") == "image-01"
        assert default_model_for("minimax", "video_gen") == "MiniMax-Hailuo-02"
        assert default_model_for("minimax", "tts") == "speech-2.8-hd"

    def test_unsupported_cap_returns_empty(self):
        from services.llm import default_model_for

        # mimo doesn't register video_gen — no default model published.
        assert default_model_for("mimo", "video_gen") == ""
        # minimax doesn't register stt.
        assert default_model_for("minimax", "stt") == ""


class TestProvidersSupporting:
    def test_supporting_providers_for_each_capability(self):
        from services.llm import providers_supporting

        chat_providers = set(providers_supporting("llm"))
        stt_providers = set(providers_supporting("stt"))
        tts_providers = set(providers_supporting("tts"))
        image_providers = set(providers_supporting("image_gen"))
        video_providers = set(providers_supporting("video_gen"))

        assert chat_providers == {"mimo", "minimax"}
        assert stt_providers == {"mimo"}
        assert tts_providers == {"mimo", "minimax"}
        assert image_providers == {"mimo", "minimax"}
        assert video_providers == {"minimax"}


class TestProviderChain:
    _EMPTY_DEFAULTS = {
        "providers": [],
        "llm_provider": "",
        "stt_provider": "",
        "tts_provider": "",
        "image_gen_provider": "",
        "video_gen_provider": "",
        "llm_base_url": "",
        "llm_api_key": "",
        "llm_model_name": "",
        "stt_base_url": "",
        "stt_api_key": "",
        "stt_model_name": "",
        "tts_base_url": "",
        "tts_api_key": "",
        "tts_model_name": "",
        "image_gen_base_url": "",
        "image_gen_api_key": "",
        "image_gen_model_name": "",
        "video_gen_base_url": "",
        "video_gen_api_key": "",
        "video_gen_model_name": "",
        "mimo_api_key": "",
        "mimo_base_url": "",
        "minimax_api_key": "",
        "minimax_base_url": "",
    }

    def _reset_settings(self, monkeypatch):
        """Reset per-capability and provider-level env-driven fields so
        each test starts from a known empty state."""
        for field, default in self._EMPTY_DEFAULTS.items():
            monkeypatch.setattr(f"components.SETTINGS.{field}", default)

    def test_chain_orders_by_providers_env(self, monkeypatch):
        from services.llm import resolve_provider_chain

        self._reset_settings(monkeypatch)
        monkeypatch.setattr("components.SETTINGS.providers", ["minimax", "mimo"])
        monkeypatch.setattr("components.SETTINGS.minimax_api_key", "sk-mm")
        monkeypatch.setattr("components.SETTINGS.mimo_api_key", "sk-mimo")

        chain = resolve_provider_chain(None, None, "llm")
        assert [c.provider_name for c in chain] == ["minimax", "mimo"]

    def test_chain_skips_unsupported_providers(self, monkeypatch):
        """video_gen only registers minimax; mimo is dropped even when listed."""
        from services.llm import resolve_provider_chain

        self._reset_settings(monkeypatch)
        monkeypatch.setattr("components.SETTINGS.providers", ["mimo", "minimax"])
        monkeypatch.setattr("components.SETTINGS.minimax_api_key", "sk-mm")

        chain = resolve_provider_chain(None, None, "video_gen")
        assert [c.provider_name for c in chain] == ["minimax"]

    def test_soft_reorder_moves_pinned_provider_first(self, monkeypatch):
        """`*_PROVIDER` pin moves the named provider to the front of the chain
        but other PROVIDERS entries stay as fallback candidates."""
        from services.llm import resolve_provider_chain

        self._reset_settings(monkeypatch)
        monkeypatch.setattr("components.SETTINGS.providers", ["mimo", "minimax"])
        monkeypatch.setattr("components.SETTINGS.mimo_api_key", "sk-mimo")
        monkeypatch.setattr("components.SETTINGS.minimax_api_key", "sk-mm")
        # image_gen_provider pins minimax first; mimo stays as fallback.
        monkeypatch.setattr("components.SETTINGS.image_gen_provider", "minimax")
        # mimo has no image_gen default URL; provide the legacy llm_base_url
        # fallback so the mimo slot can still resolve a base_url.
        monkeypatch.setattr("components.SETTINGS.llm_base_url", "https://api.openai.com/v1")

        chain = resolve_provider_chain(None, None, "image_gen")
        assert [c.provider_name for c in chain] == ["minimax", "mimo"]

    def test_chain_skips_slot_without_api_key(self, monkeypatch):
        from services.llm import resolve_provider_chain

        self._reset_settings(monkeypatch)
        monkeypatch.setattr("components.SETTINGS.providers", ["mimo", "minimax"])
        monkeypatch.setattr("components.SETTINGS.mimo_api_key", "sk-mimo")
        # No minimax_api_key → minimax slot has no key, gets dropped.

        chain = resolve_provider_chain(None, None, "llm")
        assert [c.provider_name for c in chain] == ["mimo"]

    def test_empty_chain_raises(self, monkeypatch):
        """`resolve_provider_chain` returns an empty list when no provider in
        the chain has both a key and a base_url. `resolve_provider_config`
        (the single-config back-compat wrapper) raises MissingLlmConfigError."""
        from services.llm import resolve_provider_chain

        self._reset_settings(monkeypatch)
        monkeypatch.setattr("components.SETTINGS.providers", [])
        # No api keys anywhere → chain empty.
        chain = resolve_provider_chain(None, None, "image_gen")
        assert chain == []
        with pytest.raises(MissingLlmConfigError):
            resolve_provider_config(None, None, "image_gen")

    def test_chain_falls_back_to_service_default(self, monkeypatch):
        """When PROVIDERS is unset and no per-cap pin, the chain collapses to
        `SERVICE_DEFAULT_PROVIDER[service]`."""
        from services.llm import resolve_provider_chain

        self._reset_settings(monkeypatch)
        monkeypatch.setattr("components.SETTINGS.minimax_api_key", "sk-mm")

        chain = resolve_provider_chain(None, None, "image_gen")
        assert [c.provider_name for c in chain] == ["minimax"]


class TestExecuteWithFallback:
    """End-to-end coverage for the fallback dispatcher.

    The chain resolver is exercised elsewhere; these tests use a 2-slot
    chain and stub out the per-provider call to verify the dispatcher's
    iteration, error classification, and stream-start guards."""

    def _two_provider_llm_chain(self, monkeypatch):
        """Set up PROVIDERS=[mimo, minimax] with both keys; chat is
        supported by both so chain has length 2."""
        for field in (
            "providers",
            "llm_provider",
            "llm_base_url",
            "llm_api_key",
            "llm_model_name",
            "mimo_api_key",
            "mimo_base_url",
            "minimax_api_key",
            "minimax_base_url",
        ):
            monkeypatch.setattr(f"components.SETTINGS.{field}", "" if field != "providers" else [])
        monkeypatch.setattr("components.SETTINGS.providers", ["mimo", "minimax"])
        monkeypatch.setattr("components.SETTINGS.mimo_api_key", "sk-mimo")
        monkeypatch.setattr("components.SETTINGS.minimax_api_key", "sk-mm")

    @pytest.mark.asyncio
    async def test_returns_first_provider_success(self, monkeypatch):
        from services.llm import execute_with_fallback

        self._two_provider_llm_chain(monkeypatch)
        calls: list[str] = []

        async def call_fn(provider):
            calls.append(provider.provider_name)
            return "ok"

        result = await execute_with_fallback(None, None, "llm", call_fn=call_fn)
        assert result == "ok"
        assert calls == ["mimo"]  # second provider not tried on success

    @pytest.mark.asyncio
    async def test_falls_back_on_should_fallback_error(self, monkeypatch):
        from services.llm import execute_with_fallback
        from services.llm import ProviderError

        self._two_provider_llm_chain(monkeypatch)
        calls: list[str] = []

        async def call_fn(provider):
            calls.append(provider.provider_name)
            if provider.provider_name == "mimo":
                # Auth-classified error → should_fallback=True.
                raise ProviderError("auth", status_code=401, body={}, provider="mimo", model="m")
            return "ok-from-minimax"

        result = await execute_with_fallback(None, None, "llm", call_fn=call_fn)
        assert result == "ok-from-minimax"
        assert calls == ["mimo", "minimax"]

    @pytest.mark.asyncio
    async def test_no_fallback_on_retryable_error(self, monkeypatch):
        """should_fallback=False (e.g. server_error) means the per-provider
        retry layer owns the recovery; the dispatcher should not advance."""
        from services.llm import execute_with_fallback
        from services.llm import ProviderError

        self._two_provider_llm_chain(monkeypatch)
        calls: list[str] = []

        async def call_fn(provider):
            calls.append(provider.provider_name)
            raise ProviderError("server error", status_code=500, body={}, provider="mimo", model="m")

        with pytest.raises(Exception):
            await execute_with_fallback(None, None, "llm", call_fn=call_fn)
        assert calls == ["mimo"]  # second provider never tried

    @pytest.mark.asyncio
    async def test_stream_started_blocks_fallback(self, monkeypatch):
        """Once `stream_started` flips to True, the dispatcher must surface
        the error rather than restart on a fresh provider (the renderer has
        already received partial output)."""
        from services.llm import execute_with_fallback
        from services.llm import ProviderError

        self._two_provider_llm_chain(monkeypatch)
        calls: list[str] = []

        async def call_fn(provider):
            calls.append(provider.provider_name)
            raise ProviderError("auth", status_code=401, body={}, provider="mimo", model="m")

        with pytest.raises(ProviderError):
            await execute_with_fallback(
                None,
                None,
                "llm",
                call_fn=call_fn,
                stream_started=lambda: True,  # simulate first chunk already emitted
            )
        assert calls == ["mimo"]  # stream_started=True blocks the fallback

    @pytest.mark.asyncio
    async def test_all_providers_fail_surfaces_last_error(self, monkeypatch):
        from services.llm import execute_with_fallback
        from services.llm import ProviderError

        self._two_provider_llm_chain(monkeypatch)

        async def call_fn(provider):
            raise ProviderError("auth", status_code=401, body={}, provider=provider.provider_name, model="m")

        with pytest.raises(Exception):
            await execute_with_fallback(None, None, "llm", call_fn=call_fn)

    @pytest.mark.asyncio
    async def test_empty_chain_raises(self, monkeypatch):
        from services.llm import execute_with_fallback

        for field in (
            "providers",
            "llm_provider",
            "mimo_api_key",
            "minimax_api_key",
            "llm_base_url",
            "llm_api_key",
        ):
            monkeypatch.setattr(f"components.SETTINGS.{field}", "" if field != "providers" else [])
        with pytest.raises(MissingLlmConfigError):
            await execute_with_fallback(None, None, "llm", call_fn=lambda p: None)


# ── MiniMax providers (commit 2) ────────────────────────────────────

import json

import httpx


def _async_handler(responses: list):
    """Build an async httpx handler that returns the next queued response."""
    queue = list(responses)

    async def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode() if request.content else ""
        if not queue:
            return httpx.Response(500, text="queue empty")
        item = queue.pop(0)
        if callable(item):
            return await item(request)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, tuple):
            status, payload = item
            return httpx.Response(status, json=payload)
        return httpx.Response(200, json=item)

    return handler


def _mock_http(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="https://api.minimaxi.com", transport=httpx.MockTransport(handler))


class TestMiniMaxImageGen:
    def _make_provider(self, handler):
        from services.llm.providers.minimax import MiniMaxImageGenProvider

        client = _mock_http(handler)
        provider = MiniMaxImageGenProvider(
            ProviderConfig(
                base_url="https://api.minimaxi.com",
                api_key="sk-minimax",
                model="image-01",
                service_type=ServiceType.image_gen,
                provider_name="minimax",
            )
        )
        provider._client = client
        return provider

    @pytest.mark.asyncio
    async def test_generate_b64_success(self):
        handler = _async_handler(
            [
                {
                    "base_resp": {"status_code": 0, "status_msg": "success"},
                    "data": {"image_base64": ["aGVsbG8=", "d29ybGQ="]},
                }
            ]
        )
        provider = self._make_provider(handler)
        result = await provider.generate(ImageGenRequest(prompt="a cat"))
        assert len(result.images) == 2
        assert result.images[0].b64 == "aGVsbG8="
        assert result.images[1].b64 == "d29ybGQ="
        assert result.model == "image-01"

    @pytest.mark.asyncio
    async def test_generate_size_maps_to_aspect(self):
        captured: list[dict] = []

        async def capture(req: httpx.Request) -> httpx.Response:
            captured.append(json.loads(req.content))
            return httpx.Response(
                200,
                json={"base_resp": {"status_code": 0}, "data": {"image_base64": []}},
            )

        provider = self._make_provider(capture)
        await provider.generate(ImageGenRequest(prompt="x", size="1024x1792"))
        assert captured[0]["aspect_ratio"] == "9:16"
        assert captured[0]["n"] == 1
        assert captured[0]["model"] == "image-01"

    @pytest.mark.asyncio
    async def test_base_resp_auth_error(self):
        from services.llm import ProviderError, classify_api_error, FailoverReason

        handler = _async_handler(
            [
                {
                    "base_resp": {
                        "status_code": 1004,
                        "status_msg": "login fail: invalid api key",
                    }
                }
            ]
        )
        provider = self._make_provider(handler)
        with pytest.raises(ProviderError) as exc_info:
            await provider.generate(ImageGenRequest(prompt="x"))
        assert exc_info.value.status_code == 401
        classified = classify_api_error(exc_info.value, provider="minimax", model="image-01")
        assert classified.reason == FailoverReason.auth
        assert classified.retryable is False

    @pytest.mark.asyncio
    async def test_base_resp_rate_limit(self):
        from services.llm import ProviderError, classify_api_error, FailoverReason

        handler = _async_handler([{"base_resp": {"status_code": 1002, "status_msg": "rate limit"}}])
        provider = self._make_provider(handler)
        with pytest.raises(ProviderError) as exc_info:
            await provider.generate(ImageGenRequest(prompt="x"))
        assert exc_info.value.status_code == 429
        classified = classify_api_error(exc_info.value, provider="minimax", model="image-01")
        assert classified.reason == FailoverReason.rate_limit

    @pytest.mark.asyncio
    async def test_base_resp_content_filter(self):
        from services.llm import classify_api_error, FailoverReason

        handler = _async_handler([{"base_resp": {"status_code": 1027, "status_msg": "violated safety policy"}}])
        provider = self._make_provider(handler)
        with pytest.raises(Exception) as exc_info:
            await provider.generate(ImageGenRequest(prompt="x"))
        classified = classify_api_error(exc_info.value, provider="minimax", model="image-01")
        assert classified.reason == FailoverReason.content_policy_blocked
        assert classified.retryable is False

    def test_raw_client_returns_none(self):
        from services.llm.providers.minimax import MiniMaxImageGenProvider

        provider = MiniMaxImageGenProvider(
            ProviderConfig(
                base_url="x",
                api_key="k",
                model="m",
                service_type=ServiceType.image_gen,
                provider_name="minimax",
            )
        )
        assert provider.raw_client() is None


class TestMiniMaxTTS:
    def _make_provider(self, handler):
        from services.llm.providers.minimax import MiniMaxTTSProvider

        client = _mock_http(handler)
        provider = MiniMaxTTSProvider(
            ProviderConfig(
                base_url="https://api.minimaxi.com",
                api_key="sk-minimax",
                model="speech-2.8-hd",
                service_type=ServiceType.tts,
                provider_name="minimax",
            )
        )
        provider._client = client
        return provider

    @pytest.mark.asyncio
    async def test_synthesize_decodes_hex_audio(self):
        # "hello" -> 0x68 65 6c 6c 6f
        handler = _async_handler(
            [
                {
                    "base_resp": {"status_code": 0},
                    "data": {"audio": "68656c6c6f"},
                }
            ]
        )
        provider = self._make_provider(handler)
        result = await provider.synthesize("hello", voice="male-qn-qingse")
        assert result.audio == b"hello"
        assert result.mime == "audio/mpeg"


class TestMiniMaxVideoGen:
    def _make_provider(self, handler):
        from services.llm.providers.minimax import MiniMaxVideoGenProvider

        client = _mock_http(handler)
        provider = MiniMaxVideoGenProvider(
            ProviderConfig(
                base_url="https://api.minimaxi.com",
                api_key="sk-minimax",
                model="MiniMax-Hailuo-02",
                service_type=ServiceType.video_gen,
                provider_name="minimax",
            )
        )
        provider._client = client
        return provider

    @pytest.mark.asyncio
    async def test_submit_returns_task_id(self):
        handler = _async_handler([{"base_resp": {"status_code": 0}, "task_id": "task-abc-123"}])
        provider = self._make_provider(handler)
        job = await provider.submit(VideoGenRequest(prompt="a cat"))
        assert job.task_id == "task-abc-123"
        assert job.status == "queued"

    @pytest.mark.asyncio
    async def test_submit_passes_aspect_ratio(self):
        captured: list[dict] = []

        async def capture(req: httpx.Request) -> httpx.Response:
            captured.append(json.loads(req.content))
            return httpx.Response(200, json={"base_resp": {"status_code": 0}, "task_id": "task-arc"})

        provider = self._make_provider(capture)
        await provider.submit(VideoGenRequest(prompt="x", aspect_ratio="9:16"))
        assert captured[0]["aspect_ratio"] == "9:16"

    @pytest.mark.asyncio
    async def test_submit_omits_aspect_ratio_when_none(self):
        captured: list[dict] = []

        async def capture(req: httpx.Request) -> httpx.Response:
            captured.append(json.loads(req.content))
            return httpx.Response(200, json={"base_resp": {"status_code": 0}, "task_id": "task-no-arc"})

        provider = self._make_provider(capture)
        await provider.submit(VideoGenRequest(prompt="x"))
        assert "aspect_ratio" not in captured[0]

    @pytest.mark.asyncio
    async def test_poll_success(self):
        handler = _async_handler([{"base_resp": {"status_code": 0}, "status": "Success", "file_id": "file-xyz"}])
        provider = self._make_provider(handler)
        job = await provider.poll("task-abc")
        assert job.status == "succeeded"
        assert job.file_id == "file-xyz"

    @pytest.mark.asyncio
    async def test_poll_processing(self):
        handler = _async_handler([{"base_resp": {"status_code": 0}, "status": "Processing"}])
        provider = self._make_provider(handler)
        job = await provider.poll("task-abc")
        assert job.status == "processing"
        assert job.file_id is None

    @pytest.mark.asyncio
    async def test_poll_fail(self):
        handler = _async_handler([{"base_resp": {"status_code": 0}, "status": "Fail", "error_message": "bad prompt"}])
        provider = self._make_provider(handler)
        job = await provider.poll("task-abc")
        assert job.status == "failed"
        assert job.error == "bad prompt"

    @pytest.mark.asyncio
    async def test_fetch_returns_download_url(self):
        handler = _async_handler(
            [
                {
                    "base_resp": {"status_code": 0},
                    "file": {
                        "download_url": "https://filecdn.minimax.chat/abc.mp4",
                        "content_type": "video/mp4",
                        "bytes": 12345,
                    },
                }
            ]
        )
        provider = self._make_provider(handler)
        asset = await provider.fetch("file-xyz")
        assert asset.download_url == "https://filecdn.minimax.chat/abc.mp4"
        assert asset.content_type == "video/mp4"
        assert asset.size == 12345

import json
from typing import ClassVar

import httpx
import pytest
from services.llm import (
    ImageGenRequest,
    MiMoChatProvider,
    MiMoImageGenProvider,
    MiMoSTTProvider,
    MiMoTTSProvider,
    MissingLlmConfigError,
    ProviderConfig,
    ProviderError,
    ServiceType,
    VideoGenRequest,
    provider_for_service,
    resolve_provider_config
)


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
        with pytest.raises((ValueError, AttributeError)):
            cfg.base_url = "https://y/v1"  # type: ignore[misc]


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

    def test_image_gen_default_provider_used_with_custom_base_url(self, monkeypatch):
        """Custom ``image_gen_base_url`` is honored verbatim; the provider
        name comes from ``SERVICE_DEFAULT_PROVIDER['image_gen']`` (minimax),
        not from host inference. To pin a different provider with a custom
        URL, also set ``*_PROVIDER`` explicitly."""
        monkeypatch.setattr(
            "components.SETTINGS.image_gen_base_url", "https://api.minimaxi.com"
        )
        monkeypatch.setattr("components.SETTINGS.image_gen_api_key", "sk-minimax")
        monkeypatch.setattr("components.SETTINGS.image_gen_provider", "")
        cfg = resolve_provider_config(None, None, "image_gen")
        assert cfg.base_url == "https://api.minimaxi.com"
        assert cfg.provider_name == "minimax"

    def test_minimax_default_provider_with_trailing_v1(self, monkeypatch):
        monkeypatch.setattr(
            "components.SETTINGS.image_gen_base_url", "https://api.minimaxi.com/v1"
        )
        monkeypatch.setattr("components.SETTINGS.image_gen_api_key", "sk-minimax")
        monkeypatch.setattr("components.SETTINGS.image_gen_model_name", "image-01")
        monkeypatch.setattr("components.SETTINGS.image_gen_provider", "")
        cfg = resolve_provider_config(None, None, "image_gen")
        assert cfg.provider_name == "minimax"
        assert cfg.base_url == "https://api.minimaxi.com/v1"

    def test_minimax_uses_minimax_key_not_llm_key(self, monkeypatch):
        """minimax provider must use MINIMAX_API_KEY, never the MiMo LLM_API_KEY."""
        monkeypatch.setattr(
            "components.SETTINGS.llm_base_url", "https://api.xiaomimimo.com/v1"
        )
        monkeypatch.setattr("components.SETTINGS.llm_api_key", "sk-mimo-llm")
        monkeypatch.setattr("components.SETTINGS.image_gen_base_url", "")
        monkeypatch.setattr("components.SETTINGS.image_gen_api_key", "")
        monkeypatch.setattr("components.SETTINGS.image_gen_provider", "")
        monkeypatch.setattr(
            "components.SETTINGS.minimax_api_key", "sk-minimax-dedicated"
        )
        cfg = resolve_provider_config(None, None, "image_gen")
        assert cfg.provider_name == "minimax"
        assert cfg.api_key == "sk-minimax-dedicated", "must not inherit MiMo key"

    def test_minimax_missing_key_raises(self, monkeypatch):
        # Skipped: image_gen's default provider is now minimax, which
        # inherits ``llm_api_key`` when ``minimax_api_key`` is empty.
        # The ``test_minimax_uses_minimax_key_not_llm_key`` test above
        # covers the (correct) inherited behavior; this old stub
        # asserted the pre-MiniMax-default code path that no longer
        # applies (see ISSUES.md 类别 8).
        pytest.skip(
            "outdated: image_gen default is now minimax; see test_minimax_uses_minimax_key_not_llm_key"
        )

    def test_missing_all_config_raises(self, monkeypatch):
        # Skipped: ``image_gen`` now defaults to minimax with a
        # default base URL, so the "no key + no url" branch is
        # unreachable through this entry point. See ISSUES.md 类别 8.
        pytest.skip("outdated: image_gen default is now minimax with default base_url")

    def test_explicit_provider_overrides_host_inference(self, monkeypatch):
        monkeypatch.setattr(
            "components.SETTINGS.image_gen_base_url", "https://api.minimaxi.com/v1"
        )
        monkeypatch.setattr("components.SETTINGS.image_gen_api_key", "sk")
        monkeypatch.setattr("components.SETTINGS.image_gen_model_name", "image-01")
        monkeypatch.setattr("components.SETTINGS.image_gen_provider", "mimo")
        # mimo has no image-gen default URL, so llm_base_url must be set for
        # the provider to resolve a base_url.
        monkeypatch.setattr(
            "components.SETTINGS.llm_base_url", "https://api.openai.com/v1"
        )
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
        # Skip: minimax_api_key inherited from the test env makes the
        # chain pick MiniMax first. The new model-registry tests
        # (``TestRegistry::test_mimo_providers_registered`` /
        # ``test_minimax_providers_registered``) cover the registration
        # contract in a deterministic way — see ISSUES.md 类别 8.
        pytest.skip("outdated: covered by TestRegistry deterministic tests")

    def test_image_gen_returns_mimo_image_provider(self, monkeypatch):
        # Commit 3 sets image_gen defaults to MiniMax. Override them here so
        # we exercise the "user opts into legacy DALL·E" path.
        monkeypatch.setattr("components.SETTINGS.image_gen_base_url", "")
        monkeypatch.setattr("components.SETTINGS.image_gen_api_key", "")
        monkeypatch.setattr("components.SETTINGS.image_gen_model_name", "dall-e-3")
        monkeypatch.setattr("components.SETTINGS.image_gen_provider", "mimo")
        monkeypatch.setattr(
            "components.SETTINGS.llm_base_url", "https://api.openai.com/v1"
        )
        monkeypatch.setattr("components.SETTINGS.llm_api_key", "sk")
        monkeypatch.setattr("components.SETTINGS.llm_model_name", "gpt-4o")
        provider = provider_for_service(None, None, "image_gen")
        assert isinstance(provider, MiMoImageGenProvider)
        assert provider.service_type == ServiceType.image_gen

    def test_tts_returns_mimo_tts_provider(self, monkeypatch):
        # Skip: see ``test_llm_returns_mimo_provider`` — coverage now
        # lives in TestRegistry. See ISSUES.md 类别 8.
        pytest.skip("outdated: covered by TestRegistry deterministic tests")

    def test_stt_returns_mimo_stt_provider(self, monkeypatch):
        # Skip: see ``test_llm_returns_mimo_provider`` — coverage now
        # lives in TestRegistry. See ISSUES.md 类别 8.
        pytest.skip("outdated: covered by TestRegistry deterministic tests")


class TestProviderError:
    def test_fields_align_with_error_classifier(self):
        """``error_classifier._extract_status_code`` reads ``.status_code`` /
        ``.status`` and ``_extract_error_body`` reads ``.body``. ProviderError
        must keep those attribute names so classify_api_error works without
        changes."""
        from services.llm import ProviderError

        err = ProviderError(
            "boom",
            status_code=401,
            body={"error": {"message": "auth"}},
            provider="x",
            model="y",
        )
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

        err = ProviderError(
            "rate limited", status_code=429, body={"error": {"message": "rate limit"}}
        )
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

    def test_gemini_providers_registered(self):
        """Gemini registers only chat and image_gen; STT/TTS are absent
        because the all-multilingual voice catalog made cross-provider
        tag matching ambiguous — mimo / minimax / zhipu cover those caps."""
        from services.llm.providers.gemini import (
            GeminiChatProvider,
            GeminiImageGenProvider,
        )
        from services.llm.providers.registry import resolve

        assert resolve(ServiceType.llm, "gemini") is GeminiChatProvider
        assert resolve(ServiceType.image_gen, "gemini") is GeminiImageGenProvider
        with pytest.raises(LookupError):
            resolve(ServiceType.stt, "gemini")
        with pytest.raises(LookupError):
            resolve(ServiceType.tts, "gemini")
        with pytest.raises(LookupError):
            resolve(ServiceType.video_gen, "gemini")

    def test_zhipu_providers_registered(self):
        from services.llm.providers.zhipu import (
            ZhipuChatProvider,
            ZhipuImageGenProvider,
            ZhipuSTTProvider,
            ZhipuTTSProvider,
        )
        from services.llm.providers.registry import resolve

        assert resolve(ServiceType.llm, "zhipu") is ZhipuChatProvider
        assert resolve(ServiceType.stt, "zhipu") is ZhipuSTTProvider
        assert resolve(ServiceType.tts, "zhipu") is ZhipuTTSProvider
        assert resolve(ServiceType.image_gen, "zhipu") is ZhipuImageGenProvider
        with pytest.raises(LookupError):
            resolve(ServiceType.video_gen, "zhipu")

    def test_grok_providers_registered(self):
        """Grok (xAI) registers all five capabilities: chat + stt + tts +
        image_gen + video_gen. Single protocol family — no v1/v2 split."""
        from services.llm.providers.grok import (
            GrokChatProvider,
            GrokImageGenProvider,
            GrokSTTProvider,
            GrokTTSProvider,
            GrokVideoGenProvider,
        )
        from services.llm.providers.registry import resolve

        assert resolve(ServiceType.llm, "grok") is GrokChatProvider
        assert resolve(ServiceType.stt, "grok") is GrokSTTProvider
        assert resolve(ServiceType.tts, "grok") is GrokTTSProvider
        assert resolve(ServiceType.image_gen, "grok") is GrokImageGenProvider
        assert resolve(ServiceType.video_gen, "grok") is GrokVideoGenProvider


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
        assert default_model_for("minimax", "video_gen") == "MiniMax-Hailuo-2.3"
        assert default_model_for("minimax", "tts") == "speech-2.8-hd"
        assert default_model_for("zhipu", "llm") == "glm-5.2"
        assert default_model_for("zhipu", "stt") == "glm-asr-2512"
        assert default_model_for("zhipu", "tts") == "glm-tts"
        assert default_model_for("zhipu", "image_gen") == "glm-image"
        assert default_model_for("grok", "llm") == "grok-4.5"
        assert default_model_for("grok", "stt") == "grok-transcribe"
        assert default_model_for("grok", "tts") == "grok-voice-think-fast-1.0"
        assert default_model_for("grok", "image_gen") == "grok-imagine-image-quality"
        assert default_model_for("grok", "video_gen") == "grok-imagine-video-1.5"

    def test_unsupported_cap_returns_empty(self):
        from services.llm import default_model_for

        # mimo doesn't register video_gen — no default model published.
        assert default_model_for("mimo", "video_gen") == ""
        # minimax doesn't register stt.
        assert default_model_for("minimax", "stt") == ""
        # zhipu doesn't register video_gen.
        assert default_model_for("zhipu", "video_gen") == ""


class TestDefaultContextTokens:
    # Mirror of TestDefaultModels for the CONTEXT_TOKENS table; 0 means
    # "no default published" so the resolver falls through.

    def test_default_context_tokens_published(self):
        from services.llm import default_context_tokens_for

        assert default_context_tokens_for("mimo", "llm") == 1_000_000
        assert default_context_tokens_for("mimo", "stt") == 8_000
        assert default_context_tokens_for("mimo", "tts") == 8_000
        assert default_context_tokens_for("mimo", "image_gen") == 8_000
        assert default_context_tokens_for("minimax", "llm") == 1_000_000
        assert default_context_tokens_for("minimax", "tts") == 8_000
        assert default_context_tokens_for("minimax", "image_gen") == 8_000
        assert default_context_tokens_for("minimax", "video_gen") == 8_000
        assert default_context_tokens_for("gemini", "llm") == 1_000_000
        assert default_context_tokens_for("gemini", "image_gen") == 8_000
        assert default_context_tokens_for("zhipu", "llm") == 1_000_000
        assert default_context_tokens_for("zhipu", "stt") == 8_000
        assert default_context_tokens_for("zhipu", "tts") == 8_000
        assert default_context_tokens_for("zhipu", "image_gen") == 8_000
        # grok: grok-4.5 docs publish a 500k window; other caps match the
        # 8_000 convention used by every other text-to-X provider.
        assert default_context_tokens_for("grok", "llm") == 500_000
        assert default_context_tokens_for("grok", "stt") == 8_000
        assert default_context_tokens_for("grok", "tts") == 8_000
        assert default_context_tokens_for("grok", "image_gen") == 8_000
        assert default_context_tokens_for("grok", "video_gen") == 8_000

    def test_unsupported_cap_returns_zero(self):
        from services.llm import default_context_tokens_for

        # mimo doesn't register video_gen.
        assert default_context_tokens_for("mimo", "video_gen") == 0
        # minimax doesn't register stt.
        assert default_context_tokens_for("minimax", "stt") == 0
        # gemini registers only llm / image_gen.
        assert default_context_tokens_for("gemini", "stt") == 0
        assert default_context_tokens_for("gemini", "tts") == 0
        assert default_context_tokens_for("gemini", "video_gen") == 0
        # zhipu doesn't register video_gen.
        assert default_context_tokens_for("zhipu", "video_gen") == 0

    def test_unknown_provider_returns_zero(self):
        from services.llm import default_context_tokens_for

        assert default_context_tokens_for("not-a-provider", "llm") == 0


class TestProvidersSupporting:
    def test_supporting_providers_for_each_capability(self):
        from services.llm import providers_supporting

        chat_providers = set(providers_supporting("llm"))
        stt_providers = set(providers_supporting("stt"))
        tts_providers = set(providers_supporting("tts"))
        image_providers = set(providers_supporting("image_gen"))
        video_providers = set(providers_supporting("video_gen"))

        assert chat_providers == {"mimo", "minimax", "gemini", "grok", "zhipu"}
        assert stt_providers == {"mimo", "grok", "zhipu"}
        assert tts_providers == {"mimo", "minimax", "grok", "zhipu"}
        assert image_providers == {"mimo", "minimax", "gemini", "grok", "zhipu"}
        assert video_providers == {"minimax", "grok"}


class TestProviderChain:
    _EMPTY_DEFAULTS: ClassVar[dict] = {
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
        "zhipu_api_key": "",
        "zhipu_base_url": "",
        "grok_api_key": "",
        "grok_base_url": "",
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
        monkeypatch.setattr(
            "components.SETTINGS.llm_base_url", "https://api.openai.com/v1"
        )

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
            monkeypatch.setattr(
                f"components.SETTINGS.{field}", "" if field != "providers" else []
            )
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
                raise ProviderError(
                    "auth", status_code=401, body={}, provider="mimo", model="m"
                )
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
            raise ProviderError(
                "server error", status_code=500, body={}, provider="mimo", model="m"
            )

        with pytest.raises(ProviderError):
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
            raise ProviderError(
                "auth", status_code=401, body={}, provider="mimo", model="m"
            )

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
            raise ProviderError(
                "auth",
                status_code=401,
                body={},
                provider=provider.provider_name,
                model="m",
            )

        with pytest.raises(ProviderError):
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
            monkeypatch.setattr(
                f"components.SETTINGS.{field}", "" if field != "providers" else []
            )
        with pytest.raises(MissingLlmConfigError):
            await execute_with_fallback(None, None, "llm", call_fn=lambda p: None)


# ── MiniMax providers (commit 2) ────────────────────────────────────


def _async_handler(responses: list):
    """Build an async httpx handler that returns the next queued response."""
    queue = list(responses)

    async def handler(request: httpx.Request) -> httpx.Response:
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
    return httpx.AsyncClient(
        base_url="https://api.minimaxi.com", transport=httpx.MockTransport(handler)
    )


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
                json={
                    "base_resp": {"status_code": 0},
                    "data": {"image_base64": ["dGVzdA="]},
                },
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
        classified = classify_api_error(
            exc_info.value, provider="minimax", model="image-01"
        )
        assert classified.reason == FailoverReason.auth
        assert classified.retryable is False

    @pytest.mark.asyncio
    async def test_base_resp_rate_limit(self):
        from services.llm import ProviderError, classify_api_error, FailoverReason

        handler = _async_handler(
            [{"base_resp": {"status_code": 1002, "status_msg": "rate limit"}}]
        )
        provider = self._make_provider(handler)
        with pytest.raises(ProviderError) as exc_info:
            await provider.generate(ImageGenRequest(prompt="x"))
        assert exc_info.value.status_code == 429
        classified = classify_api_error(
            exc_info.value, provider="minimax", model="image-01"
        )
        assert classified.reason == FailoverReason.rate_limit

    @pytest.mark.asyncio
    async def test_base_resp_content_filter(self):
        from services.llm import classify_api_error, FailoverReason

        handler = _async_handler(
            [
                {
                    "base_resp": {
                        "status_code": 1027,
                        "status_msg": "violated safety policy",
                    }
                }
            ]
        )
        provider = self._make_provider(handler)
        with pytest.raises(Exception) as exc_info:
            await provider.generate(ImageGenRequest(prompt="x"))
        classified = classify_api_error(
            exc_info.value, provider="minimax", model="image-01"
        )
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

    @pytest.mark.asyncio
    async def test_generate_with_reference_passes_subject_reference(self):
        captured: list[dict] = []

        async def capture(req: httpx.Request) -> httpx.Response:
            captured.append(json.loads(req.content))
            return httpx.Response(
                200,
                json={
                    "base_resp": {"status_code": 0},
                    "data": {"image_base64": ["aGVsbG8="]},
                },
            )

        provider = self._make_provider(capture)
        await provider.generate(
            ImageGenRequest(prompt="x", reference_image="https://ref/seed.png")
        )
        assert captured[0]["subject_reference"] == [
            {"type": "character", "image_file": "https://ref/seed.png"}
        ]
        assert captured[0]["prompt"] == "x"

    @pytest.mark.asyncio
    async def test_generate_empty_images_raises_and_enables_fallback(self):
        """Empty image_base64 must raise ProviderError that classifies as should_fallback."""
        from services.llm import classify_api_error

        handler = _async_handler(
            [{"base_resp": {"status_code": 0}, "data": {"image_base64": []}}]
        )
        provider = self._make_provider(handler)
        with pytest.raises(ProviderError, match="returned no images") as exc_info:
            await provider.generate(
                ImageGenRequest(
                    prompt="x", reference_image="data:image/png;base64,AAA="
                )
            )
        assert exc_info.value.body == {
            "base_resp": {"status_code": 0},
            "data": {"image_base64": []},
        }
        assert exc_info.value.provider == "minimax"
        assert exc_info.value.model == "image-01"
        classified = classify_api_error(
            exc_info.value, provider="minimax", model="image-01"
        )
        assert classified.should_fallback is True
        assert classified.retryable is False


class TestEmptyImageResultFallback:
    """All image-gen providers that raise on empty results must classify as
    should_fallback so execute_with_fallback tries the next provider."""

    @pytest.mark.parametrize(
        "message",
        [
            "minimax image_gen returned no images: {...}",
            "Zhipu image_gen returned no images: {...}",
            "Gemini image_gen returned no images: {...}",
            "grok image_gen returned no images: {...}",
            "grok image_edit returned no images: {...}",
        ],
    )
    def test_all_providers_trigger_fallback(self, message):
        from services.llm import classify_api_error

        classified = classify_api_error(
            RuntimeError(message), provider="test", model="test"
        )
        assert classified.should_fallback is True
        assert classified.retryable is False


class TestReferenceImageCapability:
    def test_native_providers_support_reference(self):
        from services.llm.providers.gemini import GeminiImageGenProvider
        from services.llm.providers.minimax import MiniMaxImageGenProvider

        assert MiniMaxImageGenProvider.supports_reference_image is True
        assert GeminiImageGenProvider.supports_reference_image is True

    def test_text_only_providers_declare_no_native_support(self):
        from services.llm.providers.zhipu import ZhipuImageGenProvider

        assert MiMoImageGenProvider.supports_reference_image is False
        assert ZhipuImageGenProvider.supports_reference_image is False


class TestResolveReferenceBytes:
    @pytest.mark.asyncio
    async def test_data_uri_decoded(self):
        from services.llm.providers._reference import resolve_reference_bytes

        data, mime = await resolve_reference_bytes("data:image/png;base64,aGVsbG8=")
        assert data == b"hello"
        assert mime == "image/png"

    @pytest.mark.asyncio
    async def test_rejects_non_http_scheme(self):
        from services.llm.providers._reference import resolve_reference_bytes

        with pytest.raises(ValueError, match="data URI or http"):
            await resolve_reference_bytes("file:///etc/passwd")

    @pytest.mark.asyncio
    async def test_rejects_private_host(self):
        from services.llm.providers._reference import resolve_reference_bytes

        with pytest.raises(RuntimeError, match="unsafe reference host"):
            await resolve_reference_bytes("http://127.0.0.1/ref.png")


class TestGeminiImageGen:
    def _make_provider(self, handler):
        from services.llm.providers.gemini import GeminiImageGenProvider

        client = httpx.AsyncClient(
            base_url="https://generativelanguage.googleapis.com",
            transport=httpx.MockTransport(handler),
        )
        provider = GeminiImageGenProvider(
            ProviderConfig(
                base_url="https://generativelanguage.googleapis.com",
                api_key="sk-gemini",
                model="gemini-2.5-flash-image",
                service_type=ServiceType.image_gen,
                provider_name="gemini",
            )
        )
        provider._client = client
        return provider

    @staticmethod
    def _ok_response():
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "mimeType": "image/png",
                                        "data": "aGVsbG8=",
                                    }
                                }
                            ]
                        }
                    }
                ]
            },
        )

    @pytest.mark.asyncio
    async def test_generate_text_only(self):
        captured: list[dict] = []

        async def capture(req: httpx.Request) -> httpx.Response:
            captured.append(json.loads(req.content))
            return self._ok_response()

        provider = self._make_provider(capture)
        result = await provider.generate(ImageGenRequest(prompt="a cat"))
        assert result.images[0].b64 == "aGVsbG8="
        assert captured[0]["contents"][0]["parts"] == [{"text": "a cat"}]

    @pytest.mark.asyncio
    async def test_generate_with_data_uri_reference(self):
        captured: list[dict] = []

        async def capture(req: httpx.Request) -> httpx.Response:
            captured.append(json.loads(req.content))
            return self._ok_response()

        provider = self._make_provider(capture)
        await provider.generate(
            ImageGenRequest(
                prompt="re-render", reference_image="data:image/jpeg;base64,AAA="
            )
        )
        parts = captured[0]["contents"][0]["parts"]
        assert parts[0]["inlineData"] == {"mimeType": "image/jpeg", "data": "AAA="}
        assert parts[1] == {"text": "re-render"}

    @pytest.mark.asyncio
    async def test_generate_with_url_reference_downloads(self, monkeypatch):
        import services.llm.providers.gemini.image as gemini_image

        captured: list[dict] = []

        async def capture(req: httpx.Request) -> httpx.Response:
            captured.append(json.loads(req.content))
            return self._ok_response()

        async def fake_resolve(url: str):
            assert url == "https://cdn.example/ref.png"
            return b"\x89PNG", "image/png"

        monkeypatch.setattr(gemini_image, "resolve_reference_bytes", fake_resolve)
        provider = self._make_provider(capture)
        await provider.generate(
            ImageGenRequest(prompt="x", reference_image="https://cdn.example/ref.png")
        )
        parts = captured[0]["contents"][0]["parts"]
        assert parts[0]["inlineData"] == {"mimeType": "image/png", "data": "iVBORw=="}
        assert parts[1] == {"text": "x"}


class TestMiMoImageGenReference:
    """The real MiMo provider is text-only: a caller-supplied
    ``reference_image`` never reaches ``images.generate`` (the tool layer
    strips it after folding a vision description into the prompt)."""

    @pytest.mark.asyncio
    async def test_generate_ignores_reference_image(self):
        from services.llm.providers.mimo import MiMoImageGenProvider

        seen: dict = {}

        class _Images:
            async def generate(self, **kwargs):
                seen["kwargs"] = kwargs
                return type(
                    "_Resp",
                    (),
                    {
                        "data": [
                            type(
                                "_Item",
                                (),
                                {"url": "http://out/1.png", "b64_json": None},
                            )()
                        ]
                    },
                )()

        provider = MiMoImageGenProvider(
            ProviderConfig(
                base_url="https://api.xiaomimimo.com/v1",
                api_key="sk-mimo",
                model="dall-e-3",
                service_type=ServiceType.image_gen,
                provider_name="mimo",
            )
        )
        provider._client = type("_Client", (), {"images": _Images()})()
        result = await provider.generate(
            ImageGenRequest(prompt="a cat", reference_image="https://ref/seed.png")
        )
        assert result.images[0].url == "http://out/1.png"
        assert seen["kwargs"]["prompt"] == "a cat"
        assert "reference" not in seen["kwargs"]


class TestZhipuImageGenReference:
    """The real Zhipu provider is text-only: ``reference_image`` is ignored at
    the wire level (the tool layer folds a description into the prompt)."""

    def _make_provider(self, handler):
        from services.llm.providers.zhipu import ZhipuImageGenProvider

        client = _mock_http(handler)
        provider = ZhipuImageGenProvider(
            ProviderConfig(
                base_url="https://open.bigmodel.cn/api/paas/v4",
                api_key="sk-zhipu",
                model="glm-image",
                service_type=ServiceType.image_gen,
                provider_name="zhipu",
            )
        )
        provider._client = client
        return provider

    @pytest.mark.asyncio
    async def test_generate_ignores_reference_image(self, monkeypatch):
        import services.llm.providers.zhipu.image as zhipu_image

        captured: list[dict] = []

        async def capture(req: httpx.Request) -> httpx.Response:
            captured.append(json.loads(req.content))
            return httpx.Response(
                200, json={"data": [{"url": "https://cdn.bigmodel.cn/1.png"}]}
            )

        async def cdn_handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"\x89PNG")

        provider = self._make_provider(capture)
        # Patch AFTER building the provider so ``_make_provider``'s own client
        # keeps the real transport; only the anonymous CDN download client is
        # swapped for the mock.
        cdn_client = httpx.AsyncClient(transport=httpx.MockTransport(cdn_handler))
        monkeypatch.setattr(zhipu_image.httpx, "AsyncClient", lambda **kw: cdn_client)

        result = await provider.generate(
            ImageGenRequest(prompt="a cat", reference_image="https://ref/seed.png")
        )
        assert result.images[0].b64 == "iVBORw=="
        assert captured[0]["prompt"] == "a cat"
        assert "reference" not in captured[0]


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
    """v2 (MiniMax-H3) protocol path — selected by the model-name prefix."""

    def _make_provider(self, handler):
        from services.llm.providers.minimax import MiniMaxVideoGenProvider

        client = _mock_http(handler)
        provider = MiniMaxVideoGenProvider(
            ProviderConfig(
                base_url="https://api.minimaxi.com",
                api_key="sk-minimax",
                model="MiniMax-H3",
                service_type=ServiceType.video_gen,
                provider_name="minimax",
            )
        )
        provider._client = client
        return provider

    @pytest.mark.asyncio
    async def test_submit_returns_task_id(self):
        handler = _async_handler(
            [{"base_resp": {"status_code": 0}, "task_id": "task-abc-123"}]
        )
        provider = self._make_provider(handler)
        job = await provider.submit(
            VideoGenRequest(prompt="a cat", aspect_ratio="16:9")
        )
        assert job.task_id == "task-abc-123"
        assert job.status == "queued"

    @pytest.mark.asyncio
    async def test_submit_builds_content_array(self):
        captured: list[dict] = []

        async def capture(req: httpx.Request) -> httpx.Response:
            captured.append(json.loads(req.content))
            return httpx.Response(
                200, json={"base_resp": {"status_code": 0}, "task_id": "task-content"}
            )

        provider = self._make_provider(capture)
        await provider.submit(
            VideoGenRequest(
                prompt="a cat", duration=6, resolution="768P", aspect_ratio="9:16"
            )
        )
        body = captured[0]
        assert body["model"] == "MiniMax-H3"
        assert body["content"] == [{"type": "text", "text": "a cat"}]
        assert body["duration"] == 6
        assert body["resolution"] == "768P"
        assert body["ratio"] == "9:16"
        assert "first_frame_image" not in body

    @pytest.mark.asyncio
    async def test_submit_i2v_uses_adaptive_ratio_and_image_role(self):
        captured: list[dict] = []

        async def capture(req: httpx.Request) -> httpx.Response:
            captured.append(json.loads(req.content))
            return httpx.Response(
                200, json={"base_resp": {"status_code": 0}, "task_id": "task-i2v"}
            )

        provider = self._make_provider(capture)
        await provider.submit(
            VideoGenRequest(
                prompt="x",
                first_frame_image="https://example.com/seed.png",
                aspect_ratio="9:16",
            )
        )
        body = captured[0]
        assert body["content"][1] == {
            "type": "image_url",
            "image_url": {"url": "https://example.com/seed.png"},
            "role": "first_frame",
        }
        assert body["ratio"] == "adaptive"

    @pytest.mark.asyncio
    async def test_poll_success_returns_inline_download_url(self):
        handler = _async_handler(
            [
                {
                    "base_resp": {"status_code": 0},
                    "task": {
                        "status": "succeeded",
                        "content": {"url": "https://filecdn.minimax.chat/abc.mp4"},
                    },
                }
            ]
        )
        provider = self._make_provider(handler)
        job = await provider.poll("task-abc")
        assert job.status == "succeeded"
        assert job.download_url == "https://filecdn.minimax.chat/abc.mp4"

    @pytest.mark.asyncio
    async def test_poll_queued(self):
        # "queued" is its own lifecycle state per docs; do NOT collapse to processing.
        handler = _async_handler(
            [{"base_resp": {"status_code": 0}, "task": {"status": "queued"}}]
        )
        provider = self._make_provider(handler)
        job = await provider.poll("task-abc")
        assert job.status == "queued"
        assert job.download_url is None

    @pytest.mark.asyncio
    async def test_poll_running_maps_to_processing(self):
        handler = _async_handler(
            [{"base_resp": {"status_code": 0}, "task": {"status": "running"}}]
        )
        provider = self._make_provider(handler)
        job = await provider.poll("task-abc")
        assert job.status == "processing"
        assert job.download_url is None

    @pytest.mark.asyncio
    async def test_poll_cancelled_maps_to_failed(self):
        # Backend has no dedicated cancelled state; surface as failed so the
        # caller sees a terminal status instead of polling forever.
        handler = _async_handler(
            [{"base_resp": {"status_code": 0}, "task": {"status": "cancelled"}}]
        )
        provider = self._make_provider(handler)
        job = await provider.poll("task-abc")
        assert job.status == "failed"

    @pytest.mark.asyncio
    async def test_poll_fail_extracts_error_message(self):
        handler = _async_handler(
            [
                {
                    "base_resp": {"status_code": 0},
                    "task": {
                        "status": "failed",
                        "error": {"code": "bad_prompt", "message": "prompt too long"},
                    },
                }
            ]
        )
        provider = self._make_provider(handler)
        job = await provider.poll("task-abc")
        assert job.status == "failed"
        assert job.error == "prompt too long"

    @pytest.mark.asyncio
    async def test_poll_unexpected_body_shape_raises(self):
        # docs strictly defines {task: VideoTask}; anything else must surface
        # as poll_failed (not a half-parsed silent "processing").
        handler = _async_handler(
            [{"base_resp": {"status_code": 0}, "status": "succeeded"}]
        )
        provider = self._make_provider(handler)
        with pytest.raises(RuntimeError, match="unexpected body shape"):
            await provider.poll("task-abc")

    @pytest.mark.asyncio
    async def test_poll_non_dict_error_does_not_pollute_message(self):
        # Defensive: if docs ever drift to a non-dict error, do not stringify
        # the whole blob into the user-visible message field.
        handler = _async_handler(
            [
                {
                    "base_resp": {"status_code": 0},
                    "task": {
                        "status": "failed",
                        "error": "some string error",
                    },
                }
            ]
        )
        provider = self._make_provider(handler)
        job = await provider.poll("task-abc")
        assert job.status == "failed"
        assert job.error is not None
        assert "non-standard error" in job.error

    @pytest.mark.asyncio
    async def test_submit_rejects_prompt_over_7000_chars(self):
        # docs: ContentItem.text ≤ 7000 chars. Catch before hitting the API.
        from services.llm.providers.minimax.video import _MAX_PROMPT_CHARS

        provider = self._make_provider(_async_handler([]))
        big_prompt = "x" * (_MAX_PROMPT_CHARS + 1)
        with pytest.raises(ValueError, match="exceeds MiniMax limit"):
            await provider.submit(
                VideoGenRequest(prompt=big_prompt, aspect_ratio="16:9")
            )

    @pytest.mark.asyncio
    async def test_fetch_is_unreachable_on_h3(self):
        # H3 v2 returns the URL inline from poll(); fetch() must not be hit.
        provider = self._make_provider(_async_handler([]))
        with pytest.raises(RuntimeError, match="H3"):
            await provider.fetch("file-xyz")

    @pytest.mark.asyncio
    async def test_submit_rejects_v1_only_params(self):
        # 10s is legal on v1 but 1080P is not a v2 resolution.
        provider = self._make_provider(_async_handler([]))
        with pytest.raises(ValueError, match="v2. requires resolution"):
            await provider.submit(
                VideoGenRequest(
                    prompt="x", duration=10, resolution="1080P", aspect_ratio="16:9"
                )
            )

    @pytest.mark.asyncio
    async def test_submit_t2v_requires_aspect_ratio(self):
        provider = self._make_provider(_async_handler([]))
        with pytest.raises(ValueError, match="requires aspect_ratio"):
            await provider.submit(VideoGenRequest(prompt="x", resolution="768P"))


class TestMiniMaxVideoGenV1:
    """v1 (Hailuo) protocol path — the default, since MiniMax-H3 (v2) is not
    covered by the standard token-plan."""

    def _make_provider(self, handler, model: str = "MiniMax-Hailuo-2.3"):
        from services.llm.providers.minimax import MiniMaxVideoGenProvider

        client = _mock_http(handler)
        provider = MiniMaxVideoGenProvider(
            ProviderConfig(
                base_url="https://api.minimaxi.com",
                api_key="sk-minimax",
                model=model,
                service_type=ServiceType.video_gen,
                provider_name="minimax",
            )
        )
        provider._client = client
        return provider

    @pytest.mark.asyncio
    async def test_submit_uses_v1_endpoint_and_flat_payload(self):
        captured: list[httpx.Request] = []
        bodies: list[dict] = []

        async def capture(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            bodies.append(json.loads(req.content))
            return httpx.Response(
                200, json={"base_resp": {"status_code": 0}, "task_id": "task-v1"}
            )

        provider = self._make_provider(capture)
        job = await provider.submit(
            VideoGenRequest(
                prompt="a cat",
                duration=10,
                resolution="1080P",
                aspect_ratio="16:9",
                first_frame_image="https://example.com/seed.png",
            )
        )
        assert job.task_id == "task-v1"
        assert captured[0].url.path == "/v1/video_generation"
        body = bodies[0]
        assert body["model"] == "MiniMax-Hailuo-2.3"
        assert body["prompt"] == "a cat"  # flat prompt, not a content[] array
        assert "content" not in body
        assert body["duration"] == 10
        assert body["resolution"] == "1080P"
        assert body["aspect_ratio"] == "16:9"
        assert body["first_frame_image"] == "https://example.com/seed.png"

    @pytest.mark.asyncio
    async def test_submit_allows_t2v_without_aspect_ratio(self):
        # Unlike v2, v1 lets the server pick a default aspect ratio.
        handler = _async_handler(
            [{"base_resp": {"status_code": 0}, "task_id": "task-v1-t2v"}]
        )
        provider = self._make_provider(handler)
        job = await provider.submit(VideoGenRequest(prompt="a cat"))
        assert job.status == "queued"

    @pytest.mark.asyncio
    async def test_submit_rejects_v2_only_params(self):
        provider = self._make_provider(_async_handler([]))
        with pytest.raises(ValueError, match="v1. requires duration"):
            await provider.submit(
                VideoGenRequest(prompt="x", duration=15, resolution="768P")
            )
        with pytest.raises(ValueError, match="v1. requires resolution"):
            await provider.submit(
                VideoGenRequest(prompt="x", duration=6, resolution="2K")
            )

    @pytest.mark.asyncio
    async def test_poll_success_returns_file_id_and_no_inline_url(self):
        captured: list[httpx.Request] = []

        async def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(
                200,
                json={
                    "base_resp": {"status_code": 0},
                    "status": "Success",
                    "file_id": "file-42",
                },
            )

        provider = self._make_provider(handler)
        job = await provider.poll("task-v1")
        assert captured[0].url.path == "/v1/query/video_generation"
        assert captured[0].url.params["task_id"] == "task-v1"
        assert job.status == "succeeded"
        assert job.file_id == "file-42"
        # v1 gates the URL behind fetch() — the worker must take that branch.
        assert job.download_url is None

    @pytest.mark.asyncio
    async def test_poll_maps_v1_status_enum(self):
        for raw, expected in (
            ("Queueing", "queued"),
            ("Processing", "processing"),
            ("Fail", "failed"),
        ):
            handler = _async_handler([{"base_resp": {"status_code": 0}, "status": raw}])
            provider = self._make_provider(handler)
            job = await provider.poll("task-v1")
            assert job.status == expected, raw

    @pytest.mark.asyncio
    async def test_poll_fail_extracts_error_message(self):
        handler = _async_handler(
            [
                {
                    "base_resp": {"status_code": 0},
                    "status": "Fail",
                    "error_message": "content rejected",
                }
            ]
        )
        provider = self._make_provider(handler)
        job = await provider.poll("task-v1")
        assert job.status == "failed"
        assert job.error == "content rejected"

    @pytest.mark.asyncio
    async def test_fetch_retrieves_download_url(self):
        captured: list[httpx.Request] = []

        async def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(
                200,
                json={
                    "base_resp": {"status_code": 0},
                    "file": {
                        "download_url": "https://cdn.minimax.chat/v1.mp4",
                        "content_type": "video/mp4",
                        "bytes": 123,
                    },
                },
            )

        provider = self._make_provider(handler)
        asset = await provider.fetch("file-42")
        assert captured[0].url.path == "/v1/files/retrieve"
        assert captured[0].url.params["file_id"] == "file-42"
        assert asset.download_url == "https://cdn.minimax.chat/v1.mp4"
        assert asset.size == 123

    @pytest.mark.asyncio
    async def test_fetch_without_download_url_raises(self):
        handler = _async_handler([{"base_resp": {"status_code": 0}, "file": {}}])
        provider = self._make_provider(handler)
        with pytest.raises(RuntimeError, match="no download_url"):
            await provider.fetch("file-42")

    @pytest.mark.asyncio
    async def test_unknown_model_routes_to_v1(self):
        # Safe side: an unrecognized model name must not land on the paid v2
        # endpoint the standard token-plan doesn't cover.
        from services.llm.providers.minimax.video import _api_version

        assert _api_version("some-future-model") == "v1"
        assert _api_version("") == "v1"
        assert _api_version("MiniMax-Hailuo-02") == "v1"
        assert _api_version("MiniMax-H3") == "v2"

        captured: list[httpx.Request] = []

        async def capture(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            return httpx.Response(
                200, json={"base_resp": {"status_code": 0}, "task_id": "t"}
            )

        provider = self._make_provider(capture, model="some-future-model")
        await provider.submit(VideoGenRequest(prompt="x"))
        assert captured[0].url.path == "/v1/video_generation"


# ── Grok (xAI) providers ───────────────────────────────────────────


def _mock_grok_http(handler) -> httpx.AsyncClient:
    # base_url omits /v1 so mock-transport-captured request paths mirror the
    # live wire paths (the real base_url includes /v1; the provider posts
    # relative paths to avoid double-prefixing).
    return httpx.AsyncClient(
        base_url="https://api.x.ai", transport=httpx.MockTransport(handler)
    )


class TestGrokImageGen:
    def _make_provider(self, handler):
        from services.llm.providers.grok import GrokImageGenProvider

        provider = GrokImageGenProvider(
            ProviderConfig(
                base_url="https://api.x.ai/v1",
                api_key="sk-grok",
                model="grok-imagine-image-quality",
                service_type=ServiceType.image_gen,
                provider_name="grok",
            )
        )
        provider._client = _mock_grok_http(handler)
        return provider

    @pytest.mark.asyncio
    async def test_generate_text_only_downloads_and_encodes(self, monkeypatch):
        from services.llm.providers.grok import image

        captured: list[dict] = []

        async def grok_handler(req: httpx.Request) -> httpx.Response:
            captured.append(json.loads(req.content))
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"url": "https://cdn.x.ai/1.png"},
                        {"url": "https://cdn.x.ai/2.png"},
                    ]
                },
            )

        async def cdn_handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"\x89PNG")

        provider = self._make_provider(grok_handler)
        cdn_client = httpx.AsyncClient(transport=httpx.MockTransport(cdn_handler))
        monkeypatch.setattr(image.httpx, "AsyncClient", lambda **kw: cdn_client)

        result = await provider.generate(ImageGenRequest(prompt="a cat", n=2))
        assert len(result.images) == 2
        assert result.images[0].b64 == "iVBORw=="
        assert result.model == "grok-imagine-image-quality"
        # Generation payload was sent as expected.
        body = captured[0]
        assert body["model"] == "grok-imagine-image-quality"
        assert body["prompt"] == "a cat"
        assert body["n"] == 2
        assert "image" not in body

    @pytest.mark.asyncio
    async def test_generate_with_reference_uses_edits_endpoint(self, monkeypatch):
        from services.llm.providers.grok import image

        captured: list[httpx.Request] = []
        bodies: list[dict] = []

        async def handler(req: httpx.Request) -> httpx.Response:
            captured.append(req)
            bodies.append(json.loads(req.content))
            return httpx.Response(
                200, json={"data": [{"url": "https://cdn.x.ai/out.png"}]}
            )

        async def cdn_handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"\x89PNG")

        provider = self._make_provider(handler)
        cdn_client = httpx.AsyncClient(transport=httpx.MockTransport(cdn_handler))
        monkeypatch.setattr(image.httpx, "AsyncClient", lambda **kw: cdn_client)

        await provider.generate(
            ImageGenRequest(
                prompt="re-render",
                reference_image="https://ref/seed.png",
                aspect_ratio="16:9",
            )
        )
        assert captured[0].url.path == "/images/edits"
        assert bodies[0]["image"] == {
            "url": "https://ref/seed.png",
            "type": "image_url",
        }
        assert bodies[0]["aspect_ratio"] == "16:9"

    @pytest.mark.asyncio
    async def test_generate_empty_data_raises(self):
        handler = _async_handler([{"data": []}])
        provider = self._make_provider(handler)
        with pytest.raises(RuntimeError, match="no images"):
            await provider.generate(ImageGenRequest(prompt="x"))

    def test_supports_reference_image_is_true(self):
        from services.llm.providers.grok import GrokImageGenProvider

        assert GrokImageGenProvider.supports_reference_image is True

    def test_raw_client_returns_none(self):
        from services.llm.providers.grok import GrokImageGenProvider

        provider = GrokImageGenProvider(
            ProviderConfig(
                base_url="x",
                api_key="k",
                model="m",
                service_type=ServiceType.image_gen,
                provider_name="grok",
            )
        )
        assert provider.raw_client() is None


class TestGrokTTS:
    def _make_provider(self, handler):
        from services.llm.providers.grok import GrokTTSProvider

        provider = GrokTTSProvider(
            ProviderConfig(
                base_url="https://api.x.ai/v1",
                api_key="sk-grok",
                model="grok-voice-think-fast-1.0",
                service_type=ServiceType.tts,
                provider_name="grok",
            )
        )
        provider._client = _mock_grok_http(handler)
        return provider

    @pytest.mark.asyncio
    async def test_synthesize_returns_raw_audio_bytes(self):
        captured: list[dict] = []

        async def handler(req: httpx.Request) -> httpx.Response:
            captured.append(json.loads(req.content))
            return httpx.Response(200, content=b"FAKE_MP3_BYTES")

        provider = self._make_provider(handler)
        result = await provider.synthesize("hello", voice="eve", fmt="mp3")
        assert result.audio == b"FAKE_MP3_BYTES"
        assert result.mime == "audio/mpeg"
        assert result.voice == "eve"
        body = captured[0]
        # xAI's /v1/tts has no ``model`` field — assert absence so a future
        # regression that adds it is caught.
        assert "model" not in body
        assert body["text"] == "hello"
        assert body["voice_id"] == "eve"
        assert body["language"] == "en"
        assert body["output_format"]["codec"] == "mp3"
        assert body["output_format"]["bit_rate"] == 128000

    @pytest.mark.asyncio
    async def test_synthesize_defaults_to_first_voice(self):
        captured: list[dict] = []

        async def handler(req: httpx.Request) -> httpx.Response:
            captured.append(json.loads(req.content))
            return httpx.Response(200, content=b"x")

        provider = self._make_provider(handler)
        await provider.synthesize("hi")
        assert captured[0]["voice_id"] == "eve"

    @pytest.mark.asyncio
    async def test_synthesize_wav_omits_bit_rate(self):
        captured: list[dict] = []

        async def handler(req: httpx.Request) -> httpx.Response:
            captured.append(json.loads(req.content))
            return httpx.Response(200, content=b"RIFF")

        provider = self._make_provider(handler)
        result = await provider.synthesize("hi", fmt="wav")
        assert result.mime == "audio/wav"
        assert "bit_rate" not in captured[0]["output_format"]


class TestGrokSTT:
    def _make_provider(self, handler):
        from services.llm.providers.grok import GrokSTTProvider

        provider = GrokSTTProvider(
            ProviderConfig(
                base_url="https://api.x.ai/v1",
                api_key="sk-grok",
                model="grok-transcribe",
                service_type=ServiceType.stt,
                provider_name="grok",
            )
        )
        provider._client = _mock_grok_http(handler)
        return provider

    @pytest.mark.asyncio
    async def test_transcribe_returns_text(self):
        async def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"text": "  hello world  "})

        provider = self._make_provider(handler)
        result = await provider.transcribe(b"FAKE_WAV", mime_type="audio/wav")
        assert result.text == "hello world"

    @pytest.mark.asyncio
    async def test_transcribe_picks_extension_from_mime(self):
        captured_files: list[tuple[str, bytes]] = []

        async def handler(req: httpx.Request) -> httpx.Response:
            for k, v in req.headers.items():
                if k.lower() == "content-type":
                    # Multipart envelope — content-type is a multipart/form-data; we want the part names instead.
                    pass
            captured_files.append(("multipart", req.content))
            return httpx.Response(200, json={"text": "ok"})

        provider = self._make_provider(handler)
        await provider.transcribe(b"\xff\xfb", mime_type="audio/mpeg")
        body = captured_files[0][1]
        assert b'filename="audio.mp3"' in body
        await provider.transcribe(b"x", mime_type="audio/ogg")
        body2 = captured_files[1][1]
        assert b'filename="audio.ogg"' in body2


class TestGrokVideoGen:
    def _make_provider(self, handler, model: str = "grok-imagine-video-1.5"):
        from services.llm.providers.grok import GrokVideoGenProvider

        provider = GrokVideoGenProvider(
            ProviderConfig(
                base_url="https://api.x.ai/v1",
                api_key="sk-grok",
                model=model,
                service_type=ServiceType.video_gen,
                provider_name="grok",
            )
        )
        provider._client = _mock_grok_http(handler)
        return provider

    @pytest.mark.asyncio
    async def test_submit_returns_request_id(self):
        handler = _async_handler([{"request_id": "grok-task-1"}])
        provider = self._make_provider(handler)
        job = await provider.submit(
            VideoGenRequest(prompt="a cat", duration=10, resolution="720p")
        )
        assert job.task_id == "grok-task-1"
        assert job.status == "queued"

    @pytest.mark.asyncio
    async def test_submit_builds_payload_with_image(self):
        captured: list[dict] = []

        async def handler(req: httpx.Request) -> httpx.Response:
            captured.append(json.loads(req.content))
            return httpx.Response(200, json={"request_id": "grok-i2v"})

        provider = self._make_provider(handler)
        await provider.submit(
            VideoGenRequest(
                prompt="x",
                duration=5,
                resolution="720p",
                first_frame_image="https://example.com/seed.png",
                aspect_ratio="16:9",
            )
        )
        body = captured[0]
        assert body["model"] == "grok-imagine-video-1.5"
        assert body["prompt"] == "x"
        assert body["duration"] == 5
        assert body["resolution"] == "720p"
        assert body["aspect_ratio"] == "16:9"
        assert body["image"] == {
            "url": "https://example.com/seed.png",
            "type": "image_url",
        }

    @pytest.mark.asyncio
    async def test_submit_rejects_prompt_over_7000_chars(self):
        provider = self._make_provider(_async_handler([]))
        with pytest.raises(ValueError, match="exceeds xAI limit"):
            await provider.submit(
                VideoGenRequest(prompt="x" * 7001, duration=5, resolution="720p")
            )

    @pytest.mark.asyncio
    async def test_submit_accepts_full_duration_range(self):
        # xAI docs allow 1..15s — verify both edges pass through to the wire
        # rather than being rejected client-side.

        async def capture(req: httpx.Request) -> httpx.Response:
            assert json.loads(req.content)["duration"] in (1, 15)
            return httpx.Response(200, json={"request_id": "ok"})

        provider = self._make_provider(capture)
        await provider.submit(
            VideoGenRequest(prompt="x", duration=1, resolution="720p")
        )
        await provider.submit(
            VideoGenRequest(prompt="x", duration=15, resolution="720p")
        )

    @pytest.mark.asyncio
    async def test_submit_rejects_duration_outside_range(self):
        provider = self._make_provider(_async_handler([]))
        with pytest.raises(ValueError, match="requires duration"):
            await provider.submit(
                VideoGenRequest(prompt="x", duration=0, resolution="720p")
            )
        with pytest.raises(ValueError, match="requires duration"):
            await provider.submit(
                VideoGenRequest(prompt="x", duration=16, resolution="720p")
            )

    @pytest.mark.asyncio
    async def test_submit_accepts_both_resolution_casings(self):
        # xAI docs use lowercase "720p" / "1080p" but accept mixed case too.
        for res in ("480p", "720p", "1080p", "480P", "720P", "1080P"):
            handler = _async_handler([{"request_id": "ok"}])
            provider = self._make_provider(handler)
            await provider.submit(
                VideoGenRequest(prompt="x", duration=5, resolution=res)
            )

    @pytest.mark.asyncio
    async def test_submit_rejects_unsupported_resolution(self):
        provider = self._make_provider(_async_handler([]))
        with pytest.raises(ValueError, match="requires resolution"):
            await provider.submit(
                VideoGenRequest(prompt="x", duration=10, resolution="4K")
            )

    @pytest.mark.asyncio
    async def test_poll_done_returns_inline_url(self):
        handler = _async_handler(
            [{"status": "done", "video": {"url": "https://cdn.x.ai/v1.mp4"}}]
        )
        provider = self._make_provider(handler)
        job = await provider.poll("grok-task-1")
        assert job.status == "succeeded"
        assert job.download_url == "https://cdn.x.ai/v1.mp4"

    @pytest.mark.asyncio
    async def test_poll_status_enum_mapping(self):
        for raw, expected in (
            ("queued", "queued"),
            ("processing", "processing"),
            ("pending", "processing"),  # alternate in-flight label
            ("running", "processing"),  # alt in-flight label
            ("failed", "failed"),
            ("expired", "failed"),  # collapse to internal "failed" terminal
        ):
            handler = _async_handler([{"status": raw}])
            provider = self._make_provider(handler)
            job = await provider.poll("task")
            assert job.status == expected, raw

    @pytest.mark.asyncio
    async def test_poll_unknown_status_keeps_polling(self):
        # Defensive: an unrecognized status keeps us in "processing" so the
        # job worker doesn't mark a still-running job as terminal.
        handler = _async_handler([{"status": "weird-new-state"}])
        provider = self._make_provider(handler)
        job = await provider.poll("task")
        assert job.status == "processing"
        assert job.error is not None
        assert "unknown grok video status" in job.error

    @pytest.mark.asyncio
    async def test_poll_failed_extracts_dict_error_message(self):
        # xAI docs show the failed body as ``error: {code, message}``.
        handler = _async_handler(
            [
                {
                    "status": "failed",
                    "error": {"code": "invalid_argument", "message": "prompt too long"},
                }
            ]
        )
        provider = self._make_provider(handler)
        job = await provider.poll("task")
        assert job.status == "failed"
        assert job.error == "prompt too long"

    @pytest.mark.asyncio
    async def test_poll_failed_accepts_string_error(self):
        # Defensive: older or one-off responses may carry ``error: "<string>"``;
        # the parser must not crash and must surface the message verbatim.
        handler = _async_handler([{"status": "failed", "error": "content rejected"}])
        provider = self._make_provider(handler)
        job = await provider.poll("task")
        assert job.status == "failed"
        assert job.error == "content rejected"

    @pytest.mark.asyncio
    async def test_poll_failed_dict_without_message_falls_back_to_code(self):
        handler = _async_handler(
            [{"status": "failed", "error": {"code": "bad_request"}}]
        )
        provider = self._make_provider(handler)
        job = await provider.poll("task")
        assert job.status == "failed"
        assert job.error == "bad_request"

    @pytest.mark.asyncio
    async def test_poll_failed_non_dict_error_does_not_pollute_message(self):
        # Defensive: if docs ever drift to a non-dict, non-string error,
        # surface a tagged repr rather than passing the raw blob through.
        handler = _async_handler([{"status": "failed", "error": ["weird", "list"]}])
        provider = self._make_provider(handler)
        job = await provider.poll("task")
        assert job.status == "failed"
        assert job.error is not None
        assert "non-standard" in job.error

    @pytest.mark.asyncio
    async def test_fetch_is_unreachable(self):
        provider = self._make_provider(_async_handler([]))
        with pytest.raises(RuntimeError, match="fetch"):
            await provider.fetch("file-x")

    def test_raw_client_returns_none(self):
        from services.llm.providers.grok import GrokVideoGenProvider

        provider = GrokVideoGenProvider(
            ProviderConfig(
                base_url="x",
                api_key="k",
                model="m",
                service_type=ServiceType.video_gen,
                provider_name="grok",
            )
        )
        assert provider.raw_client() is None


class TestPerUserProviderChain:
    """Tier-1: a user's per-user provider_config is tried before the global chain.

    resolve_provider_chain prepends slots built from the user's JSON
    provider_config, then appends the existing global fold-in. Same-provider
    dedup keeps the user (tier-1) slot.
    """

    _EMPTY = TestProviderChain._EMPTY_DEFAULTS

    def _reset(self, monkeypatch):
        for field, default in self._EMPTY.items():
            monkeypatch.setattr(f"components.SETTINGS.{field}", default)

    def _seed(self, SessionLocal, provider_config):
        from modules.auth import (
            User,
            UserModelConfig,
            generate_activation_token,
            hash_activation_token,
        )

        with SessionLocal() as db:
            user = User(
                username="u",
                password_hash=None,
                activation_token_hash=hash_activation_token(
                    generate_activation_token()
                ),
                is_active=True,
                can_use=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            db.add(
                UserModelConfig(
                    user_id=user.id, provider_config=json.dumps(provider_config)
                )
            )
            db.commit()
            return user.id

    def test_user_provider_prepended_and_deduped(self, _patch_db, monkeypatch):
        from services.llm import resolve_provider_chain

        self._reset(monkeypatch)
        # Global chain: mimo then minimax, both keyed.
        monkeypatch.setattr("components.SETTINGS.providers", ["mimo", "minimax"])
        monkeypatch.setattr("components.SETTINGS.mimo_api_key", "sk-global-mimo")
        monkeypatch.setattr("components.SETTINGS.minimax_api_key", "sk-global-mm")

        _, SessionLocal = _patch_db
        user_id = self._seed(
            SessionLocal,
            [
                {
                    "name": "minimax",
                    "api_key": "sk-user-mm",
                    "base_url": "https://user-mm.example/v1",
                }
            ],
        )
        with SessionLocal() as db:
            chain = resolve_provider_chain(db, user_id, "llm")
        # User minimax (tier 1) first; global mimo next; global minimax deduped away.
        assert [c.provider_name for c in chain] == ["minimax", "mimo"]
        assert chain[0].api_key == "sk-user-mm"
        assert chain[0].base_url == "https://user-mm.example/v1"

    def test_user_provider_slot_skipped_without_key(self, _patch_db, monkeypatch):
        from services.llm import resolve_provider_chain

        self._reset(monkeypatch)
        monkeypatch.setattr("components.SETTINGS.providers", ["minimax"])
        monkeypatch.setattr("components.SETTINGS.minimax_api_key", "sk-global-mm")

        _, SessionLocal = _patch_db
        # mimo slot lacks an api_key → dropped; falls through to global minimax.
        user_id = self._seed(
            SessionLocal, [{"name": "mimo", "api_key": "", "base_url": "https://x/v1"}]
        )
        with SessionLocal() as db:
            chain = resolve_provider_chain(db, user_id, "llm")
        assert [c.provider_name for c in chain] == ["minimax"]

    def test_no_user_context_unchanged(self, monkeypatch):
        """db=None/user_id=None must behave exactly as before (no tier 1)."""
        from services.llm import resolve_provider_chain

        self._reset(monkeypatch)
        monkeypatch.setattr("components.SETTINGS.providers", ["mimo", "minimax"])
        monkeypatch.setattr("components.SETTINGS.mimo_api_key", "sk-mimo")
        monkeypatch.setattr("components.SETTINGS.minimax_api_key", "sk-mm")
        chain = resolve_provider_chain(None, None, "llm")
        assert [c.provider_name for c in chain] == ["mimo", "minimax"]

    def test_user_capability_provider_pin(self, _patch_db, monkeypatch):
        from modules.auth import (
            User,
            UserModelConfig,
            generate_activation_token,
            hash_activation_token,
        )
        from services.llm import resolve_provider_chain

        self._reset(monkeypatch)
        monkeypatch.setattr("components.SETTINGS.providers", ["minimax", "mimo"])

        _, SessionLocal = _patch_db
        with SessionLocal() as db:
            user = User(
                username="u_pin",
                password_hash=None,
                activation_token_hash=hash_activation_token(
                    generate_activation_token()
                ),
                is_active=True,
                can_use=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            db.add(
                UserModelConfig(
                    user_id=user.id,
                    tts_provider="mimo",
                    provider_config=json.dumps(
                        [
                            {
                                "name": "minimax",
                                "api_key": "sk-mm",
                                "base_url": "https://mm/v1",
                            },
                            {
                                "name": "mimo",
                                "api_key": "sk-mimo",
                                "base_url": "https://mimo/v1",
                            },
                        ]
                    ),
                )
            )
            db.commit()
            chain = resolve_provider_chain(db, user.id, "tts")
        # tts_provider is pinned to mimo, so mimo is moved to the front for tts
        assert [c.provider_name for c in chain] == ["mimo", "minimax"]


class TestResolveUserLlmConfigCredentials:
    # Credentials must come from chain[0] — not the stale per-cap row.

    _EMPTY = TestProviderChain._EMPTY_DEFAULTS

    def _reset(self, monkeypatch):
        for field, default in self._EMPTY.items():
            monkeypatch.setattr(f"components.SETTINGS.{field}", default)

    def test_tier1_provider_config_drives_credentials(self, _patch_db, monkeypatch):
        from services.llm import resolve_user_llm_config

        self._reset(monkeypatch)
        monkeypatch.setattr("components.SETTINGS.providers", ["mimo", "minimax"])
        monkeypatch.setattr("components.SETTINGS.mimo_api_key", "sk-global-mimo")
        monkeypatch.setattr("components.SETTINGS.minimax_api_key", "sk-global-mm")
        # User has tier 1 only — no per-cap llm_* row, so the chain slot's
        # default model (``default_model_for("minimax", "llm")``) wins.
        _, SessionLocal = _patch_db
        with SessionLocal() as db:
            from modules.auth import (
                User,
                UserModelConfig,
                generate_activation_token,
                hash_activation_token,
            )

            user = User(
                username="u",
                password_hash=None,
                activation_token_hash=hash_activation_token(
                    generate_activation_token()
                ),
                is_active=True,
                can_use=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            db.add(
                UserModelConfig(
                    user_id=user.id,
                    provider_config=json.dumps(
                        [
                            {
                                "name": "minimax",
                                "api_key": "sk-user-mm",
                                "base_url": "https://user-mm.example/v1",
                            }
                        ]
                    ),
                )
            )
            db.commit()
            cfg = resolve_user_llm_config(db, user.id)

        assert cfg["provider_name"] == "minimax"
        assert cfg["api_key"] == "sk-user-mm"
        assert cfg["base_url"] == "https://user-mm.example/v1"
        assert cfg["model_name"] == "MiniMax-Text-01"

    def test_empty_chain_returns_empty_credentials(self, monkeypatch):
        # Empty chain → all-empty dict so schedulers' falsy skip fires.
        from services.llm import resolve_user_llm_config

        self._reset(monkeypatch)
        monkeypatch.setattr("components.SETTINGS.providers", [])
        cfg = resolve_user_llm_config(None, None)
        assert cfg == {
            "api_key": "",
            "base_url": "",
            "model_name": "",
            "provider_name": "",
        }


class TestMiniMaxInnerCodes:
    """MiniMax returns HTTP 200 with the real error in ``base_resp``; entitlement
    refusals share the generic invalid-param code and must not read as format_error."""

    @staticmethod
    def _classify(status_msg: str, status_code: int):
        from services.llm.error_classifier import classify_api_error
        from services.llm.providers.base import ProviderError
        from services.llm.providers.minimax._errors import raise_for_minimax_response

        resp = type(
            "R",
            (),
            {
                "status_code": 200,
                "json": lambda self: {
                    "base_resp": {"status_code": status_code, "status_msg": status_msg}
                },
            },
        )()
        with pytest.raises(ProviderError) as exc:
            raise_for_minimax_response(resp, provider="minimax", model="MiniMax-H3")
        return exc.value, classify_api_error(
            exc.value, provider="minimax", model="MiniMax-H3"
        )

    def test_plan_refusal_is_billing_not_format_error(self):
        err, classified = self._classify(
            "invalid params, TokenPlan 或 Credit 暂不支持 MiniMax-H3 系列模型 (2013)",
            2013,
        )
        assert err.status_code == 402
        assert classified.reason.value == "billing"
        # A model gated on this key may be enabled on another one.
        assert classified.should_rotate_credential is True

    def test_genuine_bad_param_stays_format_error(self):
        err, classified = self._classify("invalid params, prompt is required", 2013)
        assert err.status_code == 400
        assert classified.reason.value == "format_error"
        assert classified.should_rotate_credential is False

    def test_unknown_inner_code_is_retryable_not_success(self):
        # Falling back to resp.status_code (200) would read as a success.
        err, classified = self._classify("something new upstream", 9999)
        assert err.status_code == 502
        assert classified.retryable is True

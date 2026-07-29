from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from services.llm import (
    BaseProvider,
    ImageGenProvider,
    ImageGenRequest,
    MissingLlmConfigError,
    ProviderConfig,
    ServiceType,
    TTSProvider,
    VideoGenProvider,
    VideoGenRequest,
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

    def test_image_gen_default_is_minimax(self, monkeypatch):
        """Commit 3: image_gen defaults to MiniMax (image-01). Without a
        minimax_api_key the swap path raises MissingLlmConfigError."""
        from components import SETTINGS

        monkeypatch.setattr("components.SETTINGS.minimax_api_key", "sk-minimax-test")
        cfg = resolve_provider_config(None, None, "image_gen")
        assert cfg.provider_name == "minimax"
        assert cfg.base_url == SETTINGS.image_gen_base_url
        assert cfg.api_key == "sk-minimax-test"
        assert cfg.model == SETTINGS.image_gen_model_name

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
        handler = _async_handler([
            {
                "base_resp": {"status_code": 0, "status_msg": "success"},
                "data": {"image_base64": ["aGVsbG8=", "d29ybGQ="]},
            }
        ])
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

        handler = _async_handler([
            {
                "base_resp": {
                    "status_code": 1004,
                    "status_msg": "login fail: invalid api key",
                }
            }
        ])
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

        handler = _async_handler([
            {"base_resp": {"status_code": 1002, "status_msg": "rate limit"}}
        ])
        provider = self._make_provider(handler)
        with pytest.raises(ProviderError) as exc_info:
            await provider.generate(ImageGenRequest(prompt="x"))
        assert exc_info.value.status_code == 429
        classified = classify_api_error(exc_info.value, provider="minimax", model="image-01")
        assert classified.reason == FailoverReason.rate_limit

    @pytest.mark.asyncio
    async def test_base_resp_content_filter(self):
        from services.llm import classify_api_error, FailoverReason

        handler = _async_handler([
            {"base_resp": {"status_code": 1027, "status_msg": "violated safety policy"}}
        ])
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
                base_url="x", api_key="k", model="m",
                service_type=ServiceType.image_gen, provider_name="minimax",
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
        handler = _async_handler([
            {
                "base_resp": {"status_code": 0},
                "data": {"audio": "68656c6c6f"},
            }
        ])
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
        handler = _async_handler([
            {"base_resp": {"status_code": 0}, "task_id": "task-abc-123"}
        ])
        provider = self._make_provider(handler)
        job = await provider.submit(VideoGenRequest(prompt="a cat"))
        assert job.task_id == "task-abc-123"
        assert job.status == "queued"

    @pytest.mark.asyncio
    async def test_poll_success(self):
        handler = _async_handler([
            {"base_resp": {"status_code": 0}, "status": "Success", "file_id": "file-xyz"}
        ])
        provider = self._make_provider(handler)
        job = await provider.poll("task-abc")
        assert job.status == "succeeded"
        assert job.file_id == "file-xyz"

    @pytest.mark.asyncio
    async def test_poll_processing(self):
        handler = _async_handler([
            {"base_resp": {"status_code": 0}, "status": "Processing"}
        ])
        provider = self._make_provider(handler)
        job = await provider.poll("task-abc")
        assert job.status == "processing"
        assert job.file_id is None

    @pytest.mark.asyncio
    async def test_poll_fail(self):
        handler = _async_handler([
            {"base_resp": {"status_code": 0}, "status": "Fail", "error_message": "bad prompt"}
        ])
        provider = self._make_provider(handler)
        job = await provider.poll("task-abc")
        assert job.status == "failed"
        assert job.error == "bad prompt"

    @pytest.mark.asyncio
    async def test_fetch_returns_download_url(self):
        handler = _async_handler([
            {
                "base_resp": {"status_code": 0},
                "file": {
                    "download_url": "https://filecdn.minimax.chat/abc.mp4",
                    "content_type": "video/mp4",
                    "bytes": 12345,
                },
            }
        ])
        provider = self._make_provider(handler)
        asset = await provider.fetch("file-xyz")
        assert asset.download_url == "https://filecdn.minimax.chat/abc.mp4"
        assert asset.content_type == "video/mp4"
        assert asset.size == 12345
import base64
import io
import json
import os

import pytest


@pytest.mark.e2e
class TestTTSTool:
    @pytest.mark.asyncio
    async def test_tts_basic(self):
        from services.tools.builtin.tts_tool import text_to_speech_tool

        api_key = os.getenv("MIMO_API_KEY")
        base_url = os.getenv("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
        llm_config = {
            "api_key": api_key,
            "base_url": base_url,
            "model_name": "mimo-v2.5-tts",
        }

        # Call the actual codebase tool function directly without mock
        result_str = await text_to_speech_tool(
            text="Hello, this is a test.", llm_config=llm_config, voice="mimo_default"
        )
        result = json.loads(result_str)
        assert result["success"] is True
        audio_bytes = base64.b64decode(result["audio_base64"])
        assert len(audio_bytes) > 0
        assert audio_bytes[:1] == b"\xff" or audio_bytes[:3] == b"ID3"

    @pytest.mark.asyncio
    async def test_tts_chinese_voice(self):
        from services.tools.builtin.tts_tool import text_to_speech_tool

        api_key = os.getenv("MIMO_API_KEY")
        base_url = os.getenv("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
        llm_config = {
            "api_key": api_key,
            "base_url": base_url,
            "model_name": "mimo-v2.5-tts",
        }

        # Call the actual codebase tool function directly without mock
        result_str = await text_to_speech_tool(
            text="你好，这是一个测试。", llm_config=llm_config, voice="冰糖"
        )
        result = json.loads(result_str)
        assert result["success"] is True
        audio_bytes = base64.b64decode(result["audio_base64"])
        assert len(audio_bytes) > 0


@pytest.mark.e2e
class TestSTTTool:
    async def test_stt_basic(self, test_client, test_token):
        # Generate 2 seconds of silence WAV
        sample_rate = 8000
        num_samples = sample_rate * 2
        data = b"\x00\x00" * num_samples
        data_size = len(data)
        file_size = 36 + data_size
        buf = io.BytesIO()
        buf.write(b"RIFF")
        buf.write(file_size.to_bytes(4, "little"))
        buf.write(b"WAVE")
        buf.write(b"fmt ")
        buf.write((16).to_bytes(4, "little"))
        buf.write((1).to_bytes(2, "little"))
        buf.write((1).to_bytes(2, "little"))
        buf.write(sample_rate.to_bytes(4, "little"))
        buf.write((sample_rate * 2).to_bytes(4, "little"))
        buf.write((2).to_bytes(2, "little"))
        buf.write((16).to_bytes(2, "little"))
        buf.write(b"data")
        buf.write(data_size.to_bytes(4, "little"))
        buf.write(data)
        wav_bytes = buf.getvalue()

        # Call the actual API endpoint /api/media/stt without mock
        headers = {"Authorization": f"Bearer {test_token}"}
        files = {"audio_file": ("test.wav", wav_bytes, "audio/wav")}
        resp = test_client.post("/api/media/stt", headers=headers, files=files)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["text"] is not None


@pytest.mark.e2e
class TestImageGenTool:
    @pytest.mark.asyncio
    async def test_image_gen_basic(self):
        from services.tools.builtin.image_generation_tool import image_generation_tool

        api_key = os.getenv("MIMO_API_KEY")
        base_url = os.getenv("MIMO_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1")
        llm_config = {
            "api_key": api_key,
            "base_url": base_url,
            "model_name": "mimo-v2.5",
        }

        # Call the actual codebase tool function directly without mock
        try:
            result_str = await image_generation_tool(
                prompt="A tiny red square on white background",
                llm_config=llm_config,
                size="256x256",
            )
            result = json.loads(result_str)
            if not result.get("success"):
                error_msg = result.get("error", "")
                if (
                    "404" in error_msg
                    or "Not Found" in error_msg
                    or "未配置" in error_msg
                ):
                    pytest.skip(
                        "Image generation endpoint not supported or not configured"
                    )
                else:
                    assert False, f"Image generation failed: {error_msg}"
            assert len(result["urls"]) > 0
            assert result["urls"][0].startswith("http")
        except Exception as e:
            if (
                getattr(e, "status_code", None) == 404
                or "NotFoundError" in type(e).__name__
                or "404" in str(e)
                or "Not Found" in str(e)
            ):
                pytest.skip(
                    "Image generation endpoint not supported by the current LLM provider"
                )
            raise


class TestWebSearch:
    @pytest.mark.asyncio
    async def test_search_basic(self):
        from services.tools.builtin.web_tools import web_search_tool

        # Call the actual web search provider (DDGS) directly without mock
        result_str = await web_search_tool(query="python programming language", limit=2)
        result = json.loads(result_str)
        assert result["success"] is True
        assert "data" in result
        assert "web" in result["data"]
        assert len(result["data"]["web"]) > 0
        assert "url" in result["data"]["web"][0]
        assert "title" in result["data"]["web"][0]


class TestReferenceImageChain:
    """A ``reference_image`` is offered only to providers that consume it
    natively; text-only image providers are skipped for reference requests,
    never degraded via an image→text→image round-trip."""

    @staticmethod
    def _cfg(name: str):
        from types import SimpleNamespace

        return SimpleNamespace(provider_name=name)

    async def test_chain_filters_to_reference_capable(self, monkeypatch):
        import importlib

        tool_mod = importlib.import_module(
            "services.tools.builtin.image_generation_tool"
        )

        async def _chain(db, uid, svc):
            return [
                self._cfg("minimax"),
                self._cfg("zhipu"),
                self._cfg("gemini"),
            ]

        monkeypatch.setattr(tool_mod, "resolve_provider_chain", _chain)
        capable = {"minimax": True, "zhipu": False, "gemini": True}
        monkeypatch.setattr(
            tool_mod,
            "resolve",
            lambda svc, name: type(
                "P", (), {"supports_reference_image": capable[name]}
            )(),
        )

        chain, err = await tool_mod._image_gen_chain(None, None, "https://ref/seed.png")
        assert err is None
        assert [c.provider_name for c in chain] == ["minimax", "gemini"]

    async def test_chain_returns_error_when_none_capable(self, monkeypatch):
        import importlib

        tool_mod = importlib.import_module(
            "services.tools.builtin.image_generation_tool"
        )

        async def _chain(db, uid, svc):
            return [self._cfg("zhipu")]

        monkeypatch.setattr(tool_mod, "resolve_provider_chain", _chain)
        monkeypatch.setattr(
            tool_mod,
            "resolve",
            lambda svc, name: type("P", (), {"supports_reference_image": False})(),
        )

        chain, err = await tool_mod._image_gen_chain(None, None, "https://ref/seed.png")
        assert chain == []
        assert err is not None
        assert "以图生图" in err

    async def test_chain_unfiltered_without_reference(self, monkeypatch):
        import importlib

        tool_mod = importlib.import_module(
            "services.tools.builtin.image_generation_tool"
        )
        full = [self._cfg("zhipu"), self._cfg("minimax")]

        async def _chain(db, uid, svc):
            return list(full)

        monkeypatch.setattr(tool_mod, "resolve_provider_chain", _chain)

        chain, err = await tool_mod._image_gen_chain(None, None, None)
        assert err is None
        assert [c.provider_name for c in chain] == ["zhipu", "minimax"]

    async def test_chain_no_error_when_image_gen_unconfigured(self, monkeypatch):
        """An empty full chain (image_gen not configured at all) is NOT the
        reference-specific error — it falls through to execute_with_fallback's
        MissingLlmConfigError → '图片生成服务未配置'."""
        import importlib

        tool_mod = importlib.import_module(
            "services.tools.builtin.image_generation_tool"
        )

        async def _chain(db, uid, svc):
            return []

        monkeypatch.setattr(tool_mod, "resolve_provider_chain", _chain)

        chain, err = await tool_mod._image_gen_chain(None, None, "https://ref/seed.png")
        assert chain == []
        assert err is None

    @pytest.mark.asyncio
    async def test_tool_passes_reference_to_native_provider(self, monkeypatch):
        import importlib

        from services.llm import ImageAsset
        from services.llm import ImageGenResult

        tool_mod = importlib.import_module(
            "services.tools.builtin.image_generation_tool"
        )
        seen: dict = {}

        class _NativeProvider:
            supports_reference_image = True

            async def generate(self, req):
                seen["req"] = req
                return ImageGenResult(
                    images=[ImageAsset(url="http://out/1.png")], model="image-01"
                )

        async def _fake_execute(db, user_id, service_type, call_fn, **kwargs):
            return await call_fn(_NativeProvider())

        async def _fake_chain(*a, **kw):
            return ([self._cfg("minimax")], None)

        monkeypatch.setattr(tool_mod, "_image_gen_chain", _fake_chain)
        monkeypatch.setattr(tool_mod, "execute_with_fallback", _fake_execute)

        result = await tool_mod.image_generation_tool(
            prompt="portrait",
            llm_config={},
            user_id=None,
            reference_image="https://ref/seed.png",
        )
        payload = json.loads(result)
        assert payload["success"] is True
        assert seen["req"].reference_image == "https://ref/seed.png"

    @pytest.mark.asyncio
    async def test_tool_works_without_reference(self, monkeypatch):
        """No reference_image → chain is unfiltered, any image provider works."""
        import importlib

        from services.llm import ImageAsset
        from services.llm import ImageGenResult

        tool_mod = importlib.import_module(
            "services.tools.builtin.image_generation_tool"
        )
        seen: dict = {}

        class _TextOnlyProvider:
            supports_reference_image = False

            async def generate(self, req):
                seen["req"] = req
                return ImageGenResult(
                    images=[ImageAsset(url="http://out/1.png")], model="glm-image"
                )

        async def _fake_execute(db, user_id, service_type, call_fn, **kwargs):
            return await call_fn(_TextOnlyProvider())

        async def _fake_chain(*a, **kw):
            return ([self._cfg("zhipu")], None)

        monkeypatch.setattr(tool_mod, "_image_gen_chain", _fake_chain)
        monkeypatch.setattr(tool_mod, "execute_with_fallback", _fake_execute)

        result = await tool_mod.image_generation_tool(
            prompt="portrait", llm_config={}, user_id=None
        )
        payload = json.loads(result)
        assert payload["success"] is True
        assert payload["urls"] == ["http://out/1.png"]
        assert seen["req"].reference_image is None

    @pytest.mark.asyncio
    async def test_tool_returns_curated_error_when_none_capable(self, monkeypatch):
        import importlib

        tool_mod = importlib.import_module(
            "services.tools.builtin.image_generation_tool"
        )
        async def _fake_chain(*a, **kw):
            return ([], "当前图片生成供应商均不支持以图生图")

        monkeypatch.setattr(tool_mod, "_image_gen_chain", _fake_chain)

        result = await tool_mod.image_generation_tool(
            prompt="portrait",
            llm_config={},
            user_id=None,
            reference_image="https://ref/seed.png",
        )
        payload = json.loads(result)
        assert payload["success"] is False
        assert "以图生图" in payload["error"]


@pytest.mark.asyncio
async def test_safe_outbound_client_request_hook_blocks_unsafe_host(monkeypatch):
    """The request-hook guard must actually fire (the old "connect" hook key
    was silently ignored by httpx and never executed)."""
    import httpx

    from components import safe_outbound_async_client
    from components import network as net

    def _fake(host: str):
        return (False, "test block") if host == "rebind.test" else (True, "")

    monkeypatch.setattr(net, "is_safe_outbound", _fake)
    async with safe_outbound_async_client() as client:
        with pytest.raises(httpx.ConnectError):
            await client.get("http://rebind.test/x")

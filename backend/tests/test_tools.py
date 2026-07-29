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
            text="Hello, this is a test.",
            llm_config=llm_config,
            voice="mimo_default",
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
            text="你好，这是一个测试。",
            llm_config=llm_config,
            voice="冰糖",
        )
        result = json.loads(result_str)
        assert result["success"] is True
        audio_bytes = base64.b64decode(result["audio_base64"])
        assert len(audio_bytes) > 0


@pytest.mark.e2e
class TestSTTTool:
    def test_stt_basic(self, test_client, test_token):
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
                if "404" in error_msg or "Not Found" in error_msg or "未配置" in error_msg:
                    pytest.skip("Image generation endpoint not supported or not configured")
                else:
                    assert False, f"Image generation failed: {error_msg}"
            assert len(result["urls"]) > 0
            assert result["urls"][0].startswith("http")
        except Exception as e:
            if getattr(e, "status_code", None) == 404 or "NotFoundError" in type(e).__name__ or "404" in str(e) or "Not Found" in str(e):
                pytest.skip("Image generation endpoint not supported by the current LLM provider")
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

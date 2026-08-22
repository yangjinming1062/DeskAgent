from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_stt_reads_upload_without_stream_api(test_client, test_token, monkeypatch):
    from api.v1 import media

    async def fake_chain(*_args, **_kwargs):
        return ["mimo"]

    async def fake_execute(*_args, **_kwargs):
        return SimpleNamespace(text="hello")

    monkeypatch.setattr(media, "resolve_provider_chain", fake_chain)
    monkeypatch.setattr(media, "execute_with_fallback", fake_execute)

    resp = await test_client.post(
        "/api/media/stt",
        headers={"Authorization": f"Bearer {test_token}"},
        files={"audio_file": ("test.wav", b"RIFF", "audio/wav")},
    )

    assert resp.status_code == 200
    assert resp.json() == {"success": True, "text": "hello"}

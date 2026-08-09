from typing import Any

import pytest

from services.companion import seed_stylizer as ss
from services.llm import ImageAsset as LLMImageAsset
from services.llm import ImageGenResult as LLMImageGenResult


class _FakeAsset:
    def __init__(self, *, b64: str | None = None, url: str | None = None, mime: str = "image/png") -> None:
        self.b64 = b64
        self.url = url
        self.mime = mime


class _FakeProvider:
    """Minimal stand-in for an ImageGenProvider. Records calls and returns
    canned results."""

    supports_reference_image = False

    def __init__(self, *, result: Any | None = None, raise_exc: Exception | None = None) -> None:
        self.result = result
        self.raise_exc = raise_exc
        self.calls: list[Any] = []

    async def generate(self, req: Any) -> Any:
        self.calls.append(req)
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.result


# ─── _to_data_uri ──────────────────────────────────────────────────────────


def test_to_data_uri_encodes_bytes_with_mime():
    uri = ss._to_data_uri(b"hello", "image/jpeg")
    assert uri.startswith("data:image/jpeg;base64,")
    assert "aGVsbG8" in uri  # base64 of "hello"


def test_to_data_uri_supports_png():
    uri = ss._to_data_uri(b"\x89PNG", "image/png")
    assert uri.startswith("data:image/png;base64,")


# ─── _asset_to_bytes ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_asset_to_bytes_from_b64():
    asset = _FakeAsset(b64="aGVsbG8=", mime="image/png")  # "hello"
    out = await ss._asset_to_bytes(asset)
    assert out == (b"hello", "image/png")


@pytest.mark.asyncio
async def test_asset_to_bytes_from_b64_with_default_mime():
    asset = _FakeAsset(b64="aGVsbG8=")
    out = await ss._asset_to_bytes(asset)
    assert out == (b"hello", "image/png")  # default mime


@pytest.mark.asyncio
async def test_asset_to_bytes_returns_none_for_empty_asset():
    assert await ss._asset_to_bytes(_FakeAsset()) is None


@pytest.mark.asyncio
async def test_asset_to_bytes_blocks_unsafe_url(monkeypatch):
    def _fake_is_safe_outbound(host):
        return (False, "loopback blocked for test")

    monkeypatch.setattr(ss, "is_safe_outbound", _fake_is_safe_outbound)
    asset = _FakeAsset(url="http://127.0.0.1/seed.png")
    assert await ss._asset_to_bytes(asset) is None


@pytest.mark.asyncio
async def test_asset_to_bytes_rejects_non_http_scheme():
    asset = _FakeAsset(url="ftp://example.com/seed.png")
    assert await ss._asset_to_bytes(asset) is None


@pytest.mark.asyncio
async def test_asset_to_bytes_fetches_safe_url(monkeypatch):
    """A safe http(s) URL asset is fetched with the async client and its
    content-type header wins over the asset mime."""
    import httpx

    url = "https://cdn.example.com/seed.png"

    class _FakeClient:
        def __init__(self, *_a, **_kw):
            self.resp = httpx.Response(
                200,
                content=b"GLBIMG",
                headers={"content-type": "image/jpeg; charset=utf-8"},
                request=httpx.Request("GET", url),
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return None

        async def get(self, url):
            return self.resp

    def _fake_is_safe_outbound(host):
        return (True, "")

    monkeypatch.setattr(ss.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(ss, "is_safe_outbound", _fake_is_safe_outbound)
    asset = _FakeAsset(url=url, mime="image/png")
    out = await ss._asset_to_bytes(asset)
    assert out == (b"GLBIMG", "image/jpeg")


# ─── _bypass ───────────────────────────────────────────────────────────────


def test_bypass_returns_original_with_used_stylization_false():
    res = ss._bypass(b"orig", "image/jpeg", reason="no provider")
    assert res.bytes_ == b"orig"
    assert res.mime == "image/jpeg"
    assert res.used_stylization is False
    assert res.provider_name == ""
    assert res.reason == "no provider"


# ─── Provider resolution ───────────────────────────────────────────────────


def test_provider_supports_reference_returns_false_for_unknown():
    """Unknown provider names must not crash — default to False."""
    from services.llm import ProviderConfig
    from services.llm import ServiceType

    cfg = ProviderConfig(
        base_url="https://x", api_key="k", model="m",
        service_type=ServiceType.image_gen, provider_name="does_not_exist",
    )
    assert ss._provider_supports_reference(cfg) is False


# ─── stylize_seed_for_tripo end-to-end ─────────────────────────────────────


@pytest.mark.asyncio
async def test_stylize_returns_bypass_when_chain_is_empty(monkeypatch):
    """No image_gen provider configured → return seed unchanged."""
    monkeypatch.setattr(ss, "resolve_provider_chain", lambda *_a, **_kw: [])
    res = await ss.stylize_seed_for_tripo(b"orig", "image/jpeg", db=None, user_id=1)
    assert res.bytes_ == b"orig"
    assert res.used_stylization is False
    assert "no image_gen provider" in res.reason


@pytest.mark.asyncio
async def test_stylize_uses_native_reference_provider_when_available(monkeypatch):
    """Provider chain with a native reference-image provider is preferred."""
    from services.llm import ProviderConfig
    from services.llm import ServiceType

    native_cfg = ProviderConfig(
        base_url="https://gemini", api_key="k", model="gemini-image",
        service_type=ServiceType.image_gen, provider_name="gemini",
    )

    captured_calls: list[Any] = []

    class _NativeProvider(_FakeProvider):
        supports_reference_image = True

        async def generate(self, req):
            captured_calls.append(req)
            return LLMImageGenResult(
                images=[LLMImageAsset(b64="aGVsbG8=", mime="image/png")],
                model="gemini-image",
                raw=None,
            )

    def _fake_execute(*_a, **kw):
        # call_fn is invoked with the provider instance
        call_fn = kw["call_fn"]
        provider = _NativeProvider()
        return call_fn(provider)

    monkeypatch.setattr(ss, "resolve_provider_chain", lambda *_a, **_kw: [native_cfg])
    monkeypatch.setattr(ss, "_provider_supports_reference", lambda _c: True)
    monkeypatch.setattr(ss, "execute_with_fallback", _fake_execute)

    res = await ss.stylize_seed_for_tripo(b"orig", "image/jpeg", db=None, user_id=1)

    assert res.used_stylization is True
    assert res.bytes_ == b"hello"
    assert res.mime == "image/png"
    assert res.provider_name == "gemini-image"
    assert len(captured_calls) == 1
    # reference image is the seed wrapped as data URI
    assert captured_calls[0].reference_image.startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_stylize_falls_back_to_describe_chain_when_native_fails(monkeypatch):
    "When the native chain throws, the describe+regenerate path takes over."
    from services.llm import ProviderConfig
    from services.llm import ServiceType

    native_cfg = ProviderConfig(
        base_url="https://gemini", api_key="k", model="gemini-image",
        service_type=ServiceType.image_gen, provider_name="gemini",
    )
    text_cfg = ProviderConfig(
        base_url="https://mimo", api_key="k", model="dall-e-3",
        service_type=ServiceType.image_gen, provider_name="mimo",
    )

    call_log: list[list[str]] = []

    async def _text_provider_generate(req):
        return LLMImageGenResult(
            images=[LLMImageAsset(b64="ZGVzY3JpYmVk", mime="image/png")],
            model="dall-e-3",
            raw=None,
        )

    class _TextProvider:
        async def generate(self, req):
            return await _text_provider_generate(req)

    async def _fake_describe(*_a, **_kw):
        return "a young woman with long dark hair wearing a cream sweater and blue jeans"

    async def _execute_router(*args, **kwargs):
        # args: db, user_id, service_type, call_fn (positional)
        # kwargs: _chain (when present)
        call_fn = kwargs.get("call_fn")
        if call_fn is None and len(args) >= 4:
            call_fn = args[3]
        chain = kwargs.get("_chain") or []
        names = [c.provider_name for c in chain]
        call_log.append(names)
        if "mimo" in names:
            return await call_fn(_TextProvider())
        # Native path: simulate failure
        raise RuntimeError("native provider down")

    monkeypatch.setattr(ss, "resolve_provider_chain", lambda *_a, **_kw: [native_cfg, text_cfg])
    monkeypatch.setattr(ss, "_provider_supports_reference", lambda c: c.provider_name == "gemini")
    monkeypatch.setattr(ss, "execute_with_fallback", _execute_router)
    monkeypatch.setattr(ss, "describe_reference_image", _fake_describe)

    res = await ss.stylize_seed_for_tripo(b"orig", "image/jpeg", db=None, user_id=1)

    assert res.used_stylization is True
    assert res.bytes_ == b"described"
    assert len(call_log) >= 2
    assert call_log[0] == ["gemini"]
    assert call_log[1] == ["mimo"]

@pytest.mark.asyncio
async def test_stylize_returns_bypass_when_all_chains_fail(monkeypatch):
    """Both chains fail → return original seed untouched."""
    from services.llm import ProviderConfig
    from services.llm import ServiceType

    cfg = ProviderConfig(
        base_url="https://gemini", api_key="k", model="gemini-image",
        service_type=ServiceType.image_gen, provider_name="gemini",
    )

    async def _always_fail(*_a, **_kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr(ss, "resolve_provider_chain", lambda *_a, **_kw: [cfg])
    monkeypatch.setattr(ss, "_provider_supports_reference", lambda _c: True)
    monkeypatch.setattr(ss, "execute_with_fallback", _always_fail)

    res = await ss.stylize_seed_for_tripo(b"orig", "image/jpeg", db=None, user_id=1)
    assert res.bytes_ == b"orig"
    assert res.used_stylization is False
    assert "failed" in res.reason


# ─── StylizationResult ─────────────────────────────────────────────────────


def test_stylization_result_is_frozen():
    """Result should be a frozen dataclass — callers can't mutate it."""
    r = ss.StylizationResult(bytes_=b"x", mime="image/png", used_stylization=True, provider_name="m", reason="r")
    with pytest.raises((AttributeError, TypeError)):
        r.bytes_ = b"y"  # type: ignore[misc]
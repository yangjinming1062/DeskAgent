import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from tools.browser.engine.dom_snapshot import build_snapshot_text
from tools.browser.engine.launcher import (
    find_browser_binary,
    launch_chromium,
)
from tools.browser.schemas import ALL_BROWSER_SCHEMAS, BROWSER_SCHEMA_MAP
from tools.browser.session import (
    _navigation_session_key,
    _url_is_private,
    cleanup_all_browsers,
    get_or_create_session,
    is_local_sidecar_key,
)
from tools.browser.supervisor import SUPERVISOR_REGISTRY, CDPSupervisor

# ── 1. Schema 数量与合法性测试 ──


def test_all_33_schemas_registered_and_valid():
    assert len(ALL_BROWSER_SCHEMAS) == 33
    assert len(BROWSER_SCHEMA_MAP) == 33
    for name, schema in BROWSER_SCHEMA_MAP.items():
        assert schema["name"] == name
        assert "description" in schema
        assert "parameters" in schema
        assert schema["parameters"]["type"] == "object"


# ── 2. dom_snapshot 单元测试 ──


def test_build_snapshot_skips_ignored_node_but_recurses_children():
    nodes = [
        {"nodeId": "1", "role": {"value": "RootWebArea"}, "childIds": ["2"]},
        {"nodeId": "2", "role": {"value": "generic"}, "ignored": True, "childIds": ["3"]},
        {"nodeId": "3", "role": {"value": "button"}, "name": {"value": "Submit"}, "backendDOMNodeId": 101, "childIds": []},
    ]
    text, refs = build_snapshot_text(nodes)
    assert 'button "Submit"' in text
    assert "generic" not in text
    assert "e1" in refs
    assert refs["e1"]["backendNodeId"] == 101


def test_build_snapshot_escapes_quotes_in_name():
    nodes = [
        {"nodeId": "1", "role": {"value": "button"}, "name": {"value": 'Click "Here"'}, "backendDOMNodeId": 102, "childIds": []},
    ]
    text, refs = build_snapshot_text(nodes)
    assert 'button "Click \\"Here\\""' in text


def test_build_snapshot_assigns_sequential_refs_e1_e2_e3():
    nodes = [
        {"nodeId": "1", "role": {"value": "button"}, "name": {"value": "One"}, "backendDOMNodeId": 1, "childIds": ["2"]},
        {"nodeId": "2", "role": {"value": "link"}, "name": {"value": "Two"}, "backendDOMNodeId": 2, "childIds": ["3"]},
        {"nodeId": "3", "role": {"value": "textbox"}, "name": {"value": "Three"}, "backendDOMNodeId": 3, "childIds": []},
    ]
    text, refs = build_snapshot_text(nodes)
    assert "[ref=e1] button" in text
    assert "[ref=e2] link" in text
    assert "[ref=e3] textbox" in text
    assert "e1" in refs and "e2" in refs and "e3" in refs
    assert "@e1" in refs and "@e2" in refs and "@e3" in refs


def test_build_snapshot_interactive_only_filters_static_text():
    nodes = [
        {"nodeId": "1", "role": {"value": "generic"}, "childIds": ["2", "3"]},
        {"nodeId": "2", "role": {"value": "StaticText"}, "name": {"value": "Static headline"}, "childIds": []},
        {"nodeId": "3", "role": {"value": "button"}, "name": {"value": "Action"}, "backendDOMNodeId": 201, "childIds": []},
    ]
    text, refs = build_snapshot_text(nodes, interactive_only=True)
    assert 'button "Action"' in text
    assert "Static headline" not in text


def test_build_snapshot_handles_deeply_nested_divs_above_8_depth():
    nodes = []
    for i in range(12):
        nid = str(i + 1)
        cid = str(i + 2) if i < 11 else None
        node = {
            "nodeId": nid,
            "role": {"value": "generic" if i < 11 else "button"},
            "name": {"value": f"Level {i}" if i == 11 else ""},
            "childIds": [cid] if cid else [],
            "backendDOMNodeId": 500 + i,
        }
        nodes.append(node)

    text, _refs = build_snapshot_text(nodes, max_depth=50)
    assert 'button "Level 11"' in text


def test_build_snapshot_annotates_properties():
    nodes = [
        {
            "nodeId": "1",
            "role": {"value": "link"},
            "name": {"value": "Docs"},
            "properties": [{"name": "url", "value": {"value": "https://example.com/docs"}}],
            "childIds": [],
            "backendDOMNodeId": 10,
        },
        {
            "nodeId": "2",
            "role": {"value": "textbox"},
            "name": {"value": "Email"},
            "properties": [{"name": "value", "value": {"value": "test@example.com"}}, {"name": "required", "value": True}],
            "childIds": [],
            "backendDOMNodeId": 11,
        },
        {
            "nodeId": "3",
            "role": {"value": "checkbox"},
            "name": {"value": "Subscribe"},
            "properties": [{"name": "checked", "value": {"value": "true"}}],
            "childIds": [],
            "backendDOMNodeId": 12,
        },
    ]
    text, refs = build_snapshot_text(nodes)
    assert 'href="https://example.com/docs"' in text
    assert 'value="test@example.com"' in text
    assert "required" in text
    assert "checked=true" in text


# ── 3. launcher 单元测试 ──


def test_find_browser_binary_respects_custom_config(monkeypatch):
    monkeypatch.setattr("tools.browser.engine.launcher.load_config", lambda: {"browser": {"executable_path": sys.executable}})
    p = find_browser_binary()
    assert p == Path(sys.executable)


def test_launch_chromium_polls_devtools_active_port(tmp_path, monkeypatch):
    fake_proc = MagicMock()
    fake_proc.pid = 9999
    fake_proc.poll.return_value = None

    port_file = tmp_path / "DevToolsActivePort"

    def fake_popen(args, **kwargs):
        # 写入 DevToolsActivePort 模拟 Chromium 启动
        port_file.write_text("55555\n/devtools/browser/fake-guid\n", encoding="utf-8")
        return fake_proc

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    monkeypatch.setattr("tools.browser.engine.launcher.find_browser_binary", lambda: Path(sys.executable))

    nbp = launch_chromium(profile_dir=tmp_path, startup_timeout_s=5.0)
    assert nbp.pid == 9999
    assert nbp.port == 55555
    assert nbp.cdp_url == "ws://127.0.0.1:55555/devtools/browser/fake-guid"


def test_launch_chromium_injects_headless_when_requested(tmp_path, monkeypatch):
    captured_args = []
    fake_proc = MagicMock()
    fake_proc.pid = 9998
    fake_proc.poll.return_value = None

    def fake_popen(args, **kwargs):
        captured_args.extend(args)
        (tmp_path / "DevToolsActivePort").write_text("55555\n/devtools/browser/guid\n", encoding="utf-8")
        return fake_proc

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    monkeypatch.setattr("tools.browser.engine.launcher.find_browser_binary", lambda: Path(sys.executable))

    launch_chromium(profile_dir=tmp_path, headless=True)
    assert "--headless=new" in captured_args

    captured_args.clear()
    launch_chromium(profile_dir=tmp_path, headless=False)
    assert "--headless=new" not in captured_args


# ── 4. supervisor 方法测试 ──


@pytest.mark.asyncio
async def test_attach_initial_page_enables_correct_domains(monkeypatch):
    sup = CDPSupervisor(task_id="t-domains", cdp_url="ws://127.0.0.1:1")
    enabled_domains = []

    async def fake_cdp(method, params=None, session_id=None, timeout=10.0):
        if method == "Target.getTargets":
            return {"result": {"targetInfos": [{"targetId": "t1", "type": "page"}]}}
        if method == "Target.attachToTarget":
            return {"result": {"sessionId": "s1"}}
        if method == "Page.getFrameTree":
            return {"result": {"frameTree": {"frame": {"id": "root-1"}}}}
        enabled_domains.append((method, params))
        return {"result": {}}

    sup._cdp = fake_cdp
    await sup._attach_initial_page()

    called_methods = [m[0] for m in enabled_domains]
    assert "Page.enable" in called_methods
    assert "Page.setLifecycleEventsEnabled" in called_methods
    assert "Runtime.enable" in called_methods
    assert "Accessibility.enable" in called_methods
    assert "DOM.enable" in called_methods
    assert "Input.enable" not in called_methods


def test_supervisor_type_ref_uses_select_and_clear():
    sup = CDPSupervisor(task_id="t-type", cdp_url="ws://127.0.0.1:1")
    sup._page_session_id = "s1"
    sup._last_refs = {"e1": {"backendNodeId": 100}}

    calls = []

    def fake_send_cdp(method, params=None, timeout=10.0, session_id=None):
        calls.append((method, params))
        if method == "DOM.resolveNode":
            return {"ok": True, "result": {"object": {"objectId": "obj-1"}}}
        if method == "Runtime.callFunctionOn":
            return {"ok": True, "result": {"result": {"value": True}}}
        if method == "DOM.getBoxModel":
            return {"ok": True, "result": {"model": {"content": [10, 10, 50, 10, 50, 30, 10, 30]}}}
        return {"ok": True, "result": {}}

    sup.send_cdp = fake_send_cdp
    res = sup.type_ref("e1", "new-text")

    assert res["ok"] is True
    assert res["typed"] == "new-text"

    called_methods = [c[0] for c in calls]
    assert "DOM.resolveNode" in called_methods
    assert "Runtime.callFunctionOn" in called_methods
    assert "Input.insertText" in called_methods


def test_supervisor_press_key_maps_key_code():
    sup = CDPSupervisor(task_id="t-press", cdp_url="ws://127.0.0.1:1")
    sup._page_session_id = "s1"

    calls = []

    def fake_send_cdp(method, params=None, timeout=10.0, session_id=None):
        calls.append((method, params))
        return {"ok": True}

    sup.send_cdp = fake_send_cdp
    res = sup.press_key("Enter")
    assert res["ok"] is True
    assert any(c[0] == "Input.dispatchKeyEvent" and c[1].get("windowsVirtualKeyCode") == 13 for c in calls)


# ── 5. session.py 路由与清理 ──


def test_session_routing_private_urls(monkeypatch):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        if host in ("127.0.0.1", "localhost"):
            return [(2, 1, 6, "", ("127.0.0.1", port or 0))]
        return [(2, 1, 6, "", ("93.184.216.34", port or 0))]

    monkeypatch.setattr("socket.getaddrinfo", fake_getaddrinfo)
    assert _url_is_private("http://127.0.0.1:8080") is True
    assert _url_is_private("http://localhost:3000") is True
    assert _url_is_private("https://example.com") is False

    key_pub = _navigation_session_key("task-1", "https://example.com")
    assert key_pub == "task-1"
    assert is_local_sidecar_key(key_pub) is False

    key_priv = _navigation_session_key("task-1", "http://127.0.0.1:8080")
    assert key_priv.startswith("task-1")
    assert is_local_sidecar_key(key_priv) is True


def test_cleanup_all_browsers_stops_supervisor_and_native_process():
    fake_handle = MagicMock()
    fake_sup = MagicMock()
    fake_sup._active = True
    sess = get_or_create_session("t-clean")
    sess.launch_handle = fake_handle
    SUPERVISOR_REGISTRY._supervisors["t-clean"] = fake_sup

    cleanup_all_browsers()

    fake_sup.stop.assert_called()
    fake_handle.terminate.assert_called()
    assert SUPERVISOR_REGISTRY.get("t-clean") is None


@pytest.mark.asyncio
async def test_backoff_uses_full_jitter(monkeypatch):
    from tools.browser.supervisor import CDPSupervisor

    sup = CDPSupervisor(task_id="t-jitter", cdp_url="ws://127.0.0.1:9999")
    sup._ready_event.set()  # avoid blocking on initial start error
    sup._stop_requested = False

    sleep_args = []

    async def fake_sleep(d):
        sleep_args.append(d)
        if len(sleep_args) >= 5:
            sup._stop_requested = True

    async def fake_connect(*args, **kwargs):
        raise ConnectionRefusedError("Connection refused")

    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    monkeypatch.setattr("websockets.connect", fake_connect)

    await sup._run()
    assert len(sleep_args) >= 5
    # Full jitter: sleeps must vary and not be strictly exponential powers of 2
    assert all(0.0 <= s <= 10.0 for s in sleep_args)
    assert len(set(sleep_args)) > 1  # Verify randomness

import json
from tools.registry import registry
import tools.system.activity_tools  # noqa: F401


def test_spatial_tools_registered():
    schemas = registry.get_schemas()
    tool_names = {s["name"] for s in schemas}
    assert "system.get_work_area" in tool_names
    assert "system.get_cursor_pos" in tool_names
    assert "system.click_at" in tool_names


def test_get_work_area_handler():
    res_str = registry.dispatch("system.get_work_area", {})
    res = json.loads(res_str)
    assert "x" in res and "y" in res and "w" in res and "h" in res
    assert isinstance(res["w"], int) and isinstance(res["h"], int)


def test_get_cursor_pos_handler():
    res_str = registry.dispatch("system.get_cursor_pos", {})
    res = json.loads(res_str)
    assert "x" in res and "y" in res
    assert isinstance(res["x"], int) and isinstance(res["y"], int)


def test_click_at_handler():
    res_str = registry.dispatch("system.click_at", {"x": 100, "y": 100, "button": "left", "clicks": 1})
    res = json.loads(res_str)
    assert "clicked" in res



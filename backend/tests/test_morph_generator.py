import pytest
from services.companion.morph_generator import validate_and_sanitize_expression


def test_validate_and_sanitize_expression_valid():
    raw = {
        "name": "tender_worry",
        "label": "心疼",
        "valence": "negative",
        "description": "Used when companion feels concerned for the user",
        "weights": {"smile": 0.2, "frown": 0.5, "browDown": 0.4, "unknownShape": 0.9},
        "tags": ["温柔", "心疼"],
        "scale_boost": 1.2,
    }
    result = validate_and_sanitize_expression(raw)
    assert result is not None
    assert result["name"] == "tender_worry"
    assert result["label"] == "心疼"
    assert result["valence"] == "negative"
    assert result["scale_boost"] == 1.2
    assert "unknownShape" not in result["weights"]
    assert result["weights"]["smile"] == 0.2
    assert result["weights"]["frown"] == 0.5


def test_validate_and_sanitize_expression_clamps_weights_and_scale():
    raw = {
        "name": "super_smile",
        "label": "大笑",
        "valence": "positive",
        "description": "Big smile",
        "weights": {"smile": 1.8, "browUp": -0.5},
        "tags": ["大笑"],
        "scale_boost": 5.0,
    }
    result = validate_and_sanitize_expression(raw)
    assert result is not None
    assert result["weights"]["smile"] == 1.0
    assert "browUp" not in result["weights"]
    assert result["scale_boost"] == 3.0  # max clamp (3.0)


def test_validate_and_sanitize_expression_invalid_inputs():
    assert validate_and_sanitize_expression(None) is None
    assert validate_and_sanitize_expression("not a dict") is None
    assert validate_and_sanitize_expression({"name": ""}) is None
    assert validate_and_sanitize_expression({"name": "test", "label": "test", "weights": {}}) is None

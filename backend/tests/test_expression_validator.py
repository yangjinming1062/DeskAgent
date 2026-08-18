from services.companion import validate_and_sanitize_expression


def _raw(**over):
    base = {"name": "tender_worry", "label": "心疼", "description": "心疼又担忧地看着你", "valence": "negative", "tags": ["温柔"], "icon": "🥺"}
    base.update(over)
    return base


def test_validate_and_sanitize_expression_valid():
    result = validate_and_sanitize_expression(_raw())
    assert result == {"name": "tender_worry", "label": "心疼", "valence": "negative", "description": "心疼又担忧地看着你", "icon": "🥺", "tags": ["温柔"]}


def test_valid_minimal_fields():
    result = validate_and_sanitize_expression({"name": "Tender_Worry", "description": "担忧的神情"})
    assert result == {"name": "tender_worry", "label": "tender_worry", "valence": "neutral", "description": "担忧的神情", "icon": None, "tags": []}


def test_description_is_required():
    # description doubles as the avatar-image generation clause — an empty one
    # would generate a face with no expression.
    assert validate_and_sanitize_expression(_raw(description="")) is None
    assert validate_and_sanitize_expression({"name": "x_y"}) is None


def test_invalid_inputs():
    assert validate_and_sanitize_expression(None) is None
    assert validate_and_sanitize_expression("not a dict") is None
    assert validate_and_sanitize_expression({"name": ""}) is None
    assert validate_and_sanitize_expression(_raw(name="1_bad_start")) is None
    assert validate_and_sanitize_expression(_raw(valence="spicy")) is None

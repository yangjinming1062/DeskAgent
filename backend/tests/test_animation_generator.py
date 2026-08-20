from services.companion import get_rig_bones
from services.companion.animation_generator import validate_and_sanitize_clip


def test_validate_and_sanitize_clip():
    raw_clip = {
        "name": "poke_test",
        "duration": 2.0,
        "loop": True,
        "category": "interaction",
        "tags": ["活泼"],
        "tracks": {
            "Head": [
                {"t": 0, "r": [0, 0, 0]},
                {"t": 1.0, "r": [0.2, -0.1, 0.05]},
                {
                    "t": 1.5,
                    "r": [0.5, 0.5, 0.5],
                },  # loop 会把末帧修正为 t=2.0 且 r=[0,0,0]
            ]
        },
    }

    sanitized = validate_and_sanitize_clip(raw_clip, allowed_bones={"Head", "Spine"})
    assert sanitized is not None
    assert sanitized["name"] == "poke_test"
    assert sanitized["loop"] is True
    assert sanitized["tracks"]["Head"][-1]["t"] == 2.0
    assert sanitized["tracks"]["Head"][-1]["r"] == [0, 0, 0]


def test_get_rig_bones():
    biped_bones = get_rig_bones("biped")
    assert "Head" in biped_bones
    assert "Hips" in biped_bones

    quad_bones = get_rig_bones("quadruped")
    assert "LeftFrontLeg" in quad_bones

    fallback_bones = get_rig_bones("unknown_species")
    assert fallback_bones == biped_bones

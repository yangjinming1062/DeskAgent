import json
import pytest

from services.companion.animation_generator import (
    find_unmatched_tags,
    generate_animation_clips,
    get_rig_bones,
    validate_and_sanitize_clip,
)


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
                },  # loop will fix final keyframe to t=2.0 and r=[0, 0, 0]
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

    # fallback
    fallback_bones = get_rig_bones("unknown_species")
    assert fallback_bones == biped_bones


@pytest.mark.asyncio
async def test_find_unmatched_tags():
    existing_clips = [
        {"name": "c1", "tags": ["活泼", "元气"]},
        {"name": "c2", "tags": ["温柔"]},
    ]

    unmatched = await find_unmatched_tags(
        ["活泼", "妖娆", "妩媚"], rig_type="biped", existing_clips=existing_clips
    )
    assert unmatched == ["妖娆", "妩媚"]


@pytest.mark.asyncio
async def test_generate_animation_clips():
    async def mock_chat(
        db, user_id, system_prompt, user_payload, *, provider_config=None
    ):
        return json.dumps(
            [
                {
                    "name": "seductive_look",
                    "duration": 2.0,
                    "loop": False,
                    "category": "interaction",
                    "tags": ["妖娆"],
                    "tracks": {
                        "Head": [
                            {"t": 0, "r": [0, 0, 0]},
                            {"t": 2.0, "r": [0.1, 0.1, 0]},
                        ]
                    },
                }
            ]
        )

    clips = await generate_animation_clips(
        mock_chat,
        rig_type="biped",
        bone_list=["Head", "Spine"],
        personality_tags=["妖娆", "妩媚"],
        species="人类",
    )

    assert len(clips) == 1
    assert clips[0]["name"] == "seductive_look"
    assert clips[0]["tags"] == ["妖娆"]

import asyncio
import json
import sys
import time
from pathlib import Path

from services.llm.providers.tripo import client as tripo

EXPLORATION_DIR = Path(__file__).resolve().parents[2] / "data" / "tripo-exploration"
EXPLORATION_DIR.mkdir(parents=True, exist_ok=True)
GLB_PATH = EXPLORATION_DIR / "rig_exploration.glb"
METADATA_PATH = EXPLORATION_DIR / "rig_exploration.json"


async def run() -> int:
    print(f"exploration output dir: {EXPLORATION_DIR}")
    balance = await tripo.account_balance()
    print(f"balance: {balance}")
    if balance.get("balance", 0) <= 0:
        print("ERROR: account balance is 0; top up at https://platform.tripo3d.ai before re-running", file=sys.stderr)
        return 2

    print("step 1: text-to-model (bipedal seed)...")
    text_task = await tripo.create_text_to_model("a simple cartoon bipedal character, A-pose, neutral T-pose, white background", model_version=tripo.MODEL_VERSION_DEFAULT)
    print(f"  text_to_model task_id: {text_task}")
    print("  waiting for text_to_model to finish...")
    await tripo.poll_task(text_task, interval=2.0, timeout=300.0)

    print("step 2: rig-check...")
    prerigcheck_task = await tripo.rig_check(text_task)
    prerigcheck_output = await tripo.poll_rig_check(prerigcheck_task)
    recommended = prerigcheck_output.get("rig_type", "biped")
    riggable = prerigcheck_output.get("riggable", True)
    print(f"  rig-check riggable: {riggable}")
    print(f"  rig-check recommended rig_type: {recommended}")

    print(f"step 3: rig with spec=tripo (rig_type={recommended})...")
    rig_task = await tripo.rig(text_task, recommended, spec="tripo", model_version=tripo.MODEL_VERSION_TRIPO)
    print(f"  rig task_id: {rig_task}")
    print("step 4: poll rig task...")
    rig_result = await tripo.poll_task(rig_task, interval=3.0, timeout=900.0)
    model_url = rig_result["output"]["model_url"]
    print(f"  model_url (5-min TTL): {model_url}")

    print(f"step 5: download to {GLB_PATH}...")
    raw = await tripo.download_model(model_url)
    GLB_PATH.write_bytes(raw)
    print(f"  wrote {len(raw)} bytes")

    metadata = {
        "text_to_model_task_id": text_task,
        "rig_check_prerigcheck_task_id": prerigcheck_task,
        "rig_check_recommended_rig_type": recommended,
        "rig_check_riggable": riggable,
        "rig_task_id": rig_task,
        "model_url": model_url,
        "model_version_used": tripo.MODEL_VERSION_TRIPO,
        "spec_used": "tripo",
        "glb_path": str(GLB_PATH),
        "glb_size_bytes": len(raw),
        "saved_at": int(time.time()),
    }
    METADATA_PATH.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"step 6: metadata saved to {METADATA_PATH}")
    print()
    print("NEXT STEPS (human verification):")
    try:
        inspect_path = GLB_PATH.relative_to(Path.cwd())
    except ValueError:
        inspect_path = GLB_PATH
    print(f"  1. open {GLB_PATH} in Blender (5.2 LTS confirmed) or run")
    print(f"     ``glTF-Transform inspect {inspect_path}``")
    print("  2. read the bone names from ``skins[].joints[]`` (probably named after the original glTF node names)")
    print("  3. populate ``TRIPO_QUADRUPED_BONES`` in client/renderer/companion/3d/clips-quadruped.ts (and the other rig libraries)")
    print("     with the actual names so the placeholder 2-keyframe clips become valid AnimationTracks.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))

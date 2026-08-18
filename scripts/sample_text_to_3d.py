"""Sample the text-to-3D path across Tripo / Hunyuan with style-wording variants.

Fixed experiment subject: 梦蝶 (persona definition + bust avatar below). Per cell
of the provider × wording matrix:

  1. Appearance extraction runs through the production enhancer
     (``services.llm.prompt_engineer.enhance_t3d_prompt``) — the vision LLM
     reads the avatar; the persona text is only the skeleton.
  2. The prompt is assembled by the production ``build_t3d_prompt`` (fixed 3D
     suffix + style wording + 1024-char cap) — samples can never drift from
     what the backend would send.
  3. Submission goes through the production providers' ``submit_text_to_model``;
     results land as bare GLBs under ``backend/data/_text_to_3d_samples/<stamp>/``
     (``--rig`` additionally runs the local Blender auto-rig the hunyuan image
     pipeline uses, to gauge riggability of prompt-only poses).

The script never touches production data: no DB access, no companion-assets
writes, no AvatarAsset/CompanionModel rows.

Run via the backend's own venv so all provider packages import cleanly:

    cd /path/to/SpiritAgent
    ./backend/.venv/Scripts/python.exe scripts/sample_text_to_3d.py [--rig]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.llm import T3DAppearance

# ── Repo + venv path bootstrap (before any backend imports) ──────────

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ── Fixed inputs (梦蝶; replace per-run by editing these constants) ───

AVATAR_PATH = REPO_ROOT / "backend" / "data" / "companion-avatars" / "331lKEZNtmlS-1m5t3FNag.jpg"

PERSONA_DEFINITION: dict[str, str] = {
    "name": "梦蝶",
    "personality": "温柔体贴、善解人意，同时又聪明伶俐",
    "speaking_style": "俏皮带点小傲娇，时不时爱说一些诱惑撩人的话语。但是又懂得分寸场合，在需要正经的场合又能保持严肃认真",
    "appearance_core": "容貌姣好，身姿曼妙，拥有一头美丽的长发",
    "voice": "冰糖",
}

# 余额紧张：默认只跑单格（一次付费生成）。多格对比靠显式传参。
PROVIDERS: list[str] = ["tripo"]
WORDINGS: list[str] = ["anime"]


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sample text-to-3D per provider × style wording (direct provider calls, no DB).")
    p.add_argument("--avatar", type=Path, default=AVATAR_PATH, help="Path to the bust avatar the vision LLM extracts appearance from.")
    p.add_argument("--providers", default=",".join(PROVIDERS), help="Comma-separated provider names (default: tripo).")
    p.add_argument("--wordings", default=",".join(WORDINGS), help="Comma-separated style wordings (default: anime).")
    p.add_argument("--style", choices=["anime", "realistic"], default="anime", help="Style route fed to build_t3d_prompt (default: anime — 梦蝶是人类).")
    p.add_argument("--tripo-model", default=None, help="Override Tripo model_version (e.g. P1-20260311 stylized low-poly vs the settings default v3.1).")
    p.add_argument("--hunyuan-model", default=None, help="Override Hunyuan model version (default: SETTINGS.hunyuan_model_version, i.e. hy-3d-3.1).")
    p.add_argument("--rig", action="store_true", help="Additionally run the local Blender auto-rig on each product (riggability gate).")
    p.add_argument("--output-dir", type=Path, default=None, help="Override output dir; default backend/data/_text_to_3d_samples/<UTC-timestamp>/.")
    return p.parse_args()


# ── Appearance extraction (production enhancer, global config chain) ─


async def _extract_appearance(avatar_path: Path) -> T3DAppearance | str:
    import base64
    import mimetypes

    from services.llm import enhance_t3d_prompt, resolve_vision_chain

    mime = mimetypes.guess_type(avatar_path.name)[0] or "image/jpeg"
    data_uri = f"data:{mime};base64,{base64.b64encode(avatar_path.read_bytes()).decode('ascii')}"
    vision_chain = await resolve_vision_chain(None, None)
    print(f"[setup] vision chain: {[c.provider_name for c in vision_chain] or 'NONE → text fallback'}")
    persona = SimpleNamespace(definition_json=json.dumps(PERSONA_DEFINITION, ensure_ascii=False))
    structured = await enhance_t3d_prompt(None, None, persona, image_data_uri=data_uri, vision_chain=vision_chain)
    print(f"[setup] appearance: {structured!r}")
    return structured


# ── One matrix cell: submit → poll → download ────────────────────────


async def _sample_cell(*, provider_name: str, wording: str, prompt: str, output_dir: Path, rig: bool, tripo_model: str | None) -> Path:
    from components import SETTINGS
    from services.image_to_3d import ImageTo3DError, resolve_provider

    stem = f"{provider_name}_{wording}"
    try:
        provider = resolve_provider(provider_name)
        job = await provider.submit_text_to_model(prompt, model_version=tripo_model) if provider_name == "tripo" and tripo_model else await provider.submit_text_to_model(prompt)
        print(f"  [{stem}] submitted task {job.job_id}")
        deadline = time.monotonic() + SETTINGS.image_to_3d_max_poll_seconds
        while (result := await provider.poll(job)).status not in ("completed", "failed"):
            if time.monotonic() > deadline:
                raise ImageTo3DError(f"poll timeout ({SETTINGS.image_to_3d_max_poll_seconds:.0f}s)", provider=provider_name)
            print(f"  [{stem}] {result.status} {result.progress}%")
            await asyncio.sleep(SETTINGS.image_to_3d_poll_interval_seconds)
        if result.status == "failed":
            raise ImageTo3DError(f"provider task failed: {result.error}", provider=provider_name)

        cell_dir = output_dir / stem
        glb_path = await provider.download(result, cell_dir)
        final_path = cell_dir / f"{stem}.glb"
        glb_path.rename(final_path)

        if rig:
            from services.companion.model_service import _auto_rig_with_blender

            rigged = await _auto_rig_with_blender(final_path.read_bytes(), "biped", io_dir=cell_dir)
            rigged_path = cell_dir / f"{stem}_rigged.glb"
            rigged_path.write_bytes(rigged)
            print(f"  [{stem}] OK   -> {final_path}  ({final_path.stat().st_size} B) + rigged {rigged_path.stat().st_size} B")
        else:
            print(f"  [{stem}] OK   -> {final_path}  ({final_path.stat().st_size} B)")
        return final_path
    except Exception as exc:
        err_path = output_dir / f"{stem}.error.txt"
        err_path.parent.mkdir(parents=True, exist_ok=True)
        err_path.write_text("".join(traceback.format_exception(exc)), encoding="utf-8")
        print(f"  [{stem}] EXC: {exc.__class__.__name__}: {exc}")
        return err_path


async def main() -> None:
    args = _parse_args()

    from components import SETTINGS
    from services.llm import build_t3d_prompt

    if not args.avatar.is_file():
        sys.exit(f"头像不存在：{args.avatar}")
    if args.hunyuan_model:
        SETTINGS.hunyuan_model_version = args.hunyuan_model

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or REPO_ROOT / "backend" / "data" / "_text_to_3d_samples" / stamp
    output_dir.mkdir(parents=True, exist_ok=True)

    appearance = await _extract_appearance(args.avatar)
    providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    wordings = [w.strip() for w in args.wordings.split(",") if w.strip()]
    prompts = {w: build_t3d_prompt(appearance, args.style, wording=w) for w in wordings}

    for wording, prompt in prompts.items():
        print(f"[setup] {wording} prompt ({len(prompt)} chars):")
        print(prompt)
        (output_dir / f"prompt_{wording}.txt").write_text(prompt, encoding="utf-8")

    print(f"[setup] output_dir: {output_dir}")
    print(f"[setup] matrix: {providers} × {wordings}  style={args.style}  rig={args.rig}")

    tasks = [
        _sample_cell(
            provider_name=p,
            wording=w,
            prompt=prompts[w],
            output_dir=output_dir,
            rig=args.rig,
            tripo_model=args.tripo_model,
        )
        for p in providers
        for w in wordings
    ]
    results = await asyncio.gather(*tasks)
    written = sum(1 for r in results if isinstance(r, Path) and r.suffix == ".glb")
    print()
    print(f"[done] wrote {written}/{len(results)} GLBs to {output_dir}")
    if written < len(results):
        print(f"[done] {len(results) - written} cell(s) failed — see *.error.txt alongside")
    print("[next] 目检：面部比例/风格/躯干四肢完整度；rigged GLB 用 Blender 打开检查骨骼与蒙皮")


if __name__ == "__main__":
    asyncio.run(main())

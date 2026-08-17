"""Sample the new anime-style fullbody seed across image-gen providers.

Drives the project's image-gen providers **directly** (no DB, no
``image_generation_tool``) so the script works even when the production
PostgreSQL host isn't reachable. The reference image is fixed
(``backend/data/参考图.jpg``); species and style branch are CLI flags so the
user can compare providers side-by-side per style routing.

Pipeline per sample:
  1. Build the fullbody prompt through the **production** template resolver
     (``services.llm.prompt_engineer``) — samples can never drift from what
     the backend actually sends. ``--style`` picks the anime/realistic branch
     directly (``auto`` routes the species the way the backend would).
  2. Construct the named provider (grok / gemini / minimax) directly with
     a ``ProviderConfig`` populated from ``backend/config.toml`` and call
     ``provider.generate(req)`` once. Bypasses the DB-backed chain
     resolver used by ``image_generation_tool``.
  3. PNG bytes are decoded from the returned ``ImageAsset.b64`` and
     written to ``backend/data/_fullbody_samples/<timestamp>/<provider>_<n>.png``.

The script does NOT touch production data:
  - No writes to ``backend/data/companion-avatars/`` or
    ``backend/data/companion-assets/<uid>/``.
  - No DB writes, no asset URL persistence, no ``AvatarAsset`` rows
    created.
  - No modifications to ``_FULLBODY_PROVIDER_PRIORITY`` or any
    provider chain — samples use the production default ordering so
    the user sees what each provider emits against the same prompt.

Run via the backend's own venv so all provider packages import cleanly:

    cd /path/to/SpiritAgent
    ./backend/.venv/Scripts/python.exe scripts/sample_fullbody_providers.py
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import sys
import tomllib
import traceback
from datetime import UTC, datetime
from pathlib import Path

# ── Repo + venv path bootstrap (before any backend imports) ──────────

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Force UTF-8 stdout so Chinese prompt/output isn't mojibake'd when
# the script is invoked from Windows PowerShell.
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


# ── Fixed inputs (replace per-run by editing these constants) ─────────

REFERENCE_IMAGE_PATH = REPO_ROOT / "backend" / "data" / "参考图.jpg"

PROVIDERS: list[str] = ["grok", "gemini", "minimax"]
SAMPLES_PER_PROVIDER: int = 2
SIZE: str = "1024x1792"


# ── Argv surface ──────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sample fullbody seed per image-gen provider (direct provider calls, no DB).")
    p.add_argument("--reference-image", type=Path, default=REFERENCE_IMAGE_PATH, help="Path to the reference image.")
    p.add_argument("--providers", type=str, default=",".join(PROVIDERS), help="Comma-separated provider names (default: grok,gemini,minimax).")
    p.add_argument("--samples-per-provider", type=int, default=SAMPLES_PER_PROVIDER, help="Number of images per provider (default: 2).")
    p.add_argument("--output-dir", type=Path, default=None, help="Override output dir; default backend/data/_fullbody_samples/<UTC-timestamp>/<view>/.")
    p.add_argument("--size", default=SIZE, help="Image size as WxH or aspect-ratio token (default: 1024x1792).")
    p.add_argument(
        "--view",
        choices=["front", "right", "back"],
        default="front",
        help="Which view to render (default: front). Multi-view consistency checks typically run --view right / --view back with the same reference image.",
    )
    p.add_argument("--species", default="人类", help="Species fed to the production template resolver (default: 人类).")
    p.add_argument(
        "--style",
        choices=["auto", "anime", "realistic"],
        default="auto",
        help="Seed style branch (default: auto = the backend's species routing).",
    )
    return p.parse_args()


# ── Per-provider config from backend/config.toml (no DB) ──────────────


_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    "grok": {"key": "grok_api_key", "url": "grok_base_url", "default_url": "https://api.x.ai/v1", "model": "grok-imagine-image-quality"},
    "gemini": {"key": "gemini_api_key", "url": "gemini_base_url", "default_url": "https://generativelanguage.googleapis.com", "model": "gemini-3.1-flash-image"},
    "minimax": {"key": "minimax_api_key", "url": "minimax_base_url", "default_url": "https://api.minimaxi.com", "model": "image-01"},
}


def _load_provider_configs(config_path: Path, providers: list[str]) -> dict[str, dict[str, str]]:
    """Return ``{provider_name: {"api_key", "base_url", "model"}}`` for the
    requested subset from ``config.toml`` — no DB, no SETTINGS. Falls back
    to per-provider defaults for base URL / model when not set, and raises
    if an unknown provider name is requested (no silent KeyError mid-run)."""
    cfg = tomllib.loads(config_path.read_text(encoding="utf-8"))
    raw = cfg.get("providers", {}) or {}
    image_cfg = cfg.get("image_gen", {}) or {}
    fallback_model = image_cfg.get("image_gen_model_name") or ""

    out: dict[str, dict[str, str]] = {}
    for p in providers:
        spec = _PROVIDER_DEFAULTS.get(p)
        if spec is None:
            sys.exit(f"未支持的供应商：{p!r}（目前支持 grok / gemini / minimax）")
        out[p] = {
            "api_key": raw.get(spec["key"], ""),
            "base_url": raw.get(spec["url"], "") or spec["default_url"],
            "model": fallback_model or spec["model"],
        }
    return out


# ── Reference image → data URI ───────────────────────────────────────


def _reference_data_uri(path: Path) -> str:
    if not path.is_file():
        sys.exit(f"参考图不存在：{path}（请确认路径与文件名完全一致）")
    import mimetypes

    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


# ── Prompt construction (production resolver, no LLM round-trip) ──────


def _build_fullbody_prompt(view: str, species: str, style: str) -> str:
    """Delegate to the production template resolver so sampled prompts are
    byte-identical to what ``avatar_service`` sends. Imports happen late for
    the same reason as the provider imports below (parseable without the
    backend venv)."""
    from services.llm.prompt_engineer import build_fullbody_prompt, resolve_fullbody_style, resolve_fullbody_template  # type: ignore[import-not-found]

    resolved = resolve_fullbody_style(species) if style == "auto" else style
    template = resolve_fullbody_template(species, style=resolved)
    return build_fullbody_prompt(view, template=template)


# ── Provider instantiation ───────────────────────────────────────────


def _instantiate_provider(provider_name: str, cfg: dict[str, str]):
    """Construct the named image-gen provider with an inline ``ProviderConfig``.

    Imports happen here (not at top) so that the script still parses on
    systems without fastapi etc. installed.
    """
    from services.llm.providers.base import ProviderConfig, ServiceType  # type: ignore[import-not-found]

    config_obj = ProviderConfig(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        model=cfg["model"],
        service_type=ServiceType.image_gen,
        provider_name=provider_name,
    )

    if provider_name == "grok":
        from services.llm.providers.grok.image import GrokImageGenProvider

        return GrokImageGenProvider(config_obj)
    if provider_name == "gemini":
        from services.llm.providers.gemini.image import GeminiImageGenProvider

        return GeminiImageGenProvider(config_obj)
    if provider_name == "minimax":
        from services.llm.providers.minimax.image import MiniMaxImageGenProvider

        return MiniMaxImageGenProvider(config_obj)
    raise ValueError(f"unknown provider: {provider_name}")


# ── Per-provider sample ──────────────────────────────────────────────


async def _sample_one(
    *,
    provider_name: str,
    idx: int,
    view: str,
    output_dir: Path,
    prompt: str,
    ref_uri: str,
    provider_cfg: dict[str, str],
    size: str,
) -> Path:
    stem = f"{provider_name}_{view}_{idx}"
    try:
        from services.llm.providers.base import ImageGenRequest  # type: ignore[import-not-found]

        provider = _instantiate_provider(provider_name, provider_cfg)
        # size is "1024x1792"; xAI ingests aspect_ratio, others accept size.
        # Pass both: the provider classes are supposed to fall through unknown fields.
        req = ImageGenRequest(
            prompt=prompt,
            n=1,
            size=size,
            reference_image=ref_uri,
        )
        result = await provider.generate(req)
        if not result.images:
            err_path = output_dir / f"{stem}.error.txt"
            err_path.write_text(f"provider returned no images; raw={result.raw!r}", encoding="utf-8")
            print(f"  [{provider_name} {view} #{idx}] FAIL: empty images")
            return err_path

        asset = result.images[0]
        png_bytes = base64.b64decode(asset.b64) if asset.b64 else None
        if png_bytes is None:
            err_path = output_dir / f"{stem}.error.txt"
            err_path.write_text(f"asset.b64 empty; raw={result.raw!r}", encoding="utf-8")
            print(f"  [{provider_name} {view} #{idx}] FAIL: empty b64")
            return err_path

        out = output_dir / f"{stem}.png"
        out.write_bytes(png_bytes)
        print(f"  [{provider_name} {view} #{idx}] OK   -> {out}  model={result.model}")
        return out
    except Exception as exc:
        err_path = output_dir / f"{stem}.error.txt"
        err_path.write_text("".join(traceback.format_exception(exc)), encoding="utf-8")
        print(f"  [{provider_name} {view} #{idx}] EXC: {exc.__class__.__name__}: {exc}")
        return err_path


async def main() -> None:
    args = _parse_args()

    config_path = BACKEND_DIR / "config.toml"
    if not config_path.is_file():
        sys.exit(f"未找到 backend/config.toml：{config_path}")

    data_root = BACKEND_DIR / "data"
    if args.output_dir:
        output_dir = args.output_dir
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output_dir = data_root / "_fullbody_samples" / stamp / args.view
    output_dir.mkdir(parents=True, exist_ok=True)

    providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    provider_cfgs = _load_provider_configs(config_path, providers)
    for p in providers:
        if not provider_cfgs[p]["api_key"]:
            print(f"[warn] provider {p!r} has empty api_key — check backend/config.toml")
    ref_uri = _reference_data_uri(args.reference_image)

    print(f"[setup] view: {args.view}")
    print(f"[setup] output_dir: {output_dir}")
    print(f"[setup] reference: {args.reference_image} ({len(ref_uri)} chars data URI)")
    print(f"[setup] providers × samples: {providers} × {args.samples_per_provider} = {len(providers) * args.samples_per_provider} images")

    prompt = _build_fullbody_prompt(args.view, args.species, args.style)
    print(f"[setup] species: {args.species}  style: {args.style}")
    print(f"[setup] fullbody prompt ({len(prompt)} chars):")
    print(prompt)
    print()

    tasks = [
        _sample_one(
            provider_name=p,
            idx=i,
            view=args.view,
            output_dir=output_dir,
            prompt=prompt,
            ref_uri=ref_uri,
            provider_cfg=provider_cfgs[p],
            size=args.size,
        )
        for p in providers
        for i in range(1, args.samples_per_provider + 1)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    failures = sum(1 for r in results if isinstance(r, BaseException))
    written = sum(1 for r in results if isinstance(r, Path) and r.suffix == ".png")
    print()
    print(f"[done] wrote {written} PNGs to {output_dir}")
    if len(results) - written > 0:
        print(f"[done] {len(results) - written} sample(s) failed — see *.error.txt alongside the PNGs")
    if failures:
        print(f"[done] {failures} task(s) raised")


if __name__ == "__main__":
    asyncio.run(main())

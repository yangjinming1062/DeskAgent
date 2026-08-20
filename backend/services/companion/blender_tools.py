import asyncio
import re
import tempfile
import textwrap
from contextlib import nullcontext
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from components import SETTINGS, get_logger
from sqlalchemy.ext.asyncio import AsyncSession

from ..llm import MissingLlmConfigError, provider_from_config, resolve_vision_chain
from ..worker import run_blender
from .model_service import ModelGenerationError

logger = get_logger(__name__)


@dataclass
class BlenderResult:
    success: bool
    glb_bytes: bytes | None = None
    preview_png: bytes | None = None
    stderr: str = ""


@dataclass
class EvaluationResult:
    score: int
    converged: bool
    critique: str


async def _vision_llm_call(db: AsyncSession | None, user_id: int, system_prompt: str, text_instruction: str, image_data_uris: list[str], **create_kwargs: object) -> str:
    """用首个可用 vision provider 直接发起多模态调用。"""
    chain = await resolve_vision_chain(db, user_id)
    if not chain:
        raise ModelGenerationError("没有可用的 vision LLM provider，无法分析图像")

    provider = provider_from_config(chain[0])
    client = provider.raw_client()
    if client is None:
        raise MissingLlmConfigError(f"vision provider '{provider.provider_name}' is not OpenAI-compatible")

    content: list[dict[str, Any]] = [{"type": "text", "text": text_instruction}]
    for uri in image_data_uris:
        content.append({"type": "image_url", "image_url": {"url": uri}})

    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": content}]
    response = await client.chat.completions.create(model=provider.config.model, messages=messages, **create_kwargs)
    return (response.choices[0].message.content or "").strip()


_FENCE_PATTERN = re.compile(r"^```\w*\n", re.MULTILINE)


def _strip_code_fences(text: str) -> str:
    r"""剥离 LLM 输出外层的 ``` 代码围栏。"""
    cleaned = text.strip()
    cleaned = _FENCE_PATTERN.sub("", cleaned, count=1)
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].rstrip()
    return cleaned.strip()


@lru_cache(maxsize=8)
def _read_scaffold(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _merge_scaffold(llm_code: str, scaffold_path: Path, build_marker: str) -> str:
    """把 LLM 生成的函数体替换进脚手架脚本。"""
    return _read_scaffold(scaffold_path).replace(build_marker, textwrap.indent(llm_code.strip(), "    "), 1)


async def run_blender_scaffold(
    scaffold_path: Path, llm_code: str, build_marker: str, payload_args: list[str], *, render_preview: bool = True, script_name: str = "build_script.py", io_dir: Path | None = None
) -> BlenderResult:
    """合并脚手架后执行无头 Blender；传入 io_dir 时复用 worker 挂载进沙箱的作业目录，否则用自建临时目录并在此清理。"""
    if not scaffold_path.exists():
        return BlenderResult(success=False, stderr=f"scaffold not found: {scaffold_path}")

    merged_script = _merge_scaffold(llm_code, scaffold_path, build_marker)

    io_ctx = nullcontext(str(io_dir)) if io_dir is not None else tempfile.TemporaryDirectory()
    with io_ctx as tmp:
        tmp_dir = Path(tmp)
        script_path = tmp_dir / script_name
        glb_path = tmp_dir / "output.glb"
        render_path = tmp_dir / "preview.png"

        script_path.write_text(merged_script, encoding="utf-8")

        payload: list[str] = ["--output", str(glb_path), *payload_args]
        if render_preview:
            payload.extend(["--render-output", str(render_path)])

        try:
            returncode, combined_stderr = await run_blender(tmp_dir, script_name, payload, timeout=SETTINGS.blender_llm_timeout)
        except FileNotFoundError:
            return BlenderResult(success=False, stderr="blender binary (or docker sandbox) not found on PATH")

        if returncode != 0:
            return BlenderResult(success=False, stderr=combined_stderr[-3000:])

        if not glb_path.exists() or glb_path.stat().st_size < 64:
            return BlenderResult(success=False, stderr="Blender produced no valid GLB output")

        if render_preview and render_path.exists():
            glb_bytes, preview_bytes = await asyncio.gather(asyncio.to_thread(glb_path.read_bytes), asyncio.to_thread(render_path.read_bytes))
        else:
            glb_bytes, preview_bytes = (await asyncio.to_thread(glb_path.read_bytes), None)
        return BlenderResult(success=True, glb_bytes=glb_bytes, preview_png=preview_bytes)

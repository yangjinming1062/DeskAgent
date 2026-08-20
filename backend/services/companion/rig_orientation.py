"""本地绑骨用的面朝向检测：无头渲染四个水平视角快照后交由视觉 LLM 判断哪面露脸。几何启发式（头部顶点密度）在长发网格上失效，而人脸识别对视觉模型极其可靠。"""

import asyncio
import base64
import shutil
from pathlib import Path

from components import get_logger

from ..llm import provider_from_config, resolve_vision_chain, strip_think_blocks
from ..worker import run_blender

logger = get_logger(__name__)

# 视图 → 该视图露脸时网格的面朝向相对规范 -Y 前方的偏航角（度）。
_VIEW_YAW: dict[str, float] = {"front": 0.0, "right": 90.0, "back": 180.0, "left": -90.0}
_LETTER_TO_VIEW: dict[str, str] = {"A": "front", "B": "right", "C": "back", "D": "left"}

_PROMPT = "这四张图是同一个3D角色模型的四个视角快照，顺序为 A=front、B=right、C=back、D=left。哪一张能看到角色的正脸（五官：眼睛/鼻子/嘴）？只回答一个字母。"


async def detect_face_yaw(glb_bytes: bytes, *, workdir: Path, user_id: int | None = None, db=None) -> float:
    """在 workdir 内完成渲染与 LLM 往返；任何失败都返回 0.0，让绑骨退化到默认朝向而非中断整个生成。"""
    try:
        return await _detect(glb_bytes, workdir, user_id=user_id, db=db)
    except Exception:
        logger.warning("face yaw detection failed; rigging with yaw=0", exc_info=True)
        return 0.0


async def _detect(glb_bytes: bytes, workdir: Path, *, user_id: int | None, db) -> float:
    views = await _render_views(glb_bytes, workdir)
    chain = await resolve_vision_chain(db, user_id)
    if not chain:
        logger.warning("no vision provider configured; rigging with yaw=0")
        return 0.0
    provider = provider_from_config(chain[0])
    client = provider.raw_client()
    if client is None:
        return 0.0

    content: list = [{"type": "text", "text": _PROMPT}]
    for view in ("front", "right", "back", "left"):
        b64 = base64.b64encode(views[view].read_bytes()).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
    response = await client.chat.completions.create(model=provider.config.model, messages=[{"role": "user", "content": content}])
    answer = strip_think_blocks(response.choices[0].message.content or "").strip().upper()
    for ch in answer:
        if ch in _LETTER_TO_VIEW:
            yaw = _VIEW_YAW[_LETTER_TO_VIEW[ch]]
            logger.info("face yaw detected", extra={"yaw": yaw, "answer": answer[:8]})
            return yaw
    logger.warning("face yaw answer unparseable; rigging with yaw=0", extra={"answer": answer[:40]})
    return 0.0


async def _render_views(glb_bytes: bytes, workdir: Path) -> dict[str, Path]:
    script_path = Path(__file__).parent.parent.parent / "assets" / "animations" / "render_face_views.py"
    if not script_path.exists():
        raise FileNotFoundError("render_face_views.py 脚本缺失，无法检测面朝向")
    inp = workdir / "face_input.glb"
    await asyncio.to_thread(inp.write_bytes, glb_bytes)
    await asyncio.to_thread(shutil.copyfile, script_path, workdir / "render_face_views.py")
    returncode, stderr = await run_blender(workdir, "render_face_views.py", ["--input", str(inp), "--outdir", str(workdir)], timeout=300)
    if returncode != 0:
        raise RuntimeError(f"视角渲染失败: {stderr[-300:]}")
    views = {view: workdir / f"{view}.png" for view in _VIEW_YAW}
    missing = [str(p) for p in views.values() if not p.exists()]
    if missing:
        raise RuntimeError(f"视角渲染缺图: {missing}")
    return views

import asyncio
import re
import shutil
import tempfile
import textwrap
from contextlib import nullcontext
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from components import SESSION_LOCAL, SETTINGS, get_logger, parse_llm_json
from sqlalchemy.ext.asyncio import AsyncSession

from ..llm import chat, provider_from_config, resolve_vision_chain
from ..llm.llm_client import MissingLlmConfigError
from ..worker import run_blender
from .asset_store import build_data_uri, save_companion_model
from .avatar_service import resolve_uploaded_avatar_path
from .model_service import (
    ModelGenerationError,
    _emit_model_failed,
    _emit_model_ready,
    _emit_progress,
    _extract_morph_names_from_glb,
    _finalize_generation,
    _inject_morph_targets,
    _mark_generation_failed,
    _rig_naming_for,
    parse_glb_json,
)
from .rig_bone_specs import bone_names, format_bone_tree
from .rig_type_selector import select_rig_type

logger = get_logger(__name__)

_SCAFFOLD_PATH = Path(__file__).parent.parent.parent / "assets" / "animations" / "llm_bpy_scaffold.py"
_BUILD_MARKER = "    __BUILD_BODY__"


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


_CODE_GEN_SYSTEM_PROMPT = """\
你是一位精通 Blender Python API (bpy) 的 3D 角色建模师。你的任务是根据提供的三视图参考图，\
编写 bpy 代码来创建一个完整的、可绑骨的 3D 角色模型。

## 你的代码运行环境
- Blender 以 headless 模式运行（`blender --background`），版本 5.2
- 场景已通过 `bpy.ops.wm.read_factory_settings(use_empty=True)` 重置
- 你的代码将被注入到 scaffold 的 _build_body(ctx) 函数中
- scaffold 负责 GLB 导出和可选渲染，你只需创建网格、骨骼、材质
- 无 GPU——不要使用 EEVEE
- 无外部文件依赖——所有几何体和材质必须在代码中生成
- ctx 是一个 dict，包含 seed_front / seed_right / seed_back（图片路径，可选用于 UV 投影）和 params

## 必须创建的骨骼层级（{rig_type}，{rig_naming} spec）
{bone_tree}

骨骼必须用以上精确名称创建（大小写敏感），否则动画系统无法驱动。
骨骼位置应构成 T-pose（双足）/ 自然站姿（其他物种），Y 轴朝上，模型面朝 -Z。

## bpy API 关键参考
```python
# 网格创建
bpy.ops.mesh.primitive_uv_sphere_add(radius=1, location=(0,0,0))
bpy.ops.mesh.primitive_cylinder_add(radius=1, depth=2, location=(0,0,0))
bpy.ops.mesh.primitive_cube_add(size=1, location=(0,0,0))

# 修改器
obj.modifiers.new(name="Subsurf", type='SUBSURF').levels = 2
obj.modifiers.new(name="Mirror", type='MIRROR').use_axis[0] = True

# 合并网格
bpy.ops.object.select_all(action='DESELECT')
for obj in parts: obj.select_set(True)
bpy.context.view_layer.objects.active = parts[0]
bpy.ops.object.join()

# 骨骼创建
arm_data = bpy.data.armatures.new("Armature")
arm_obj = bpy.data.objects.new("Armature", arm_data)
bpy.context.scene.collection.objects.link(arm_obj)
bpy.context.view_layer.objects.active = arm_obj
bpy.ops.object.mode_set(mode='EDIT')
bone = arm_data.edit_bones.new("Hips")
bone.head = (0, 0.9, 0); bone.tail = (0, 1.0, 0)
# 设置父子关系: child_bone.parent = parent_bone
bpy.ops.object.mode_set(mode='OBJECT')

# 自动权重蒙皮
bpy.ops.object.select_all(action='DESELECT')
mesh_obj.select_set(True); arm_obj.select_set(True)
bpy.context.view_layer.objects.active = arm_obj
bpy.ops.object.parent_set(type='ARMATURE_AUTO')

# 材质（Principled BSDF）
mat = bpy.data.materials.new("Skin")
mat.use_nodes = True
bsdf = mat.node_tree.nodes["Principled BSDF"]
bsdf.inputs["Base Color"].default_value = (0.83, 0.65, 0.46, 1)  # RGBA 0-1
bsdf.inputs["Roughness"].default_value = 0.55
mesh_obj.data.materials.append(mat)
```

## 材质要求
- 至少创建 "Skin"（roughness 0.55）和 "Eyes"（roughness 0.12）材质
- 可选创建 "Hair"、"Clothing" 等

## 输出要求
- 坐标系：Y 轴朝上，模型面朝 -Z（glTF 标准）
- 初始姿态：biped=T-pose，其他=N-pose（自然站姿）
- 所有网格已蒙皮到 armature

## 分析参考图
仔细观察三视图（正面、侧面、背面），提取：
- 体型轮廓（高矮胖瘦、肢体比例）
- 肤色、发色、瞳色（用 RGB 0-1 值表示）
- 发型与体积
- 标志性物种特征（耳朵、尾巴、翅膀、鳍等）
- 服装基本色块

然后编写代码创建匹配的角色。不要被 A-pose 姿势干扰——专注于外观特征。

直接输出 Python 代码（_build_body 函数体内容），不要 markdown 围栏，不要解释。\
代码中可以直接使用 bpy 和 ctx 变量。\
"""

_FIX_SYSTEM_PROMPT = """\
你之前生成的 Blender 脚本执行失败。请修复脚本。

错误信息：
{stderr}

你的上一个脚本：
{prev_script}

请修复导致错误的部分。可能的原因：
- bpy API 版本差异（Blender 5.2）
- 属性名错误
- 操作前需要正确的 selection / active / mode 状态
- Principled BSDF 某些 input 名称在 5.2 中不同

保留整体结构，只修改导致错误的部分。
输出修复后的完整 Python 代码（_build_body 函数体内容），不要 markdown 围栏。\
"""

_REFINE_SYSTEM_PROMPT = """\
你之前生成的模型已成功导出。请比较渲染预览图与原始三视图参考图，\
找出可以通过代码调整改善的差异。

你的评估意见：
{critique}

你之前的代码：
{prev_script}

请修改代码以改善匹配度。常见调整：
- 体型比例（骨骼长度、肢体粗细）
- 颜色微调（skin/hair/eye/clothing）
- 头部大小、眼睛大小
- 发型体积和长度
- 物种特征的大小/位置

注意：
- 不要改变骨骼名称或层级
- 不要改变坐标系或导出设置
- 保留已有代码中正确的部分

输出修改后的完整 Python 代码（_build_body 函数体内容），不要 markdown 围栏。\
"""

_EVAL_SYSTEM_PROMPT = """\
比较渲染预览图（最后一张）与原始三视图参考图（前三张）。\
评估 3D 模型与参考角色的匹配度。

输出 JSON：
{{"score": 0-10, "converged": true/false, "critique": "具体的改进建议，用中文"}}

判断标准：
- 7 分以上通常可以收敛
- 主要看：体型轮廓、颜色、发型、物种特征
- 忽略 A-pose vs T-pose 的姿势差异
- 忽略纹理细节（当前模型使用纯色材质）\
"""


def _resolve_seeds(view_filenames: dict[str, str], io_dir: Path | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve the three seed images once. Single pass halves disk lookups vs
    resolving data-URIs and file-paths independently. With ``io_dir`` (worker
    host), seeds are copied into the per-job workspace so the sandbox
    container only ever sees the mounted io dir, never the data_dir layout."""
    uris: dict[str, str] = {}
    paths: dict[str, str] = {}
    for view_key in ("front", "right", "back"):
        filename = view_filenames[view_key]
        resolved = resolve_uploaded_avatar_path(filename)
        if resolved is None:
            raise ModelGenerationError(f"{view_key} 视角种子图文件不可读: {filename}")
        path, content_type = resolved
        uris[view_key] = build_data_uri(path.read_bytes(), content_type)
        if io_dir is not None:
            dest = io_dir / f"seed_{view_key}{path.suffix}"
            shutil.copyfile(path, dest)
            paths[view_key] = str(dest)
        else:
            paths[view_key] = str(path)
    return uris, paths


async def _vision_llm_call(db: AsyncSession | None, user_id: int, system_prompt: str, text_instruction: str, image_data_uris: list[str], **create_kwargs: object) -> str:
    """Direct multimodal LLM call using the first resolved vision provider."""
    chain = await resolve_vision_chain(db, user_id)
    if not chain:
        raise ModelGenerationError("没有可用的 vision LLM provider，无法分析种子图")

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
    r"""Remove ```language ... ``` wrappers if present."""
    cleaned = text.strip()
    cleaned = _FENCE_PATTERN.sub("", cleaned, count=1)
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].rstrip()
    return cleaned.strip()


async def _llm_generate_script(seed_uris: dict[str, str], rig_type: str, bone_tree: str, rig_naming: str, species: str, user_id: int, db: AsyncSession | None = None) -> str:
    system = _CODE_GEN_SYSTEM_PROMPT.format(rig_type=rig_type, rig_naming=rig_naming, bone_tree=bone_tree)
    instruction = f"物种：{species}\n骨骼类型：{rig_type}\n\n请分析以上三视图角色图，输出 bpy 建模代码。"
    images = [seed_uris["front"], seed_uris["right"], seed_uris["back"]]
    raw = await _vision_llm_call(db, user_id, system, instruction, images)
    return _strip_code_fences(raw)


async def _llm_fix_script(prev_script: str, stderr: str, seed_uris: dict[str, str], user_id: int, db: AsyncSession | None = None) -> str:
    system = _FIX_SYSTEM_PROMPT.format(stderr=stderr[:2000], prev_script=prev_script)
    instruction = "请修复脚本并输出完整代码。"
    images = [seed_uris["front"], seed_uris["right"], seed_uris["back"]]
    raw = await _vision_llm_call(db, user_id, system, instruction, images)
    return _strip_code_fences(raw)


async def _llm_refine_script(prev_script: str, preview_uri: str, critique: str, seed_uris: dict[str, str], user_id: int, db: AsyncSession | None = None) -> str:
    system = _REFINE_SYSTEM_PROMPT.format(critique=critique, prev_script=prev_script)
    instruction = "请根据评估意见改进脚本，输出完整代码。"
    images = [seed_uris["front"], seed_uris["right"], seed_uris["back"], preview_uri]
    raw = await _vision_llm_call(db, user_id, system, instruction, images)
    return _strip_code_fences(raw)


async def _llm_evaluate(preview_uri: str, seed_uris: dict[str, str], user_id: int, db: AsyncSession | None = None) -> EvaluationResult:
    instruction = "参考图（前 3 张）与渲染预览图（最后 1 张）对比，输出评估 JSON。"
    images = [seed_uris["front"], seed_uris["right"], seed_uris["back"], preview_uri]
    raw = await _vision_llm_call(db, user_id, _EVAL_SYSTEM_PROMPT, instruction, images)
    parsed = parse_llm_json(raw) or {}
    return EvaluationResult(
        score=int(parsed.get("score", 0)) if isinstance(parsed, dict) else 0,
        converged=bool(parsed.get("converged", False)) if isinstance(parsed, dict) else False,
        critique=str(parsed.get("critique", "")) if isinstance(parsed, dict) else "",
    )


@lru_cache(maxsize=8)
def _read_scaffold(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _merge_scaffold(llm_code: str, scaffold_path: Path | None = None, build_marker: str | None = None) -> str:
    """Substitute LLM function body into scaffold."""
    path = scaffold_path or _SCAFFOLD_PATH
    return _read_scaffold(path).replace(build_marker or _BUILD_MARKER, textwrap.indent(llm_code.strip(), "    "), 1)


async def run_blender_scaffold(
    scaffold_path: Path, llm_code: str, build_marker: str, payload_args: list[str], *, render_preview: bool = True, script_name: str = "build_script.py", io_dir: Path | None = None
) -> BlenderResult:
    """Merge LLM code into scaffold and execute headless Blender. ``io_dir``
    lets the worker host everything (script, seeds, outputs) in the per-job
    directory it mounts into the sandbox container; the default private
    tempdir is cleaned up here instead."""
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
            glb_bytes, preview_bytes = await asyncio.to_thread(glb_path.read_bytes), None
        return BlenderResult(success=True, glb_bytes=glb_bytes, preview_png=preview_bytes)


async def _execute_blender_script(llm_code: str, seed_paths: dict[str, str], *, render_preview: bool = True, io_dir: Path | None = None) -> BlenderResult:
    payload = ["--seed-front", seed_paths.get("front", ""), "--seed-right", seed_paths.get("right", ""), "--seed-back", seed_paths.get("back", "")]
    return await run_blender_scaffold(_SCAFFOLD_PATH, llm_code, _BUILD_MARKER, payload, render_preview=render_preview, script_name="build_character.py", io_dir=io_dir)


def _validate_glb(glb_bytes: bytes, required_bones: set[str]) -> list[str]:
    """Return missing-required-bone names (empty = all present)."""
    gltf = parse_glb_json(glb_bytes)
    if gltf is None:
        return ["GLB JSON chunk unparseable"]

    # Collect all node names that are joints (referenced by skins).
    skin_joints: set[int] = set()
    for skin in gltf.get("skins", []):
        skin_joints.update(skin.get("joints", []))

    node_names: set[str] = set()
    for idx, node in enumerate(gltf.get("nodes", [])):
        name = node.get("name", "")
        if name and (not skin_joints or idx in skin_joints):
            node_names.add(name)

    missing = sorted(required_bones - node_names)
    return missing


async def run_blender_llm_pipeline(user_id: int, view_filenames: dict[str, str], species: str, model_id: int, *, io_dir: Path | None = None) -> None:
    try:
        await _emit_progress(user_id, "analyzing", 5, provider="blender_llm")
        rig_type = await select_rig_type(chat, species, user_id=user_id)
        rig_naming = _rig_naming_for(rig_type)
        bone_tree = format_bone_tree(rig_type)
        required_bones = bone_names(rig_type)

        seed_uris, seed_paths = await asyncio.to_thread(_resolve_seeds, view_filenames, io_dir)

        best_glb: bytes | None = None
        prev_script: str | None = None
        last_error: str | None = None
        last_critique: str = ""
        last_preview_uri: str | None = None
        max_iters = SETTINGS.blender_llm_max_iterations

        for i in range(max_iters):
            await _emit_progress(user_id, "generating", 5 + int(75 * i / max(1, max_iters)), provider="blender_llm")
            logger.info("Blender+LLM iteration %d/%d", i + 1, max_iters, extra={"user_id": user_id})

            async with SESSION_LOCAL() as db:
                if i == 0:
                    script = await _llm_generate_script(seed_uris, rig_type, bone_tree, rig_naming, species, user_id, db)
                elif last_error:
                    script = await _llm_fix_script(prev_script, last_error, seed_uris, user_id, db)
                else:
                    script = await _llm_refine_script(prev_script, last_preview_uri or "", last_critique, seed_uris, user_id, db)

            result = await _execute_blender_script(script, seed_paths, render_preview=True, io_dir=io_dir)

            if not result.success:
                logger.warning("Blender execution failed (iter %d): %s", i + 1, result.stderr[:200], extra={"user_id": user_id})
                last_error = result.stderr
                prev_script = script
                continue

            last_error = None

            issues = _validate_glb(result.glb_bytes, required_bones)
            if issues:
                missing_list = ", ".join(issues[:10])
                logger.warning("GLB validation failed (iter %d): missing bones: %s", i + 1, missing_list, extra={"user_id": user_id})
                last_error = f"GLB validation failed — missing bones: {missing_list}"
                prev_script = script
                continue

            # "Never get worse": only overwrite best_glb once validation passes.
            best_glb = result.glb_bytes

            if result.preview_png is None:
                logger.info("No preview render; accepting GLB without visual refinement", extra={"user_id": user_id})
                break

            last_preview_uri = build_data_uri(result.preview_png, "image/png")

            async with SESSION_LOCAL() as db:
                evaluation = await _llm_evaluate(last_preview_uri, seed_uris, user_id, db)

            logger.info("LLM evaluation (iter %d): score=%d converged=%s", i + 1, evaluation.score, evaluation.converged, extra={"user_id": user_id})

            if evaluation.converged or i == max_iters - 1:
                break

            last_critique = evaluation.critique
            prev_script = script

        if best_glb is None:
            raise ModelGenerationError("Blender+LLM 管线在所有迭代中均未能生成有效模型")

        await _emit_progress(user_id, "injecting_morphs", 85, provider="blender_llm")
        rig_original_url = save_companion_model(best_glb, user_id=user_id)
        final_glb = await _inject_morph_targets(best_glb, io_dir=io_dir)

        await _emit_progress(user_id, "finalizing", 95, provider="blender_llm")
        asset_url = save_companion_model(final_glb, user_id=user_id)
        morph_names = _extract_morph_names_from_glb(final_glb)

        activated = await _finalize_generation(
            model_id, user_id, asset_url=asset_url, rig_original_url=rig_original_url, provider="blender_llm", species=species, rig_type=rig_type, morph_names=morph_names
        )

        if not activated:
            logger.info("Blender+LLM generation superseded by a newer run; asset saved without activating", extra={"user_id": user_id, "model_id": model_id})
            return

        await _emit_model_ready(user_id, model_id, asset_url, species=species, rig_type=rig_type)
        await _emit_progress(user_id, "done", 100, provider="blender_llm")
        logger.info("Blender+LLM generation succeeded", extra={"user_id": user_id, "species": species, "rig_type": rig_type, "morph_count": len(morph_names)})

    except Exception:
        logger.warning("Blender+LLM generation failed", extra={"user_id": user_id}, exc_info=True)
        # model.failed reaches the client — fixed copy only (PROTOCOL §1.2).
        await _emit_model_failed(user_id, "3D 模型生成失败，请稍后重试")
        await _mark_generation_failed(model_id, "3D 模型生成失败，请稍后重试")

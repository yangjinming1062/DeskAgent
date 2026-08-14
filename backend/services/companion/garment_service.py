import asyncio
import tempfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from components import SESSION_LOCAL, SETTINGS, get_logger, parse_llm_json
from sqlalchemy.orm import Session

from .asset_store import build_data_uri
from .blender_llm_pipeline import EvaluationResult, _strip_code_fences, _vision_llm_call, run_blender_scaffold
from .model_service import parse_glb_json
from .rig_bone_specs import format_bone_tree

logger = get_logger(__name__)

_GARMENT_SCAFFOLD_PATH = Path(__file__).parent.parent.parent / "assets" / "animations" / "garment_bpy_scaffold.py"
_GARMENT_BUILD_MARKER = "    __BUILD_GARMENT__"

MAX_ITERATIONS_FALLBACK = 6


# ─── System prompts ──────────────────────────────────────────────

_CODE_GEN_SYSTEM_PROMPT = """\
你是一位精通 Blender Python API (bpy) 的 3D 服装建模师。你的任务是根据服装描述和参考图，\
在已有的角色身体上创建服装几何体。

## 你的代码运行环境
- Blender 以 headless 模式运行（`blender --background`），版本约 3.3
- 场景已重置，身体 GLB 已导入（armature + body mesh）
- 你的代码将被注入到 scaffold 的 `_build_garment(ctx)` 函数中
- scaffold 负责确定性后处理（贴合/加厚/蒙皮/防穿模）和 GLB 导出
- 你只需创建服装 mesh，**不要**修改身体、不要创建/修改 armature

## ctx 结构
```python
ctx = {
    "body": {"armature": <body armature bpy 对象>, "mesh": <body mesh bpy 对象>},
    "bones": {
        "<bone_name>": {"head": (x,y,z), "tail": (x,y,z), "length": float},
        ...
    },
    "body_bounds": {"min": (x,y,z), "max": (x,y,z)},
    "params": {}
}
```
骨骼名一律从 `ctx["bones"]` 取，不硬编码。

## 硬性契约
1. 只创建服装 mesh（MESH 对象），**禁止**创建/修改 armature、禁止删除身体对象
2. 每个服装 mesh 必须建 `VG_ANCHOR` 顶点组，放入必须贴合身体的顶点（腰口/领口/袖口/肩线）
3. 轮廓决定区域（裙摆、衣身下摆、褶皱）**不要**放进 `VG_ANCHOR`（保持毛坯形状）
4. 网格是单层壳即可（后处理会加厚度），顶点密度不足时允许加 subsurf
5. 不要用 solidify（后处理统一做）

## bpy API 关键参考
```python
import bmesh, bpy
from mathutils import Vector

# 创建服装 mesh
mesh = bpy.data.meshes.new("Garment")
obj = bpy.data.objects.new("Garment", mesh)
bpy.context.scene.collection.objects.link(obj)

# 用 bmesh 构建几何体
bm = bmesh.new()
bmesh.ops.create_cone(bm, cap_ends=True, segments=32, radius1=0.15, radius2=0.25, depth=0.5)
# ... 自由构建 ...
bm.to_mesh(mesh)
bm.free()

# 从骨骼获取位置参考
spine2_head = ctx["bones"]["<脊柱骨骼名>"]["head"]  # 如 mixamorig:Spine2
hips_head = ctx["bones"]["<臀部骨骼名>"]["head"]

# 移动 mesh 到正确位置
obj.location = spine2_head

# 添加 VG_ANCHOR 顶点组（必须贴合身体的顶点）
vg = obj.vertex_groups.new(name="VG_ANCHOR")
vg.add([0, 1, 2, 3, ...], 1.0, 'REPLACE')  # 腰口/领口顶点索引

# 添加材质
mat = bpy.data.materials.new("GarmentMat")
mat.use_nodes = True
bsdf = mat.node_tree.nodes["Principled BSDF"]
bsdf.inputs["Base Color"].default_value = (0.2, 0.3, 0.8, 1)
obj.data.materials.append(mat)
```

## 设计指南
- 根据服装描述决定覆盖区域和轮廓（裙子的蓬度、外套的长度等）
- 参考骨骼位置定位服装（腰、肩、臀等）
- 服装应比身体略大（留出料厚空间）
- 锚点（腰口/领口）要贴合身体，自由区域（裙摆/下摆）保持轮廓

直接输出 Python 代码（_build_garment 函数体内容），不要 markdown 围栏，不要解释。\
代码中可以直接使用 bpy 和 ctx 变量。\
"""

_ACCESSORY_CODE_GEN_SYSTEM_PROMPT = """\
你是一位精通 Blender Python API (bpy) 的 3D 挂件建模师。你的任务是根据挂件描述和参考图，\
在角色身体上创建一个挂件 mesh（包、帽、眼镜、围巾、翅膀等硬质附件）。

## 你的代码运行环境
- Blender 以 headless 模式运行（`blender --background`），版本约 3.3
- 场景已重置，身体 GLB 已导入（armature + body mesh，供你读取位置参考）
- 你的代码将被注入到 scaffold 的 `_build_garment(ctx)` 函数中
- scaffold 负责导出；挂件**不需要**蒙皮/贴合/加厚等后处理
- 你只需创建挂件 mesh，**不要**修改身体、不要创建/修改 armature

## ctx 结构
```python
ctx = {
    "body": {"armature": <body armature bpy 对象>, "mesh": <body mesh bpy 对象>},
    "bones": { "<bone_name>": {"head": (x,y,z), "tail": (x,y,z), "length": float}, ... },
    "body_bounds": {"min": (x,y,z), "max": (x,y,z)},
    "params": {"socket": "<挂点骨骼名>"}
}
```

## 硬性契约
1. 只创建挂件 mesh（MESH 对象），**禁止**创建/修改 armature、禁止删除身体对象
2. 挂件的**佩戴点**必须对准挂点骨骼 `ctx["params"]["socket"]` 的位置：
   - 手提包/手持物 → 挂件的手柄/抓握部位在骨骼位置
   - 帽子 → 帽子内底中心在头顶（骨骼位置上方约骨骼 length 处）
   - 眼镜 → 镜片中心在骨骼位置前方（角色面朝 -Y，即 y 坐标减小的方向）
   - 背包 → 背包贴身面在骨骼位置后方（+Y）
3. 挂件是**实体**网格（有厚度、闭合），不需要 VG_ANCHOR 顶点组
4. 网格在世界坐标系原点附近构建后，通过设置 `obj.location` / 顶点偏移放到挂点位置

## bpy API 关键参考
```python
import bmesh, bpy
from mathutils import Vector

mesh = bpy.data.meshes.new("Accessory")
obj = bpy.data.objects.new("Accessory", mesh)
bpy.context.scene.collection.objects.link(obj)

bm = bmesh.new()
# ... 用 create_cube / create_uv_sphere / extrude 等构建实体挂件 ...
bm.to_mesh(mesh)
bm.free()

# 挂点骨骼位置（挂件的佩戴参考点）
socket = ctx["params"]["socket"]
bone = ctx["bones"][socket]
obj.location = bone["head"]  # 或按佩戴类型偏移

mat = bpy.data.materials.new("AccessoryMat")
mat.use_nodes = True
obj.data.materials.append(mat)
```

## 设计指南
- 挂件尺寸参考 ctx["body_bounds"]（例如背包高度约为体高的 25–35%）
- 实体感：倒角/厚度让挂件不显薄片
- 位置宁近勿远——挂件必须明显挂在身体上而不是悬浮

直接输出 Python 代码（_build_garment 函数体内容），不要 markdown 围栏，不要解释。\
代码中可以直接使用 bpy 和 ctx 变量。\
"""

_FIX_SYSTEM_PROMPT = """\
你之前生成的服装 Blender 脚本执行失败。请修复脚本。

错误信息：
{stderr}

你上一个脚本：
{prev_script}

请修复导致错误的部分。可能的原因：
- bpy API 版本差异（Blender ~3.3）
- 属性名错误
- bmesh 操作前需要正确的模式/选择状态
- VG_ANCHOR 顶点组的 add 调用参数错误

保留整体结构，只修改导致错误的部分。
{anchor_hint}
输出修复后的完整 Python 代码（_build_garment 函数体内容），不要 markdown 围栏。\
"""

_REFINE_SYSTEM_PROMPT = """\
你之前生成的服装已成功导出。请比较渲染预览图（身体+服装合成）与参考图，\
找出可以通过代码调整改善的差异。

你的评估意见：
{critique}

你之前的代码：
{prev_script}

约束：
- 只能改几何/颜色/轮廓
- 不得改身体、不得改骨骼
- {anchor_hint}
- 骨骼名从 ctx["bones"] 取

输出修改后的完整 Python 代码（_build_garment 函数体内容），不要 markdown 围栏。\
"""

_EVAL_SYSTEM_PROMPT = """\
比较渲染预览图（身体+服装合成，最后一张）与服装参考图/描述。\
评估服装的匹配度和质量。

输出 JSON：
{{"score": 0-10, "converged": true/false, "critique": "具体的改进建议，用中文"}}

判断标准：
- 7 分以上通常可以收敛
- 主要看：服装轮廓（蓬度/长度/覆盖区域）、位置（是否在正确位置）、贴合度
- 忽略纹理细节（后处理会生成 PBR 贴图）\
"""


# The fix/refine loops are shared by garment and accessory iterations; only the
# anchor contract differs (garments need VG_ANCHOR, accessories must stay solid).
_ANCHOR_HINTS = {"garment": "确保 VG_ANCHOR 顶点组存在且非空。", "accessory": "挂件保持实体网格，佩戴点对准挂点骨骼。"}


# ─── LLM call wrappers ───────────────────────────────────────────


async def _code_call(system: str, instruction: str, images: list[str], user_id: int, db: Session | None) -> str:
    raw = await _vision_llm_call(db, user_id, system, instruction, images)
    return _strip_code_fences(raw)


async def _llm_generate_garment_script(
    body_preview_uri: str,
    reference_uris: list[str],
    rig_type: str,
    ctx_info: str,
    description: str,
    user_id: int,
    db: Session | None = None,
    *,
    kind: str = "garment",
    socket: str | None = None,
) -> str:
    if kind == "accessory":
        system = _ACCESSORY_CODE_GEN_SYSTEM_PROMPT
        instruction = f"挂件描述：{description}\n\n挂点骨骼：{socket}\n\nctx 中的骨骼数据：\n{ctx_info}\n\n请分析身体参考图和挂件描述，输出 bpy 挂件建模代码。"
    else:
        system = _CODE_GEN_SYSTEM_PROMPT
        bone_tree = format_bone_tree(rig_type)
        instruction = f"服装描述：{description}\n\n骨骼树（{rig_type}）：\n{bone_tree}\n\nctx 中的骨骼数据：\n{ctx_info}\n\n请分析身体参考图和服装描述，输出 bpy 服装建模代码。"
    return await _code_call(system, instruction, [body_preview_uri] + reference_uris, user_id, db)


async def _llm_fix_garment_script(
    prev_script: str, stderr: str, body_preview_uri: str, reference_uris: list[str], user_id: int, db: Session | None = None, *, kind: str = "garment"
) -> str:
    system = _FIX_SYSTEM_PROMPT.format(stderr=stderr[:2000], prev_script=prev_script, anchor_hint=_ANCHOR_HINTS.get(kind, ""))
    return await _code_call(system, "请修复脚本并输出完整代码。", [body_preview_uri] + reference_uris, user_id, db)


async def _llm_refine_garment_script(
    prev_script: str, preview_uri: str, critique: str, body_preview_uri: str, reference_uris: list[str], user_id: int, db: Session | None = None, *, kind: str = "garment"
) -> str:
    system = _REFINE_SYSTEM_PROMPT.format(critique=critique, prev_script=prev_script, anchor_hint=_ANCHOR_HINTS.get(kind, ""))
    return await _code_call(system, "请根据评估意见改进脚本，输出完整代码。", [body_preview_uri] + reference_uris + [preview_uri], user_id, db)


async def _llm_evaluate_garment(preview_uri: str, body_preview_uri: str, reference_uris: list[str], description: str, user_id: int, db: Session | None = None) -> EvaluationResult:
    instruction = f"服装描述：{description}\n\n参考图/身体参考图与渲染预览图对比，输出评估 JSON。"
    images = [body_preview_uri] + reference_uris + [preview_uri]
    raw = await _vision_llm_call(db, user_id, _EVAL_SYSTEM_PROMPT, instruction, images)
    parsed = parse_llm_json(raw) or {}
    if not isinstance(parsed, dict):
        parsed = {}
    return EvaluationResult(score=int(parsed.get("score", 0)), converged=bool(parsed.get("converged", False)), critique=str(parsed.get("critique", "")))


# ─── GLB validation ──────────────────────────────────────────────


def joint_names_from_gltf(gltf: dict[str, Any]) -> list[str]:
    names: list[str] = []
    nodes = gltf.get("nodes") or []
    for skin in gltf.get("skins") or []:
        for node_idx in skin.get("joints") or []:
            if 0 <= node_idx < len(nodes):
                node = nodes[node_idx]
                if isinstance(node, dict):
                    name = node.get("name", "")
                    if name:
                        names.append(name)
    return names


def _extract_joint_names(glb_bytes: bytes) -> list[str]:
    gltf = parse_glb_json(glb_bytes)
    return joint_names_from_gltf(gltf) if gltf else []


def _validate_garment_glb(glb_bytes: bytes, body_joint_names: list[str], *, kind: str = "garment") -> list[str]:
    """Validate garment joint consistency with body skeleton or accessory parseability."""
    if kind == "accessory":
        return [] if parse_glb_json(glb_bytes) is not None else ["accessory GLB unparseable"]
    garment_joints = _extract_joint_names(glb_bytes)
    if not garment_joints:
        return ["garment GLB has no skin/joints"]
    if garment_joints != body_joint_names:
        return [f"joint mismatch: garment has {len(garment_joints)} joints, body has {len(body_joint_names)}"]
    return []


# ─── Main pipeline ───────────────────────────────────────────────


async def run_garment_pipeline(
    *,
    description: str,
    body_glb_bytes: bytes,
    body_preview_uri: str,
    reference_uris: list[str] | None = None,
    rig_type: str = "biped",
    kind: str = "garment",
    socket: str | None = None,
    assembly_json: str,
    body_joint_names: list[str] | None = None,
    user_id: int,
    db: Session | None = None,
) -> bytes:
    """Iterate LLM-Blender-evaluation loop until convergence; returns best garment GLB bytes."""
    reference_uris = reference_uris or []
    body_joint_names = _extract_joint_names(body_glb_bytes) if body_joint_names is None else body_joint_names
    ctx_bones_info = _build_ctx_bones_info(body_joint_names)

    max_iters = getattr(SETTINGS, "blender_llm_max_iterations", MAX_ITERATIONS_FALLBACK) or MAX_ITERATIONS_FALLBACK

    best_glb: bytes | None = None
    prev_script: str | None = None
    last_error: str | None = None
    last_critique = ""
    last_preview_uri: str | None = None

    with tempfile.TemporaryDirectory() as tmp:
        body_glb_path = Path(tmp) / "body.glb"
        await asyncio.to_thread(body_glb_path.write_bytes, body_glb_bytes)

        for i in range(max_iters):
            logger.info("Geometric pipeline iteration %d/%d (%s)", i + 1, max_iters, kind, extra={"user_id": user_id})

            with nullcontext(db) if db is not None else SESSION_LOCAL() as iter_db:
                if i == 0:
                    script = await _llm_generate_garment_script(body_preview_uri, reference_uris, rig_type, ctx_bones_info, description, user_id, iter_db, kind=kind, socket=socket)
                elif last_error:
                    script = await _llm_fix_garment_script(prev_script or "", last_error, body_preview_uri, reference_uris, user_id, iter_db, kind=kind)
                else:
                    script = await _llm_refine_garment_script(
                        prev_script or "", last_preview_uri or "", last_critique, body_preview_uri, reference_uris, user_id, iter_db, kind=kind
                    )

            result = await run_blender_scaffold(
                _GARMENT_SCAFFOLD_PATH,
                script,
                _GARMENT_BUILD_MARKER,
                ["--body-glb", str(body_glb_path), "--assembly", assembly_json, "--kind", kind, "--socket", socket or ""],
                script_name="build_garment.py",
            )

            if not result.success:
                logger.warning("Blender execution failed (iter %d): %s", i + 1, result.stderr[:200], extra={"user_id": user_id})
                last_error = result.stderr
                prev_script = script
                continue

            last_error = None
            issues = _validate_garment_glb(result.glb_bytes, body_joint_names, kind=kind)
            if issues:
                logger.warning("Garment GLB validation failed (iter %d): %s", i + 1, "; ".join(issues), extra={"user_id": user_id})
                last_error = f"GLB validation: {'; '.join(issues)}"
                prev_script = script
                continue

            best_glb = result.glb_bytes

            if result.preview_png is None:
                logger.info("No garment preview render; accepting GLB without visual refinement", extra={"user_id": user_id})
                break

            last_preview_uri = build_data_uri(result.preview_png, "image/png")

            with nullcontext(db) if db is not None else SESSION_LOCAL() as iter_db:
                evaluation = await _llm_evaluate_garment(last_preview_uri, body_preview_uri, reference_uris, description, user_id, iter_db)

            logger.info("Garment evaluation (iter %d): score=%d converged=%s", i + 1, evaluation.score, evaluation.converged, extra={"user_id": user_id})

            if evaluation.converged or i == max_iters - 1:
                break

            last_critique = evaluation.critique
            prev_script = script

    if best_glb is None:
        raise RuntimeError("Garment pipeline failed to produce a valid GLB after all iterations")

    return best_glb


def _build_ctx_bones_info(joints: list[str]) -> str:
    if not joints:
        return "(bone names unavailable)"
    return "\n".join(f'  "{name}"' for name in joints)

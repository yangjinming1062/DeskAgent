"""关键点 → 骨骼 pivot + skin weights。骨骼拓扑固定，pivot 由姿态估计动态计算。"""

from dataclasses import dataclass, field

from components import get_logger

from .layer_extractor import ExtractedLayer

logger = get_logger(__name__)


# 骨骼拓扑：name → parent name；pivot 与 z_order 在 build_skeleton 时计算。
_BONE_TOPOLOGY: tuple[tuple[str, str | None], ...] = (
    ("root", None),
    ("back_hair", "root"),
    ("body_main", "root"),
    ("neck", "body_main"),
    ("head", "neck"),
    ("eye_L", "head"),
    ("eye_R", "head"),
    ("mouth", "head"),
    ("front_hair", "head"),
    ("shoulder_L", "body_main"),
    ("shoulder_R", "body_main"),
    ("elbow_L", "shoulder_L"),
    ("elbow_R", "shoulder_R"),
    ("wrist_L", "elbow_L"),
    ("wrist_R", "elbow_R"),
    ("hand_L", "wrist_L"),
    ("hand_R", "wrist_R"),
    ("hip_L", "body_main"),
    ("hip_R", "body_main"),
    ("knee_L", "hip_L"),
    ("knee_R", "hip_R"),
    ("ankle_L", "knee_L"),
    ("ankle_R", "knee_R"),
    ("skirt", "body_main"),
    ("bust", "body_main"),
)


# 默认 z_order；同 z_order 内绘制顺序由 manifest.meshes 的 z_order 决定。
_BONE_DEFAULT_Z: dict[str, int] = {
    "back_hair": 0,
    "body_main": 2,
    "neck": 2,
    "head": 2,
    "eye_L": 2,
    "eye_R": 2,
    "mouth": 2,
    "front_hair": 5,
    "shoulder_L": 2,
    "shoulder_R": 2,
    "elbow_L": 4,
    "elbow_R": 4,
    "wrist_L": 4,
    "wrist_R": 4,
    "hand_L": 4,
    "hand_R": 4,
    "hip_L": 1,
    "hip_R": 1,
    "knee_L": 1,
    "knee_R": 1,
    "ankle_L": 1,
    "ankle_R": 1,
    "skirt": 3,
    "bust": 3,
    "root": 0,
}


_KP_TO_BONE: dict[str, str] = {
    "head": "head",
    "neck": "neck",
    "left_shoulder": "shoulder_L",
    "right_shoulder": "shoulder_R",
    "left_elbow": "elbow_L",
    "right_elbow": "elbow_R",
    "left_wrist": "wrist_L",
    "right_wrist": "wrist_R",
    "left_hand": "hand_L",
    "right_hand": "hand_R",
    "left_hip": "hip_L",
    "right_hip": "hip_R",
    "left_knee": "knee_L",
    "right_knee": "knee_R",
    "left_ankle": "ankle_L",
    "right_ankle": "ankle_R",
    "left_eye": "eye_L",
    "right_eye": "eye_R",
    "hair_back_root": "back_hair",
}


# 视觉 LLM 输出关键点带 left_/right_ 前缀，骨骼拓扑用 L/R 后缀；查表时用反向映射。
_BONE_TO_KP: dict[str, str] = {bone: kp for kp, bone in _KP_TO_BONE.items()}


# mesh 层名 → 主绑定骨骼：arm_L/R 部件跨肩-肘-腕-手四骨，声明多骨影响集，
# 客户端按顶点到各骨骼 pivot 的距离分配权重（见 2d 客户端 runtime buildSkinnedMesh）。
_ARM_INFLUENCES: dict[str, list[dict]] = {
    "arm_L": [
        {"bone": "shoulder_L", "weight": 0.45},
        {"bone": "elbow_L", "weight": 0.35},
        {"bone": "wrist_L", "weight": 0.15},
        {"bone": "hand_L", "weight": 0.05},
    ],
    "arm_R": [
        {"bone": "shoulder_R", "weight": 0.45},
        {"bone": "elbow_R", "weight": 0.35},
        {"bone": "wrist_R", "weight": 0.15},
        {"bone": "hand_R", "weight": 0.05},
    ],
}

# 腿部件跨髋-膝-踝三骨；同上按距离分配。
_LEG_INFLUENCES: dict[str, list[dict]] = {
    "leg_L": [
        {"bone": "hip_L", "weight": 0.4},
        {"bone": "knee_L", "weight": 0.35},
        {"bone": "ankle_L", "weight": 0.25},
    ],
    "leg_R": [
        {"bone": "hip_R", "weight": 0.4},
        {"bone": "knee_R", "weight": 0.35},
        {"bone": "ankle_R", "weight": 0.25},
    ],
}


@dataclass
class BoneDef:
    name: str
    parent: str | None
    pivot: tuple[float, float]
    z_order: int


@dataclass
class MeshDef:
    name: str
    texture: str
    geometry_w: float
    geometry_h: float
    z_order: int
    origin: tuple[float, float] = (0.0, 0.0)
    bones_influences: list[dict] = field(default_factory=list)


def _layer_center(layer: ExtractedLayer) -> tuple[float, float]:
    x1, y1, x2, y2 = layer.bbox
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def build_bones(
    kp: dict[str, tuple[float, float]],
    extracted: list[ExtractedLayer],
    *,
    canvas_w: int = 1024,
    canvas_h: int = 1366,
) -> list[BoneDef]:
    """从关键点 + 部件 bbox 计算骨骼 pivot；缺失关键点用部件几何中心兜底。"""
    layer_by_name: dict[str, ExtractedLayer] = {layer.name: layer for layer in extracted}
    fallback_center = (0.5, 0.5)

    def pivot_for(kp_name: str, layer_name: str | None) -> tuple[float, float]:
        if kp_name in kp:
            return kp[kp_name]

        if layer_name and layer_name in layer_by_name:
            return _layer_center(layer_by_name[layer_name])

        return fallback_center

    bones: list[BoneDef] = []

    for name, parent in _BONE_TOPOLOGY:
        if name == "root":
            pivot = (0.5, 50.0 / canvas_h)
        elif name == "skirt":
            pivot = pivot_for("left_hip", "body_main") if "left_hip" in kp else (kp.get("neck", (0.5, 0.5))[0], 0.78)
            skirt_layer = layer_by_name.get("clothing") or layer_by_name.get(
                "body_main",
            )
            if skirt_layer is not None:
                pivot = (
                    (pivot[0] + _layer_center(skirt_layer)[0]) / 2,
                    max(pivot[1], _layer_center(skirt_layer)[1]),
                )
        elif name == "bust":
            anchor = kp.get("neck") or pivot_for("left_shoulder", "body_main")
            anchor2 = kp.get("left_shoulder") or kp.get("right_shoulder") or anchor
            pivot = ((anchor[0] + anchor2[0]) / 2, (anchor[1] + anchor2[1]) / 2 + 0.05)
        elif name == "mouth":
            anchor = kp.get("nose") or kp.get("head") or fallback_center
            pivot = (anchor[0], min(anchor[1] + 0.04, 0.95))
        elif name in {"hand_L", "hand_R"}:
            wrist_kp = kp.get("left_wrist" if name == "hand_L" else "right_wrist")
            hand_kp = kp.get("left_hand" if name == "hand_L" else "right_hand")
            anchor = hand_kp or wrist_kp or fallback_center
            pivot = (anchor[0], min(anchor[1] + 0.05, 0.98)) if hand_kp is None else anchor
        else:
            layer_name = (
                None
                if name
                in {
                    "shoulder_L",
                    "shoulder_R",
                    "elbow_L",
                    "elbow_R",
                    "wrist_L",
                    "wrist_R",
                }
                else name
            )
            kp_name = _BONE_TO_KP.get(name, name)
            pivot = pivot_for(kp_name, layer_name)

        bones.append(
            BoneDef(
                name=name,
                parent=parent,
                pivot=(pivot[0] * canvas_w, pivot[1] * canvas_h),
                z_order=_BONE_DEFAULT_Z.get(name, 0),
            ),
        )

    logger.info("built skeleton bones", extra={"count": len(bones)})
    return bones


def build_meshes(
    extracted: list[ExtractedLayer],
    *,
    canvas_w: int = 1024,
    canvas_h: int = 1366,
) -> list[MeshDef]:
    """每个部件一张 plane mesh；eye / mouth 不单独 mesh，由 head mesh 内部骨骼驱动变形。"""
    meshes: list[MeshDef] = []

    for layer in sorted(extracted, key=lambda x: x.z_order):
        if layer.name in {"eye_L", "eye_R", "mouth"}:
            continue

        w, h = layer.pixel_size
        influences = [{"bone": layer.name, "weight": 1.0}]

        if layer.name == "body_main":
            influences = [
                {"bone": "body_main", "weight": 0.6},
                {"bone": "neck", "weight": 0.2},
                {"bone": "head", "weight": 0.2},
            ]

        if layer.name == "front_hair":
            influences = [
                {"bone": "front_hair", "weight": 0.7},
                {"bone": "head", "weight": 0.3},
            ]

        if layer.name == "back_hair":
            influences = [{"bone": "back_hair", "weight": 1.0}]

        # clothing 是衣物/裙摆层：绑 body_main 跟随躯干，混入 skirt 骨承接裙摆物理
        # 与步态摆动——骨骼表没有 clothing 骨，走单骨回退会把衣物层钉死在 root 上。
        if layer.name == "clothing":
            influences = [
                {"bone": "body_main", "weight": 0.6},
                {"bone": "skirt", "weight": 0.4},
            ]

        if layer.name in _ARM_INFLUENCES:
            influences = _ARM_INFLUENCES[layer.name]

        if layer.name in _LEG_INFLUENCES:
            influences = _LEG_INFLUENCES[layer.name]

        cx = (layer.bbox[0] + layer.bbox[2]) / 2 * canvas_w
        cy = (layer.bbox[1] + layer.bbox[3]) / 2 * canvas_h

        meshes.append(
            MeshDef(
                name=f"{layer.name}_mesh",
                texture=f"{layer.name}.png",
                geometry_w=float(w),
                geometry_h=float(h),
                z_order=layer.z_order,
                origin=(cx, cy),
                bones_influences=influences,
            ),
        )

    return meshes

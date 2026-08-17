from dataclasses import dataclass

from .rig_bone_specs import get_bone_hierarchy

Vec3 = tuple[float, float, float]


@dataclass(frozen=True)
class LayoutBone:
    parent: str | None
    head: Vec3
    tail: Vec3


def _mirror(v: Vec3) -> Vec3:
    return (-v[0], v[1], v[2])


def _build_biped(place, chain, out) -> None:
    chain(["Hips", "Spine", "Spine1", "Spine2"], (0.0, 0.48, 0.0), [(0.0, 0.09, 0.0)] * 4)
    neck_base = out["Spine2"].tail
    chain(["Neck", "Head"], neck_base, [(0.0, 0.05, 0.0), (0.0, 0.10, 0.0)])
    head_tail = out["Head"].tail
    place("Jaw", head_tail, (0.0, 0.02, -0.06))
    place("LeftEye", (-0.03, head_tail[1], head_tail[2]), (0.0, 0.01, -0.02))
    place("RightEye", (0.03, head_tail[1], head_tail[2]), (0.0, 0.01, -0.02))
    shoulder_y = out["Spine2"].tail[1]
    arm_deltas = [(-0.05, 0.01, 0.0), (-0.14, 0.0, 0.0), (-0.16, 0.0, 0.0), (-0.09, 0.0, 0.0)]
    chain(["LeftShoulder", "LeftArm", "LeftForeArm", "LeftHand"], (-0.04, shoulder_y, 0.0), arm_deltas)
    chain(["RightShoulder", "RightArm", "RightForeArm", "RightHand"], (0.04, shoulder_y, 0.0), [_mirror(d) for d in arm_deltas])
    hips_head = out["Hips"].head
    leg_deltas = [(0.0, -0.20, 0.0), (0.0, -0.18, 0.0), (0.0, -0.05, -0.02), (0.0, -0.02, -0.05)]
    chain(["LeftUpLeg", "LeftLeg", "LeftFoot", "LeftToeBase"], (-0.08, hips_head[1], 0.0), leg_deltas)
    chain(["RightUpLeg", "RightLeg", "RightFoot", "RightToeBase"], (0.08, hips_head[1], 0.0), leg_deltas)


def _build_quadruped(place, chain, out) -> None:
    chain(["Hips", "Spine", "Spine1", "Spine2"], (0.0, 0.55, 0.25), [(0.0, 0.01, -0.06), (0.0, 0.01, -0.16), (0.0, 0.01, -0.16), (0.0, 0.01, -0.16)])
    withers = out["Spine2"].tail
    chain(["Neck", "Head"], withers, [(0.0, 0.04, -0.09), (0.0, 0.04, -0.09)])
    place("Jaw", out["Head"].tail, (0.0, 0.02, -0.06))
    front_deltas = [(0.0, -0.22, 0.0), (0.0, -0.20, 0.0), (0.0, -0.06, -0.04)]
    hind_deltas = [(0.0, -0.22, 0.0), (0.0, -0.20, 0.0), (0.0, -0.06, -0.04)]
    chain(["LeftFrontLeg", "LeftFrontKnee", "LeftFrontFoot"], (-0.14, withers[1], withers[2]), front_deltas)
    chain(["RightFrontLeg", "RightFrontKnee", "RightFrontFoot"], (0.14, withers[1], withers[2]), front_deltas)
    hips_head = out["Hips"].head
    chain(["LeftHindLeg", "LeftHindKnee", "LeftHindFoot"], (-0.14, hips_head[1], hips_head[2]), hind_deltas)
    chain(["RightHindLeg", "RightHindKnee", "RightHindFoot"], (0.14, hips_head[1], hips_head[2]), hind_deltas)
    chain(["Tail", "Tail1", "Tail2"], hips_head, [(0.0, 0.04, 0.09), (0.0, 0.03, 0.09), (0.0, 0.02, 0.08)])


def _build_avian(place, chain, out) -> None:
    chain(["Hips", "Spine", "Spine1"], (0.0, 0.50, 0.10), [(0.0, 0.06, -0.06)] * 3)
    chest = out["Spine1"].tail
    chain(["Neck", "Head"], chest, [(0.0, 0.06, -0.05), (0.0, 0.05, -0.04)])
    place("Jaw", out["Head"].tail, (0.0, 0.02, -0.05))
    wing_deltas = [(-0.14, 0.01, 0.0), (-0.14, 0.0, 0.0), (-0.10, -0.01, 0.0)]
    chain(["LeftWing1", "LeftWing2", "LeftWing3"], (-0.06, chest[1], chest[2]), wing_deltas)
    chain(["RightWing1", "RightWing2", "RightWing3"], (0.06, chest[1], chest[2]), [_mirror(d) for d in wing_deltas])
    hips_head = out["Hips"].head
    leg_deltas = [(0.0, -0.16, 0.0), (0.0, -0.10, -0.02)]
    chain(["LeftLeg", "LeftFoot"], (-0.07, hips_head[1], hips_head[2]), leg_deltas)
    chain(["RightLeg", "RightFoot"], (0.07, hips_head[1], hips_head[2]), leg_deltas)
    chain(["Tail1", "Tail2", "Tail3"], hips_head, [(0.0, -0.02, 0.10), (0.0, -0.01, 0.09), (0.0, 0.0, 0.08)])


def _build_serpentine(place, chain, out) -> None:
    chain(["Hips", "Spine"] + [f"Spine{i}" for i in range(1, 10)] + ["Neck", "Head"], (0.0, 0.5, 0.45), [(0.0, 0.0, -0.07)] * 13)
    place("Jaw", out["Head"].tail, (0.0, 0.0, -0.06))
    chain(["Tail1", "Tail2", "Tail3", "Tail4", "Tail5"], out["Hips"].head, [(0.0, 0.0, 0.06)] * 5)


def _build_aquatic(place, chain, out) -> None:
    chain(["Hips", "Spine", "Spine1", "Spine2"], (0.0, 0.55, 0.20), [(0.0, 0.01, -0.06), (0.0, 0.01, -0.13), (0.0, 0.01, -0.13), (0.0, 0.01, -0.13)])
    mid = out["Spine2"].tail
    chain(["Neck", "Head"], mid, [(0.0, 0.02, -0.08), (0.0, 0.02, -0.08)])
    place("Jaw", out["Head"].tail, (0.0, 0.0, -0.05))
    place("TopFin", mid, (0.0, 0.15, 0.0))
    place("BottomFin", mid, (0.0, -0.12, 0.0))
    place("LeftFin", (-0.10, mid[1], mid[2]), (-0.14, 0.0, 0.0))
    place("RightFin", (0.10, mid[1], mid[2]), (0.14, 0.0, 0.0))
    chain(["Tail1", "Tail2", "Tail3", "Tail4"], out["Hips"].head, [(0.0, 0.0, 0.10), (0.0, 0.0, 0.10), (0.0, 0.03, 0.08), (0.0, -0.03, 0.08)])


def _build_hexapod(place, chain, out) -> None:
    chain(["Hips", "Spine", "Spine1", "Spine2"], (0.0, 0.55, 0.20), [(0.0, 0.0, -0.06), (0.0, 0.0, -0.13), (0.0, 0.0, -0.13), (0.0, 0.0, -0.13)])
    front = out["Spine"].tail
    mid = out["Spine1"].tail
    rear = out["Spine2"].tail
    chain(["Neck", "Head"], rear, [(0.0, 0.04, -0.07), (0.0, 0.03, -0.06)])
    head_tail = out["Head"].tail
    place("Jaw", head_tail, (0.0, 0.0, -0.05))
    place("LeftAntenna", (-0.02, head_tail[1], head_tail[2]), (-0.06, 0.06, -0.04))
    place("RightAntenna", (0.02, head_tail[1], head_tail[2]), (0.06, 0.06, -0.04))
    leg_deltas = [(-0.05, -0.15, 0.0), (0.0, -0.15, 0.0), (0.0, -0.08, -0.03)]
    for prefix, attach in (("Front", front), ("Mid", mid), ("Hind", rear)):
        chain([f"Left{prefix}Leg", f"Left{prefix}Knee", f"Left{prefix}Foot"], (-0.16, attach[1], attach[2]), leg_deltas)
        chain([f"Right{prefix}Leg", f"Right{prefix}Knee", f"Right{prefix}Foot"], (0.16, attach[1], attach[2]), [_mirror(d) for d in leg_deltas])
    chain(["Tail1", "Tail2"], out["Hips"].head, [(0.0, 0.0, 0.10), (0.0, 0.0, 0.09)])


def _build_octopod(place, chain, out) -> None:
    chain(["Hips", "Spine", "Spine1", "Spine2"], (0.0, 0.45, 0.0), [(0.0, 0.03, 0.0), (0.0, 0.08, 0.0), (0.0, 0.08, 0.0), (0.0, 0.08, 0.0)])
    chain(["Neck", "Head"], out["Spine2"].tail, [(0.0, 0.05, 0.0), (0.0, 0.09, 0.0)])
    place("Jaw", out["Head"].tail, (0.0, 0.0, -0.05))
    hips_head = out["Hips"].head
    for side, sx in (("Left", -1.0), ("Right", 1.0)):
        for name, z in (("FrontLeg", -0.15), ("MidFrontLeg", -0.05), ("MidBackLeg", 0.05), ("BackLeg", 0.15)):
            place(f"{side}{name}", (sx * 0.10, hips_head[1], z), (sx * 0.12, -0.30, z * 0.3))
    place("Tail1", hips_head, (0.0, -0.10, 0.06))


_LAYOUTS = {
    "biped": _build_biped,
    "quadruped": _build_quadruped,
    "avian": _build_avian,
    "serpentine": _build_serpentine,
    "aquatic": _build_aquatic,
    "hexapod": _build_hexapod,
    "octopod": _build_octopod,
}


def layout_skeleton(rig_type: str) -> dict[str, LayoutBone]:
    """Deterministic bbox-proportioned skeleton layout for a rig type.

    Coordinates are fractions of the mesh's world bounding box: x/z in
    [-0.5, 0.5] of width/depth around the box centre, y in [0, 1] of height
    from the bottom. ``auto_rig.py`` scales them to the imported GLB. Model
    faces -Z (glTF convention).
    """
    hierarchy = get_bone_hierarchy(rig_type)
    parent_of = {name: parent for name, parent, _ in hierarchy}
    out: dict[str, LayoutBone] = {}

    def place(name: str, head: Vec3, delta: Vec3) -> None:
        out[name] = LayoutBone(parent=parent_of[name], head=head, tail=(head[0] + delta[0], head[1] + delta[1], head[2] + delta[2]))

    def chain(names: list[str], start: Vec3, deltas: list[Vec3]) -> None:
        pos = start
        for name, d in zip(names, deltas, strict=True):
            place(name, pos, d)
            pos = out[name].tail

    _LAYOUTS.get(rig_type, _build_biped)(place, chain, out)
    missing = set(parent_of) - set(out)
    if missing:
        raise ValueError(f"rig layout for {rig_type!r} misses bones: {sorted(missing)}")
    return out

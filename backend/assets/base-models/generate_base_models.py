import json
import math
import os
import shutil

import bmesh
import bpy
import numpy as np
from mathutils import Vector

# ====================================================================
# Configuration
# ====================================================================

OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
)

SPECIES = {
    "human": {
        "height_scale": 1.0,
        "width_scale": 1.0,
        "skin_color": (0.85, 0.70, 0.56),
        "eye_color": (0.10, 0.10, 0.14),
        "skin_roughness": 0.60,
        "skin_metallic": 0.0,
        "emissive": None,
        "transmission": 0.0,
        "ring_segments": 16,
        "subsurf": 1,
    },
    "elf": {
        "height_scale": 1.12,
        "width_scale": 0.88,
        "skin_color": (0.90, 0.83, 0.75),
        "eye_color": (0.08, 0.22, 0.38),
        "skin_roughness": 0.55,
        "skin_metallic": 0.0,
        "emissive": None,
        "transmission": 0.0,
        "ring_segments": 18,
        "subsurf": 1,
    },
    "spirit_beast": {
        "height_scale": 0.93,
        "width_scale": 1.08,
        "skin_color": (0.40, 0.28, 0.18),
        "eye_color": (0.65, 0.38, 0.10),
        "skin_roughness": 0.75,
        "skin_metallic": 0.0,
        "emissive": None,
        "transmission": 0.0,
        "ring_segments": 16,
        "subsurf": 1,
    },
    "mecha": {
        "height_scale": 1.15,
        "width_scale": 1.12,
        "skin_color": (0.42, 0.44, 0.48),
        "eye_color": (0.0, 0.85, 1.0),
        "skin_roughness": 0.35,
        "skin_metallic": 0.85,
        "emissive": (0.0, 0.7, 0.9),
        "transmission": 0.0,
        "ring_segments": 6,
        "subsurf": 0,
    },
    "shapeshifter": {
        "height_scale": 1.0,
        "width_scale": 0.95,
        "skin_color": (0.78, 0.73, 0.83),
        "eye_color": (0.55, 0.38, 0.78),
        "skin_roughness": 0.30,
        "skin_metallic": 0.0,
        "emissive": (0.35, 0.25, 0.55),
        "transmission": 0.20,
        "ring_segments": 20,
        "subsurf": 1,
    },
}

# Ring data: (z, radius_x, radius_y[, y_shift]) pre-scaling.
# Character faces +Y in Blender. Root Empty rotates 180 deg so the
# exported glTF faces -Z per GLB_MODEL_SPEC.md §1.
TORSO_RINGS = [
    (0.80, 0.140, 0.100, 0.000),
    (0.85, 0.150, 0.110, 0.000),
    (0.91, 0.130, 0.090, 0.000),
    (0.98, 0.140, 0.095, 0.000),
    (1.05, 0.150, 0.100, 0.000),
    (1.12, 0.160, 0.105, 0.000),
    (1.18, 0.150, 0.100, 0.000),
    (1.22, 0.100, 0.070, 0.000),
    (1.26, 0.055, 0.050, 0.000),
    (1.29, 0.050, 0.045, 0.000),
    (1.32, 0.070, 0.065, -0.005),
    (1.35, 0.082, 0.075, -0.010),
    (1.38, 0.090, 0.082, -0.014),
    (1.41, 0.096, 0.086, -0.012),
    (1.44, 0.098, 0.090, -0.010),
    (1.47, 0.096, 0.088, -0.006),
    (1.50, 0.092, 0.085, -0.002),
    (1.53, 0.082, 0.078, 0.000),
    (1.56, 0.065, 0.062, 0.000),
    (1.59, 0.040, 0.038, 0.000),
    (1.62, 0.015, 0.014, 0.000),
]
ARM_RINGS = [
    (1.16, 0.048, 0.045),
    (1.10, 0.046, 0.043),
    (1.04, 0.044, 0.041),
    (0.98, 0.042, 0.039),
    (0.92, 0.040, 0.037),
    (0.86, 0.036, 0.034),
]
LEG_RINGS = [
    (0.80, 0.065, 0.060),
    (0.74, 0.063, 0.058),
    (0.66, 0.060, 0.055),
    (0.56, 0.056, 0.052),
    (0.46, 0.052, 0.048),
    (0.36, 0.048, 0.045),
    (0.26, 0.044, 0.042),
    (0.16, 0.040, 0.038),
    (0.08, 0.036, 0.034),
]
FOOT_RINGS = [
    (0.08, 0.036, 0.034, 0.000),
    (0.06, 0.038, 0.048, -0.020),
    (0.03, 0.036, 0.044, -0.050),
    (0.01, 0.028, 0.032, -0.070),
    (0.003, 0.018, 0.020, -0.080),
]

# ====================================================================
# Utilities
# ====================================================================


def falloff(dist, max_dist):
    t = max(0.0, 1.0 - dist / max_dist)
    return t * t * (3.0 - 2.0 * t)


def clean_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def point_to_segment_dist(p, a, b):
    ab = b - a
    ab_len2 = ab.dot(ab)
    if ab_len2 < 1e-10:
        return (p - a).length
    t = max(0.0, min(1.0, (p - a).dot(ab) / ab_len2))
    return (p - (a + ab * t)).length


def set_input(node, name, value):
    if name in node.inputs:
        node.inputs[name].default_value = value


# ====================================================================
# Mesh building
# ====================================================================


def build_tube(bm, specs, segments, h, w, x_center=0.0):
    rings = []
    for spec in specs:
        z = spec[0] * h
        rx = spec[1] * w
        ry = spec[2] * w
        ys = spec[3] if len(spec) > 3 else 0.0
        ring = []
        for i in range(segments):
            ang = 2.0 * math.pi * i / segments
            ring.append(bm.verts.new((rx * math.cos(ang) + x_center * w, ry * math.sin(ang) + ys, z)))
        rings.append(ring)
    for i in range(len(rings) - 1):
        a, b = rings[i], rings[i + 1]
        for j in range(len(a)):
            k = (j + 1) % len(a)
            try:
                bm.faces.new([a[j], a[k], b[k], b[j]])
            except ValueError:
                pass
    return rings


def cap_ring(bm, ring):
    cx = sum(v.co.x for v in ring) / len(ring)
    cy = sum(v.co.y for v in ring) / len(ring)
    cz = sum(v.co.z for v in ring) / len(ring)
    center = bm.verts.new((cx, cy, cz))
    for i in range(len(ring)):
        j = (i + 1) % len(ring)
        try:
            bm.faces.new([ring[i], ring[j], center])
        except ValueError:
            pass
    return center


def build_body_mesh(config):
    h = config["height_scale"]
    w = config["width_scale"]
    segs = config["ring_segments"]
    limb_segs = max(segs - 4, 6)
    foot_segs = max(segs - 6, 6)
    bm = bmesh.new()
    torso = build_tube(bm, TORSO_RINGS, segs, h, w)
    cap_ring(bm, torso[0])
    cap_ring(bm, torso[-1])
    for sign in (-1, 1):
        arm = build_tube(bm, ARM_RINGS, limb_segs, h, w, x_center=sign * 0.195)
        cap_ring(bm, arm[-1])
    for sign in (-1, 1):
        build_tube(bm, LEG_RINGS, limb_segs, h, w, x_center=sign * 0.065)
    for sign in (-1, 1):
        foot = build_tube(bm, FOOT_RINGS, foot_segs, h, w, x_center=sign * 0.065)
        cap_ring(bm, foot[-1])
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    mesh = bpy.data.meshes.new("Body")
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Body", mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def apply_subsurf(obj, levels):
    if levels <= 0:
        return
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    mod = obj.modifiers.new("Subsurf", "SUBSURF")
    mod.levels = levels
    mod.render_levels = levels
    bpy.ops.object.modifier_apply(modifier="Subsurf")


def add_face_features(obj, config):
    h = config["height_scale"]
    w = config["width_scale"]
    ez = 1.44 * h
    ey = -0.085 * w
    ex = 0.034 * w
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    for v in bm.verts:
        co = v.co
        for sx in (-ex, ex):
            d = (co - Vector((sx, ey, ez))).length
            if d < 0.030:
                f = falloff(d, 0.030)
                v.co.y += 0.010 * f
        nc = Vector((0, -0.094 * w, 1.41 * h))
        d = (co - nc).length
        if d < 0.020:
            f = falloff(d, 0.020)
            v.co.y -= 0.012 * f
            v.co.z += 0.002 * f
        mc = Vector((0, -0.090 * w, 1.38 * h))
        d = (co - mc).length
        if d < 0.024:
            f = falloff(d, 0.024)
            v.co.y += 0.006 * f
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


# ====================================================================
# Armature
# ====================================================================


def bone_defs(h=1.0, w=1.0):
    return [
        ("Hips", (0, 0, 0.80 * h), (0, 0, 0.90 * h), None, False),
        ("Spine", (0, 0, 0.90 * h), (0, 0, 1.02 * h), "Hips", False),
        ("Spine1", (0, 0, 1.02 * h), (0, 0, 1.14 * h), "Spine", False),
        ("Spine2", (0, 0, 1.14 * h), (0, 0, 1.22 * h), "Spine1", False),
        ("Neck", (0, 0, 1.22 * h), (0, 0, 1.30 * h), "Spine2", False),
        ("Head", (0, 0, 1.30 * h), (0, 0, 1.56 * h), "Neck", False),
        ("LeftEye", (-0.034 * w, -0.055, 1.44 * h), (-0.034 * w, -0.075, 1.44 * h), "Head", False),
        ("RightEye", (0.034 * w, -0.055, 1.44 * h), (0.034 * w, -0.075, 1.44 * h), "Head", False),
        ("Jaw", (0, -0.010, 1.36 * h), (0, -0.040, 1.33 * h), "Head", False),
        ("LeftShoulder", (-0.040 * w, 0, 1.21 * h), (-0.120 * w, 0, 1.19 * h), "Spine2", False),
        ("LeftArm", (-0.120 * w, 0, 1.19 * h), (-0.185 * w, 0, 1.04 * h), "LeftShoulder", False),
        ("LeftForeArm", (-0.185 * w, 0, 1.04 * h), (-0.200 * w, 0, 0.88 * h), "LeftArm", False),
        ("LeftHand", (-0.200 * w, 0, 0.88 * h), (-0.210 * w, 0, 0.80 * h), "LeftForeArm", False),
        ("RightShoulder", (0.040 * w, 0, 1.21 * h), (0.120 * w, 0, 1.19 * h), "Spine2", False),
        ("RightArm", (0.120 * w, 0, 1.19 * h), (0.185 * w, 0, 1.04 * h), "RightShoulder", False),
        ("RightForeArm", (0.185 * w, 0, 1.04 * h), (0.200 * w, 0, 0.88 * h), "RightArm", False),
        ("RightHand", (0.200 * w, 0, 0.88 * h), (0.210 * w, 0, 0.80 * h), "RightForeArm", False),
        ("LeftUpLeg", (-0.060 * w, 0, 0.80 * h), (-0.065 * w, 0, 0.46 * h), "Hips", False),
        ("LeftLeg", (-0.065 * w, 0, 0.46 * h), (-0.065 * w, 0, 0.08 * h), "LeftUpLeg", False),
        ("LeftFoot", (-0.065 * w, 0, 0.08 * h), (-0.065 * w, -0.05, 0.03 * h), "LeftLeg", False),
        ("LeftToeBase", (-0.065 * w, -0.05, 0.03 * h), (-0.065 * w, -0.09, 0.0), "LeftFoot", False),
        ("RightUpLeg", (0.060 * w, 0, 0.80 * h), (0.065 * w, 0, 0.46 * h), "Hips", False),
        ("RightLeg", (0.065 * w, 0, 0.46 * h), (0.065 * w, 0, 0.08 * h), "RightUpLeg", False),
        ("RightFoot", (0.065 * w, 0, 0.08 * h), (0.065 * w, -0.05, 0.03 * h), "RightLeg", False),
        ("RightToeBase", (0.065 * w, -0.05, 0.03 * h), (0.065 * w, -0.09, 0.0), "RightFoot", False),
    ]


def build_armature(config):
    h = config["height_scale"]
    w = config["width_scale"]
    arm_data = bpy.data.armatures.new("Armature")
    arm_obj = bpy.data.objects.new("Armature", arm_data)
    bpy.context.collection.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")
    bones = {}
    for name, head, tail, parent_name, _c in bone_defs(h, w):
        eb = arm_data.edit_bones.new(name)
        eb.head = Vector(head)
        eb.tail = Vector(tail)
        eb.use_connect = False
        if parent_name and parent_name in bones:
            eb.parent = bones[parent_name]
        bones[name] = eb
    bpy.ops.object.mode_set(mode="OBJECT")
    return arm_obj


# ====================================================================
# Eye objects
# ====================================================================


def create_eye_objects(config, armature):
    h = config["height_scale"]
    w = config["width_scale"]
    ez = 1.44 * h
    ex = 0.034 * w
    ey = -0.068
    eyes = []
    for name, xp in [("LeftEye", -ex), ("RightEye", ex)]:
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.020, location=(xp, ey, ez), segments=16, ring_count=12)
        eye = bpy.context.object
        eye.name = name
        vg = eye.vertex_groups.new(name="Head")
        vg.add(range(len(eye.data.vertices)), 1.0, "REPLACE")
        mod = eye.modifiers.new("Armature", "ARMATURE")
        mod.object = armature
        eye.parent = armature
        eyes.append(eye)
    return eyes


# ====================================================================
# Weight assignment
# ====================================================================


def assign_weights(obj, armature):
    bone_segs = []
    for bone in armature.data.bones:
        hd = armature.matrix_world @ bone.head_local
        tl = armature.matrix_world @ bone.tail_local
        bone_segs.append((bone.name, hd, tl))
    for name, _, _ in bone_segs:
        if name not in obj.vertex_groups:
            obj.vertex_groups.new(name=name)
    sigma = 0.075
    two_s2 = 2.0 * sigma * sigma
    for v in obj.data.vertices:
        weights = []
        for name, hd, tl in bone_segs:
            d = point_to_segment_dist(v.co, hd, tl)
            if d < 0.25:
                wgt = math.exp(-(d * d) / two_s2)
                if wgt > 0.001:
                    weights.append((name, wgt))
        weights.sort(key=lambda x: -x[1])
        weights = weights[:4]
        total = sum(w for _, w in weights)
        if total > 0.001:
            for name, wgt in weights:
                obj.vertex_groups[name].add([v.index], wgt / total, "REPLACE")


# ====================================================================
# Morph targets
# ====================================================================


def add_morph_targets(obj, config):
    h = config["height_scale"]
    w = config["width_scale"]
    EZ = 1.44 * h
    EY = -0.085 * w
    EX = 0.034 * w
    MZ = 1.38 * h
    MY = -0.090 * w
    BZ = 1.47 * h
    BY = -0.082 * w
    NZ = 1.41 * h
    NY = -0.094 * w
    CZ = 1.42 * h
    CY = -0.082 * w
    obj.shape_key_add(name="Basis", from_mix=False)
    basis = obj.data.shape_keys.key_blocks[0]

    def add_sk(name, fn):
        sk = obj.shape_key_add(name=name, from_mix=False)
        for i in range(len(basis.data)):
            sk.data[i].co = fn(basis.data[i].co)

    def displace(co, cx, cy, cz, radius, dx, dy, dz, upper=False, lower=False):
        d = math.sqrt((co.x - cx) ** 2 + (co.y - cy) ** 2 + (co.z - cz) ** 2)
        if d >= radius:
            return co
        if upper and co.z <= cz:
            return co
        if lower and co.z >= cz:
            return co
        f = falloff(d, radius)
        return Vector((co.x + dx * f, co.y + dy * f, co.z + dz * f))

    add_sk("eyeBlinkLeft", lambda c: displace(c, -EX, EY, EZ, 0.026, 0, 0.005, -0.014, upper=True))
    add_sk("eyeBlinkRight", lambda c: displace(c, EX, EY, EZ, 0.026, 0, 0.005, -0.014, upper=True))

    add_sk("eyeWideLeft", lambda c: displace(c, -EX, EY, EZ, 0.022, 0, -0.003, -0.004, lower=True))
    add_sk("eyeWideRight", lambda c: displace(c, EX, EY, EZ, 0.022, 0, -0.003, -0.004, lower=True))

    add_sk("eyeSquintLeft", lambda c: displace(c, -EX, EY, EZ, 0.024, 0, 0.003, -0.006, upper=True))
    add_sk("eyeSquintRight", lambda c: displace(c, EX, EY, EZ, 0.024, 0, 0.003, -0.006, upper=True))

    def _eye_droop(co):
        r = co
        for sx in (-EX, EX):
            r = displace(r, sx, EY, EZ, 0.025, 0, 0.002, -0.008, upper=True)
        return r

    add_sk("eyesLookDown", _eye_droop)

    add_sk("browInnerUp", lambda c: displace(c, 0, BY, BZ, 0.035, 0, -0.002, 0.006))
    add_sk("browInnerDown", lambda c: displace(c, 0, BY, BZ, 0.035, 0, 0.002, -0.006))
    add_sk("jawOpen", lambda c: displace(c, 0, MY, MZ, 0.050, 0, 0.004, -0.018, lower=True))

    def _smile(co):
        r = co
        for sx in (-EX * 0.8, EX * 0.8):
            r = displace(r, sx, MY, MZ, 0.025, 0, -0.002, 0.006)
        return r

    add_sk("mouthSmile", _smile)
    add_sk("mouthSmileRight", lambda c: displace(c, EX * 0.8, MY, MZ, 0.025, 0, -0.002, 0.006))

    def _frown(co):
        r = co
        for sx in (-EX * 0.8, EX * 0.8):
            r = displace(r, sx, MY, MZ, 0.025, 0, 0.001, -0.005)
        return r

    add_sk("mouthFrown", _frown)

    def _cheek(co):
        r = co
        for sx in (-EX, EX):
            r = displace(r, sx, CY, CZ, 0.028, 0, -0.001, 0.005)
        return r

    add_sk("cheekSquintLeft", _cheek)
    add_sk("noseSneerLeft", lambda c: displace(c, 0, NY, NZ, 0.022, 0, -0.003, 0.004))
    add_sk("tongueOut", lambda c: displace(c, 0, MY, MZ - 0.02, 0.030, 0, -0.012, -0.008, lower=True))

    # Body morphs
    add_sk("Body_Height", lambda c: Vector((c.x, c.y, c.z * 1.03)))

    def _bw(co):
        return Vector((co.x * 1.05, co.y * 1.05, co.z))

    add_sk("Body_Weight", _bw)

    def _bm(co):
        if co.z > 0.90 * h:
            return Vector((co.x * 1.04, co.y * 1.04, co.z))
        return co

    add_sk("Body_Muscle", _bm)

    def _bs(co):
        if 1.08 * h < co.z < 1.24 * h:
            return Vector((co.x * 1.10, co.y, co.z))
        return co

    add_sk("Body_Shoulders", _bs)

    def _fw(co):
        if co.z > 1.30 * h:
            return Vector((co.x * 1.06, co.y, co.z))
        return co

    add_sk("Face_Width", _fw)

    def _fj(co):
        if 1.32 * h < co.z < 1.40 * h:
            return Vector((co.x * 1.08, co.y, co.z))
        return co

    add_sk("Face_Jaw", _fj)


# ====================================================================
# Procedural textures
# ====================================================================


def _smooth_noise(uv, scale, seed=0):
    x = uv[..., 0] * scale
    y = uv[..., 1] * scale
    xi = x.astype(np.int32)
    yi = y.astype(np.int32)
    xf = x - xi
    yf = y - yi
    xf = xf * xf * (3 - 2 * xf)
    yf = yf * yf * (3 - 2 * yf)

    def h(ix, iy):
        g = np.sin(ix * 127.1 + iy * 311.7 + seed * 74.7)
        return g - np.floor(g)

    a = h(xi, yi)
    b = h(xi + 1, yi)
    cc = h(xi, yi + 1)
    d = h(xi + 1, yi + 1)
    return a * (1 - xf) * (1 - yf) + b * xf * (1 - yf) + cc * (1 - xf) * yf + d * xf * yf


def _make_image(name, pixels_flat, size):
    img = bpy.data.images.new(name, width=size, height=size)
    img.pixels[:] = pixels_flat
    img.pack()
    return img


def create_procedural_textures(config):
    size = 256
    sc = config["skin_color"]
    sr = config["skin_roughness"]
    sm = config["skin_metallic"]
    seed = int(config["height_scale"] * 1000 + config["width_scale"] * 100) % 999
    u = np.linspace(0, 1, size, dtype=np.float32)
    v = np.linspace(0, 1, size, dtype=np.float32)
    uu, vv = np.meshgrid(u, v)
    uv = np.stack([uu, vv], axis=-1)
    ones = np.ones((size, size), dtype=np.float32)
    n = (_smooth_noise(uv, 24, seed) * 0.05 - 0.025).astype(np.float32)
    bc = np.dstack([np.clip(sc[0] + n, 0, 1), np.clip(sc[1] + n, 0, 1), np.clip(sc[2] + n, 0, 1), ones]).flatten()
    nx = (_smooth_noise(uv, 16, seed + 1) * 0.04 - 0.02).astype(np.float32)
    ny = (_smooth_noise(uv, 16, seed + 2) * 0.04 - 0.02).astype(np.float32)
    nm = np.dstack([0.5 + nx, 0.5 + ny, ones, ones]).flatten()
    rn = (_smooth_noise(uv, 12, seed + 3) * 0.1).astype(np.float32)
    rv = np.clip(sr + rn, 0.02, 0.98)
    rm = np.dstack([rv, rv, rv, ones]).flatten()
    mn = (_smooth_noise(uv, 8, seed + 4) * 0.05).astype(np.float32)
    mv = np.clip(sm + mn, 0.0, 1.0)
    mm = np.dstack([mv, mv, mv, ones]).flatten()
    dist = np.sqrt((uu - 0.5) ** 2 + (vv - 0.5) ** 2)
    ao_val = np.clip(1.0 - dist * 0.35, 0.6, 1.0).astype(np.float32)
    aom = np.dstack([ao_val, ao_val, ao_val, ones]).flatten()
    return {
        "base_color": _make_image("BaseColor", bc, size),
        "normal": _make_image("Normal", nm, size),
        "roughness": _make_image("Roughness", rm, size),
        "metallic": _make_image("Metallic", mm, size),
        "ao": _make_image("AO", aom, size),
    }


# ====================================================================
# UV projection
# ====================================================================


def add_uv_projection(obj):
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")


# ====================================================================
# Materials
# ====================================================================


def create_materials(body_obj, eye_objs, config):
    em = config.get("emissive")
    tr = config.get("transmission", 0.0)
    textures = create_procedural_textures(config)
    skin = bpy.data.materials.new("Skin")
    skin.use_nodes = True
    nt = skin.node_tree
    nodes = nt.nodes
    links = nt.links
    bsdf = nodes.get("Principled BSDF")
    if bsdf:
        if em:
            for key in ("Emission Color", "Emission"):
                if key in bsdf.inputs:
                    bsdf.inputs[key].default_value = (*em, 1.0)
                    break
            set_input(bsdf, "Emission Strength", 0.4)
        if tr > 0:
            for key in ("Transmission Weight", "Transmission"):
                if key in bsdf.inputs:
                    bsdf.inputs[key].default_value = tr
                    break

    def tex_node(img, cs, x, y):
        n = nodes.new("ShaderNodeTexImage")
        n.image = img
        n.image.colorspace_settings.name = cs
        n.location = (x, y)
        return n

    bc_tex = tex_node(textures["base_color"], "sRGB", -800, 400)
    links.new(bc_tex.outputs["Color"], bsdf.inputs["Base Color"])
    met_tex = tex_node(textures["metallic"], "Non-Color", -800, 150)
    links.new(met_tex.outputs["Color"], bsdf.inputs["Metallic"])
    rough_tex = tex_node(textures["roughness"], "Non-Color", -800, -50)
    links.new(rough_tex.outputs["Color"], bsdf.inputs["Roughness"])
    norm_tex = tex_node(textures["normal"], "Non-Color", -800, -250)
    nmap = nodes.new("ShaderNodeNormalMap")
    nmap.location = (-500, -250)
    links.new(norm_tex.outputs["Color"], nmap.inputs["Color"])
    links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    body_obj.data.materials.append(skin)
    ec = config["eye_color"]
    eye_mat = bpy.data.materials.new("Eyes")
    eye_mat.use_nodes = True
    ebsdf = eye_mat.node_tree.nodes.get("Principled BSDF")
    if ebsdf:
        set_input(ebsdf, "Base Color", (*ec, 1.0))
        set_input(ebsdf, "Roughness", 0.12)
        set_input(ebsdf, "Metallic", 0.0)
        if em:
            for key in ("Emission Color", "Emission"):
                if key in ebsdf.inputs:
                    ebsdf.inputs[key].default_value = (*ec, 1.0)
                    break
            set_input(ebsdf, "Emission Strength", 1.5)
    for eye in eye_objs:
        eye.data.materials.append(eye_mat)


# ====================================================================
# Animation — 31 clips per GLB_MODEL_SPEC.md §3
# ====================================================================

import math as _m


def _kf(arm, bone, frame, rot=(0, 0, 0)):
    if bone not in arm.pose.bones:
        return
    pb = arm.pose.bones[bone]
    pb.rotation_mode = "XYZ"
    pb.rotation_euler = rot
    pb.keyframe_insert(data_path="rotation_euler", frame=frame)


def _rest(arm, frame):
    """Keyframe the neutral pose on all pose bones."""
    for pb in arm.pose.bones:
        pb.rotation_mode = "XYZ"
        pb.rotation_euler = (0, 0, 0)
        pb.keyframe_insert(data_path="rotation_euler", frame=frame)


# ---- §3.1 Core state animations (MUST, 9) ----


def kf_idle(arm, cfg):
    """Breathing + weight shift + micro head movement. 120f loop."""
    _rest(arm, 0)
    for fr in (0, 120):
        _kf(arm, "Spine", fr, (0.02, 0, 0))
        _kf(arm, "Spine1", fr, (0.01, 0, 0))
        _kf(arm, "LeftArm", fr, (0, 0, 0.06))
        _kf(arm, "RightArm", fr, (0, 0, -0.06))
        _kf(arm, "LeftForeArm", fr, (-0.04, 0, 0))
        _kf(arm, "RightForeArm", fr, (-0.04, 0, 0))
    _kf(arm, "Spine", 30, (-0.01, 0, 0.008))
    _kf(arm, "Spine1", 30, (-0.02, 0, 0))
    _kf(arm, "Head", 30, (-0.01, 0.02, 0.01))
    _kf(arm, "Spine", 60, (0.04, 0, -0.008))
    _kf(arm, "Spine1", 60, (0.025, 0, 0))
    _kf(arm, "Head", 60, (0.01, -0.01, -0.01))
    _kf(arm, "Spine", 90, (0.015, 0, 0.01))
    _kf(arm, "Head", 90, (0.0, 0.03, 0.005))


def kf_listening(arm, cfg):
    """Head tilt toward user, body lean forward. 105f loop."""
    _rest(arm, 0)
    for fr in (0, 105):
        _kf(arm, "Spine", fr, (-0.03, 0, 0))
        _kf(arm, "Spine1", fr, (-0.02, 0, 0))
        _kf(arm, "Neck", fr, (0.03, 0.06, 0))
        _kf(arm, "Head", fr, (0.02, 0.04, -0.03))
        _kf(arm, "LeftArm", fr, (0, 0, 0.04))
        _kf(arm, "RightArm", fr, (0, 0, -0.04))
    _kf(arm, "Head", 35, (0.03, 0.02, -0.05))
    _kf(arm, "Neck", 35, (0.05, 0.04, 0.02))
    _kf(arm, "Head", 70, (0.01, 0.05, -0.01))
    _kf(arm, "Neck", 70, (0.02, 0.08, 0))


def kf_thinking(arm, cfg):
    """Hand to chin, look up, occasional nod. 120f loop."""
    _rest(arm, 0)
    for fr in (0, 120):
        _kf(arm, "Spine", fr, (0.04, 0, 0))
        _kf(arm, "Head", fr, (0.12, 0.08, 0))
        _kf(arm, "RightArm", fr, (-0.35, 0, -0.55))
        _kf(arm, "RightForeArm", fr, (-1.15, 0.25, 0))
        _kf(arm, "RightHand", fr, (-0.15, 0, 0))
        _kf(arm, "LeftArm", fr, (0, 0, 0.08))
        _kf(arm, "LeftForeArm", fr, (-0.15, 0, 0))
    _kf(arm, "Head", 40, (0.15, 0.04, 0.03))
    _kf(arm, "RightForeArm", 40, (-1.05, 0.15, 0.05))
    _kf(arm, "Head", 80, (0.10, 0.12, -0.02))
    _kf(arm, "RightForeArm", 80, (-1.20, 0.35, -0.03))


def kf_speaking(arm, cfg):
    """Conversational gestures, alternating arms. 120f loop."""
    _rest(arm, 0)
    for fr in (0, 120):
        _kf(arm, "Spine", fr, (0.03, 0, 0))
        _kf(arm, "Spine1", fr, (0.02, 0, 0))
        _kf(arm, "LeftArm", fr, (0, 0, 0.10))
        _kf(arm, "RightArm", fr, (0, 0, -0.10))
        _kf(arm, "LeftForeArm", fr, (-0.20, 0, 0))
        _kf(arm, "RightForeArm", fr, (-0.20, 0, 0))
    _kf(arm, "RightArm", 30, (0.05, 0, -0.40))
    _kf(arm, "RightForeArm", 30, (-0.50, 0.15, 0))
    _kf(arm, "Head", 30, (0.03, -0.06, 0.02))
    _kf(arm, "Spine2", 30, (0, 0.04, 0))
    _kf(arm, "LeftArm", 60, (0.05, 0, 0.40))
    _kf(arm, "LeftForeArm", 60, (-0.50, -0.15, 0))
    _kf(arm, "Head", 60, (0.03, 0.06, -0.02))
    _kf(arm, "Spine2", 60, (0, -0.04, 0))
    _kf(arm, "LeftArm", 90, (0.02, 0, 0.25))
    _kf(arm, "RightArm", 90, (0.02, 0, -0.25))
    _kf(arm, "Head", 90, (0.06, 0, 0))


def kf_working(arm, cfg):
    """Forward lean, hands simulating typing. 120f loop."""
    _rest(arm, 0)
    for fr in (0, 120):
        _kf(arm, "Spine", fr, (-0.06, 0, 0))
        _kf(arm, "Spine1", fr, (-0.04, 0, 0))
        _kf(arm, "Spine2", fr, (-0.03, 0, 0))
        _kf(arm, "Neck", fr, (0.08, 0, 0))
        _kf(arm, "LeftArm", fr, (-0.10, 0, 0.20))
        _kf(arm, "RightArm", fr, (-0.10, 0, -0.20))
        _kf(arm, "LeftForeArm", fr, (-0.70, 0, 0))
        _kf(arm, "RightForeArm", fr, (-0.70, 0, 0))
        _kf(arm, "LeftHand", fr, (-0.10, 0, 0))
        _kf(arm, "RightHand", fr, (-0.10, 0, 0))
    _kf(arm, "LeftForeArm", 30, (-0.75, 0.05, 0.02))
    _kf(arm, "RightForeArm", 30, (-0.65, -0.05, -0.02))
    _kf(arm, "LeftForeArm", 60, (-0.65, -0.05, -0.02))
    _kf(arm, "RightForeArm", 60, (-0.75, 0.05, 0.02))
    _kf(arm, "LeftForeArm", 90, (-0.72, 0.03, 0.01))
    _kf(arm, "RightForeArm", 90, (-0.68, -0.03, -0.01))


def kf_sleeping(arm, cfg):
    """Eyes closed, head down, slow deep breathing. 180f loop."""
    _rest(arm, 0)
    for fr in (0, 180):
        _kf(arm, "Spine", fr, (-0.05, 0, 0.02))
        _kf(arm, "Spine1", fr, (-0.04, 0, 0))
        _kf(arm, "Neck", fr, (0.12, 0, 0))
        _kf(arm, "Head", fr, (0.20, 0.05, 0.03))
        _kf(arm, "LeftArm", fr, (0, 0, 0.03))
        _kf(arm, "RightArm", fr, (0, 0, -0.03))
        _kf(arm, "LeftForeArm", fr, (-0.10, 0, 0))
        _kf(arm, "RightForeArm", fr, (-0.10, 0, 0))
    _kf(arm, "Spine", 60, (-0.08, 0, 0.04))
    _kf(arm, "Head", 60, (0.25, 0.08, 0.05))
    _kf(arm, "Spine", 120, (-0.02, 0, 0.0))
    _kf(arm, "Head", 120, (0.15, 0.02, 0.01))


def kf_interacting(arm, cfg):
    """Poke reaction start — bounce + look at user. 45f oneshot."""
    _rest(arm, 0)
    _kf(arm, "Spine", 10, (-0.05, 0, 0))
    _kf(arm, "Head", 10, (-0.08, 0.12, 0))
    _kf(arm, "Spine2", 10, (0, 0.08, 0))
    _kf(arm, "Spine", 25, (-0.02, 0, 0))
    _kf(arm, "Head", 25, (-0.03, 0.06, 0))
    _rest(arm, 45)


def kf_emotional_idle(arm, cfg):
    """Neutral torso cycle for emotional states. 105f loop."""
    _rest(arm, 0)
    for fr in (0, 105):
        _kf(arm, "Spine", fr, (0.015, 0, 0))
        _kf(arm, "Spine1", fr, (0.01, 0, 0))
        _kf(arm, "LeftArm", fr, (0, 0, 0.04))
        _kf(arm, "RightArm", fr, (0, 0, -0.04))
    _kf(arm, "Spine", 35, (0.025, 0, 0.005))
    _kf(arm, "Spine", 70, (0.005, 0, -0.005))


def kf_disconnected(arm, cfg):
    """Yawn, head drift, gaze wandering. 150f loop."""
    _rest(arm, 0)
    for fr in (0, 150):
        _kf(arm, "Spine", fr, (-0.02, 0, 0.03))
        _kf(arm, "LeftArm", fr, (0, 0, 0.05))
        _kf(arm, "RightArm", fr, (0, 0, -0.05))
        _kf(arm, "LeftForeArm", fr, (-0.08, 0, 0))
        _kf(arm, "RightForeArm", fr, (-0.08, 0, 0))
    _kf(arm, "Head", 30, (0.10, -0.08, 0.05))
    _kf(arm, "Neck", 30, (0.05, -0.05, 0.03))
    _kf(arm, "Head", 75, (0.05, 0.10, -0.08))
    _kf(arm, "Neck", 75, (0.02, 0.06, -0.04))
    _kf(arm, "Head", 112, (0.08, 0.03, 0.06))


# ---- §3.2 Micro-action variants (SHOULD, 4) ----


def kf_idle_look_around(arm, cfg):
    """Look left, right, up, down. 75f oneshot."""
    _rest(arm, 0)
    _kf(arm, "Head", 15, (0, 0.15, 0))
    _kf(arm, "Neck", 15, (0, 0.08, 0))
    _kf(arm, "Head", 35, (0, -0.15, 0))
    _kf(arm, "Neck", 35, (0, -0.08, 0))
    _kf(arm, "Head", 55, (0.12, 0, 0))
    _kf(arm, "Head", 65, (-0.05, 0, 0))
    _rest(arm, 75)


def kf_idle_blink(arm, cfg):
    """Rub eyes / hard blink. 15f oneshot."""
    _rest(arm, 0)
    _kf(arm, "Head", 5, (-0.03, 0, 0.02))
    _kf(arm, "RightArm", 5, (-0.50, 0, -0.60))
    _kf(arm, "RightForeArm", 5, (-1.00, 0.30, 0))
    _kf(arm, "LeftArm", 7, (-0.50, 0, 0.60))
    _kf(arm, "LeftForeArm", 7, (-1.00, -0.30, 0))
    _rest(arm, 15)


def kf_idle_stretch(arm, cfg):
    """Stretch arms, expand chest. 75f oneshot."""
    _rest(arm, 0)
    _kf(arm, "Spine", 15, (-0.03, 0, 0))
    _kf(arm, "LeftArm", 25, (-0.20, 0, 0.60))
    _kf(arm, "RightArm", 25, (-0.20, 0, -0.60))
    _kf(arm, "LeftForeArm", 25, (-0.05, 0, 0))
    _kf(arm, "RightForeArm", 25, (-0.05, 0, 0))
    _kf(arm, "Spine", 35, (0.02, 0, 0))
    _kf(arm, "LeftArm", 50, (-0.08, 0, 0.15))
    _kf(arm, "RightArm", 50, (-0.08, 0, -0.15))
    _rest(arm, 75)


def kf_idle_shift_weight(arm, cfg):
    """Weight shift, shoulder roll. 45f oneshot."""
    _rest(arm, 0)
    _kf(arm, "Hips", 12, (0, 0, 0.03))
    _kf(arm, "Spine", 12, (0.01, 0, -0.02))
    _kf(arm, "LeftShoulder", 12, (-0.08, 0, 0))
    _kf(arm, "RightShoulder", 12, (0.08, 0, 0))
    _kf(arm, "Hips", 25, (0, 0, -0.03))
    _kf(arm, "Spine", 25, (0.01, 0, 0.02))
    _kf(arm, "LeftShoulder", 25, (0.08, 0, 0))
    _kf(arm, "RightShoulder", 25, (-0.08, 0, 0))
    _rest(arm, 45)


# ---- §3.3 Situational idle variants (SHOULD, 6) ----


def kf_idle_thinking(arm, cfg):
    """Chin rest, occasional code-look. 105f loop."""
    _rest(arm, 0)
    for fr in (0, 105):
        _kf(arm, "Spine", fr, (0.02, 0, 0))
        _kf(arm, "Head", fr, (0.05, 0.03, 0))
        _kf(arm, "LeftArm", fr, (-0.10, 0, 0.30))
        _kf(arm, "LeftForeArm", fr, (-0.80, 0.20, 0))
        _kf(arm, "RightArm", fr, (0, 0, -0.05))
        _kf(arm, "RightForeArm", fr, (-0.10, 0, 0))
    _kf(arm, "Head", 35, (0.03, -0.08, 0.02))
    _kf(arm, "Head", 70, (0.07, 0.05, -0.03))


def kf_idle_typing(arm, cfg):
    """Air typing, slow rhythm. 105f loop."""
    _rest(arm, 0)
    for fr in (0, 105):
        _kf(arm, "Spine", fr, (-0.03, 0, 0))
        _kf(arm, "LeftArm", fr, (-0.05, 0, 0.12))
        _kf(arm, "RightArm", fr, (-0.05, 0, -0.12))
        _kf(arm, "LeftForeArm", fr, (-0.50, 0, 0))
        _kf(arm, "RightForeArm", fr, (-0.50, 0, 0))
    _kf(arm, "LeftForeArm", 26, (-0.55, 0.03, 0.01))
    _kf(arm, "RightForeArm", 26, (-0.45, -0.03, -0.01))
    _kf(arm, "LeftForeArm", 52, (-0.45, -0.03, -0.01))
    _kf(arm, "RightForeArm", 52, (-0.55, 0.03, 0.01))
    _kf(arm, "LeftForeArm", 78, (-0.53, 0.02, 0))
    _kf(arm, "RightForeArm", 78, (-0.47, -0.02, 0))


def kf_idle_bounce(arm, cfg):
    """Beat-following bounce. 90f loop."""
    _rest(arm, 0)
    for fr in (0, 90):
        _kf(arm, "LeftArm", fr, (0, 0, 0.05))
        _kf(arm, "RightArm", fr, (0, 0, -0.05))
    for beat in (0, 30, 60, 90):
        _kf(arm, "Spine", beat, (0.03, 0, 0))
        _kf(arm, "Neck", beat, (0.02, 0, 0))
        _kf(arm, "LeftLeg", beat, (0.02, 0, 0))
        _kf(arm, "RightLeg", beat, (0.02, 0, 0))
    for beat in (15, 45, 75):
        _kf(arm, "Spine", beat, (-0.02, 0, 0))
        _kf(arm, "Neck", beat, (-0.03, 0, 0))
        _kf(arm, "LeftLeg", beat, (-0.01, 0, 0))
        _kf(arm, "RightLeg", beat, (-0.01, 0, 0))
    _kf(arm, "Head", 15, (0.04, 0, 0.02))
    _kf(arm, "Head", 45, (0.04, 0, -0.02))
    _kf(arm, "Head", 75, (0.04, 0, 0.02))


def kf_idle_sway(arm, cfg):
    """Relaxed left-right sway. 90f loop."""
    _rest(arm, 0)
    for fr in (0, 90):
        _kf(arm, "LeftArm", fr, (0, 0, 0.08))
        _kf(arm, "RightArm", fr, (0, 0, -0.08))
    _kf(arm, "Spine", 22, (0.01, 0, 0.04))
    _kf(arm, "Spine1", 22, (0, 0, 0.03))
    _kf(arm, "Head", 22, (0, 0.02, 0.02))
    _kf(arm, "Spine", 45, (0, 0, 0))
    _kf(arm, "Head", 45, (0, 0, 0))
    _kf(arm, "Spine", 67, (0.01, 0, -0.04))
    _kf(arm, "Spine1", 67, (0, 0, -0.03))
    _kf(arm, "Head", 67, (0, -0.02, -0.02))


def kf_idle_calm(arm, cfg):
    """Near-static, minimal breath. 120f loop."""
    _rest(arm, 0)
    for fr in (0, 120):
        _kf(arm, "Spine", fr, (0.005, 0, 0))
        _kf(arm, "LeftArm", fr, (0, 0, 0.02))
        _kf(arm, "RightArm", fr, (0, 0, -0.02))
    _kf(arm, "Spine", 60, (0.01, 0, 0))
    _kf(arm, "Head", 80, (0.0, 0.01, 0))


def kf_idle_engaged(arm, cfg):
    """Forward lean, hands gripping. 105f loop."""
    _rest(arm, 0)
    for fr in (0, 105):
        _kf(arm, "Spine", fr, (-0.05, 0, 0))
        _kf(arm, "Spine1", fr, (-0.03, 0, 0))
        _kf(arm, "LeftArm", fr, (-0.15, 0, 0.25))
        _kf(arm, "RightArm", fr, (-0.15, 0, -0.25))
        _kf(arm, "LeftForeArm", fr, (-0.85, 0.15, 0))
        _kf(arm, "RightForeArm", fr, (-0.85, -0.15, 0))
        _kf(arm, "LeftHand", fr, (-0.05, 0, 0))
        _kf(arm, "RightHand", fr, (-0.05, 0, 0))
    _kf(arm, "Head", 35, (0.05, -0.04, 0.02))
    _kf(arm, "Head", 70, (0.03, 0.04, -0.02))


# ---- §3.4 Movement animations (MUST walk / SHOULD others, 5) ----


def kf_walk(arm, cfg):
    """Forward walk cycle. 36f loop."""
    _rest(arm, 0)
    for fr in (0, 36):
        _kf(arm, "LeftArm", fr, (0, 0, 0.03))
        _kf(arm, "RightArm", fr, (0, 0, -0.03))
        _kf(arm, "LeftForeArm", fr, (-0.20, 0, 0))
        _kf(arm, "RightForeArm", fr, (-0.20, 0, 0))
    _kf(arm, "LeftUpLeg", 9, (0.35, 0, 0))
    _kf(arm, "LeftLeg", 9, (-0.50, 0, 0))
    _kf(arm, "RightUpLeg", 9, (-0.15, 0, 0))
    _kf(arm, "RightLeg", 9, (0.10, 0, 0))
    _kf(arm, "Hips", 9, (-0.02, 0, 0))
    _kf(arm, "Spine", 9, (-0.03, 0, 0))
    _kf(arm, "LeftArm", 9, (0, 0.15, 0.05))
    _kf(arm, "RightArm", 9, (0, 0.15, -0.05))
    _kf(arm, "LeftForeArm", 9, (-0.40, 0, 0))
    _kf(arm, "RightForeArm", 9, (-0.15, 0, 0))
    _kf(arm, "LeftUpLeg", 18, (-0.15, 0, 0))
    _kf(arm, "LeftLeg", 18, (0.10, 0, 0))
    _kf(arm, "RightUpLeg", 18, (0.35, 0, 0))
    _kf(arm, "RightLeg", 18, (-0.50, 0, 0))
    _kf(arm, "Hips", 18, (0.02, 0, 0))
    _kf(arm, "LeftArm", 18, (0, 0.15, 0.05))
    _kf(arm, "RightArm", 18, (0, 0.15, -0.05))
    _kf(arm, "LeftForeArm", 18, (-0.15, 0, 0))
    _kf(arm, "RightForeArm", 18, (-0.40, 0, 0))
    _kf(arm, "LeftUpLeg", 27, (-0.05, 0, 0))
    _kf(arm, "RightUpLeg", 27, (-0.05, 0, 0))


def kf_idle_to_walk(arm, cfg):
    """Idle to walk transition. 15f oneshot."""
    _rest(arm, 0)
    _kf(arm, "Spine", 7, (-0.02, 0, 0))
    _kf(arm, "LeftUpLeg", 7, (0.10, 0, 0))
    _kf(arm, "RightArm", 7, (0, 0.05, -0.05))
    _rest(arm, 15)


def kf_walk_to_idle(arm, cfg):
    """Walk to idle transition. 15f oneshot."""
    _rest(arm, 0)
    _kf(arm, "LeftUpLeg", 7, (0.05, 0, 0))
    _kf(arm, "RightLeg", 7, (-0.05, 0, 0))
    _kf(arm, "Spine", 7, (-0.01, 0, 0))
    _rest(arm, 15)


def kf_fly(arm, cfg):
    """Flight cycle — arms out, gentle bob. 75f loop."""
    _rest(arm, 0)
    for fr in (0, 75):
        _kf(arm, "LeftArm", fr, (-0.15, 0, 0.80))
        _kf(arm, "RightArm", fr, (-0.15, 0, -0.80))
        _kf(arm, "LeftForeArm", fr, (-0.10, 0, 0))
        _kf(arm, "RightForeArm", fr, (-0.10, 0, 0))
        _kf(arm, "LeftUpLeg", fr, (-0.05, 0, 0.10))
        _kf(arm, "RightUpLeg", fr, (-0.05, 0, -0.10))
    _kf(arm, "Spine", 18, (0.03, 0, 0))
    _kf(arm, "Neck", 18, (0.02, 0, 0))
    _kf(arm, "LeftArm", 37, (-0.20, 0, 0.90))
    _kf(arm, "RightArm", 37, (-0.20, 0, -0.90))
    _kf(arm, "Spine", 56, (0.01, 0, 0))
    _kf(arm, "LeftArm", 56, (-0.10, 0, 0.70))


def kf_drag(arm, cfg):
    """Hanging pose — body下垂, limbs relaxed. 15f oneshot."""
    _rest(arm, 0)
    _kf(arm, "Spine", 7, (0.15, 0, 0))
    _kf(arm, "Spine1", 7, (0.10, 0, 0))
    _kf(arm, "LeftArm", 7, (0.40, 0, 0.15))
    _kf(arm, "RightArm", 7, (0.40, 0, -0.15))
    _kf(arm, "LeftForeArm", 7, (-0.05, 0, 0))
    _kf(arm, "RightForeArm", 7, (-0.05, 0, 0))
    _kf(arm, "Head", 7, (0.20, 0, 0))
    _rest(arm, 15)


# ---- §3.5 Interaction reactions (SHOULD, 4) ----


def kf_poke_reaction_light(arm, cfg):
    """Slight head turn, quizzical. 24f oneshot."""
    _rest(arm, 0)
    _kf(arm, "Spine2", 6, (0, 0.04, 0))
    _kf(arm, "Head", 6, (-0.05, 0.08, 0.03))
    _kf(arm, "Neck", 6, (-0.02, 0.05, 0.02))
    _kf(arm, "Spine", 12, (-0.01, 0, 0))
    _kf(arm, "Head", 12, (-0.02, 0.03, 0.01))
    _rest(arm, 24)


def kf_poke_reaction_heavy(arm, cfg):
    """Big bounce, startled. 36f oneshot."""
    _rest(arm, 0)
    _kf(arm, "Spine", 6, (-0.10, 0, 0))
    _kf(arm, "Spine1", 6, (-0.06, 0, 0))
    _kf(arm, "Head", 6, (-0.12, 0.10, 0))
    _kf(arm, "LeftArm", 6, (0.05, 0, 0.20))
    _kf(arm, "RightArm", 6, (0.05, 0, -0.20))
    _kf(arm, "LeftForeArm", 6, (-0.40, 0, 0))
    _kf(arm, "RightForeArm", 6, (-0.40, 0, 0))
    _kf(arm, "Spine", 14, (-0.04, 0, 0))
    _kf(arm, "Head", 14, (-0.05, 0.05, 0))
    _kf(arm, "LeftArm", 14, (0.02, 0, 0.10))
    _kf(arm, "RightArm", 14, (0.02, 0, -0.10))
    _kf(arm, "Spine", 24, (-0.01, 0, 0))
    _rest(arm, 36)


def kf_poke_reaction_happy(arm, cfg):
    """Happy turn, smile. 30f oneshot."""
    _rest(arm, 0)
    _kf(arm, "Spine2", 8, (0, -0.06, 0))
    _kf(arm, "Head", 8, (-0.05, -0.10, -0.03))
    _kf(arm, "Neck", 8, (-0.02, -0.06, -0.02))
    _kf(arm, "RightArm", 8, (-0.10, 0, -0.30))
    _kf(arm, "RightForeArm", 8, (-0.30, 0, 0))
    _kf(arm, "Spine2", 16, (0, -0.03, 0))
    _kf(arm, "Head", 16, (-0.02, -0.05, -0.01))
    _rest(arm, 30)


def kf_drag_end(arm, cfg):
    """Landing bounce, recover. 24f oneshot."""
    _rest(arm, 0)
    _kf(arm, "Spine", 5, (0.08, 0, 0))
    _kf(arm, "Spine1", 5, (0.05, 0, 0))
    _kf(arm, "LeftLeg", 5, (0.08, 0, 0))
    _kf(arm, "RightLeg", 5, (0.08, 0, 0))
    _kf(arm, "LeftArm", 5, (0.10, 0, 0.10))
    _kf(arm, "RightArm", 5, (0.10, 0, -0.10))
    _kf(arm, "Spine", 12, (0.03, 0, 0))
    _kf(arm, "LeftLeg", 12, (0.03, 0, 0))
    _kf(arm, "RightLeg", 12, (0.03, 0, 0))
    _rest(arm, 24)


# ---- §3.6 Ceremonial animations (SHOULD, 3) ----


def kf_greeting(arm, cfg):
    """Wave + smile. 75f oneshot."""
    _rest(arm, 0)
    _kf(arm, "RightArm", 15, (-1.30, 0, -0.35))
    _kf(arm, "RightForeArm", 15, (-0.30, 0, 0))
    _kf(arm, "Head", 15, (-0.03, -0.08, 0))
    _kf(arm, "Spine2", 15, (0, -0.03, 0))
    _kf(arm, "RightForeArm", 30, (-0.30, 0.60, 0))
    _kf(arm, "RightForeArm", 45, (-0.30, -0.50, 0))
    _kf(arm, "RightForeArm", 55, (-0.30, 0.30, 0))
    _kf(arm, "Head", 55, (-0.02, -0.04, 0))
    _rest(arm, 75)


def kf_goodbye(arm, cfg):
    """Light wave + watch. 60f oneshot."""
    _rest(arm, 0)
    _kf(arm, "RightArm", 15, (-0.80, 0, -0.30))
    _kf(arm, "RightForeArm", 15, (-0.40, 0, 0))
    _kf(arm, "Head", 15, (-0.02, -0.06, 0))
    _kf(arm, "RightForeArm", 25, (-0.40, 0.35, 0))
    _kf(arm, "RightForeArm", 35, (-0.40, -0.30, 0))
    _kf(arm, "RightArm", 45, (-0.20, 0, -0.15))
    _kf(arm, "RightForeArm", 45, (-0.20, 0, 0))
    _kf(arm, "Head", 50, (-0.01, -0.03, 0))
    _rest(arm, 60)


def kf_wake_up(arm, cfg):
    """Rub eyes, stretch, come to. 75f oneshot."""
    _rest(arm, 0)
    _kf(arm, "Spine", 10, (-0.05, 0, 0))
    _kf(arm, "Head", 10, (0.15, 0, 0))
    _kf(arm, "RightArm", 10, (-0.60, 0, -0.65))
    _kf(arm, "RightForeArm", 10, (-1.10, 0.35, 0))
    _kf(arm, "LeftArm", 10, (-0.60, 0, 0.65))
    _kf(arm, "LeftForeArm", 10, (-1.10, -0.35, 0))
    _kf(arm, "Head", 30, (0.05, 0, 0))
    _kf(arm, "RightArm", 30, (-0.20, 0, 0.30))
    _kf(arm, "LeftArm", 30, (-0.20, 0, -0.30))
    _kf(arm, "RightForeArm", 30, (-0.10, 0, 0))
    _kf(arm, "LeftForeArm", 30, (-0.10, 0, 0))
    _kf(arm, "Spine", 50, (0.03, 0, 0))
    _kf(arm, "RightArm", 50, (-0.10, 0, 0.10))
    _kf(arm, "LeftArm", 50, (-0.10, 0, -0.10))
    _rest(arm, 75)


# ---- Registry: all 31 clips ----

CLIPS = [
    ("idle", kf_idle),
    ("listening", kf_listening),
    ("thinking", kf_thinking),
    ("speaking", kf_speaking),
    ("working", kf_working),
    ("sleeping", kf_sleeping),
    ("interacting", kf_interacting),
    ("emotional_idle", kf_emotional_idle),
    ("disconnected", kf_disconnected),
    ("walk", kf_walk),
    ("idle_look_around", kf_idle_look_around),
    ("idle_blink", kf_idle_blink),
    ("idle_stretch", kf_idle_stretch),
    ("idle_shift_weight", kf_idle_shift_weight),
    ("idle_thinking", kf_idle_thinking),
    ("idle_typing", kf_idle_typing),
    ("idle_bounce", kf_idle_bounce),
    ("idle_sway", kf_idle_sway),
    ("idle_calm", kf_idle_calm),
    ("idle_engaged", kf_idle_engaged),
    ("idle_to_walk", kf_idle_to_walk),
    ("walk_to_idle", kf_walk_to_idle),
    ("fly", kf_fly),
    ("drag", kf_drag),
    ("poke_reaction_light", kf_poke_reaction_light),
    ("poke_reaction_heavy", kf_poke_reaction_heavy),
    ("poke_reaction_happy", kf_poke_reaction_happy),
    ("drag_end", kf_drag_end),
    ("greeting", kf_greeting),
    ("goodbye", kf_goodbye),
    ("wake_up", kf_wake_up),
]


def create_animations(armature, config):
    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    for clip_name, kfn in CLIPS:
        action = bpy.data.actions.new(clip_name)
        if armature.animation_data is None:
            armature.animation_data_create()
        armature.animation_data.action = action
        kfn(armature, config)
        track = armature.animation_data.nla_tracks.new()
        track.name = clip_name
        track.strips.new(clip_name, 0, action)
    armature.animation_data.action = None


# ====================================================================
# Export & verify
# ====================================================================


def export_glb(filepath):
    bpy.ops.export_scene.gltf(
        filepath=str(filepath),
        export_format="GLB",
        export_yup=True,
        export_apply=False,
        export_materials="EXPORT",
        export_cameras=False,
        export_lights=False,
        export_animations=True,
        export_animation_mode="ACTIONS",
        export_skins=True,
        export_morph=True,
        export_morph_normal=True,
        export_morph_tangent=False,
        export_morph_animation=False,
    )


def verify_glb(filepath):
    with open(filepath, "rb") as f:
        data = f.read()
    size_mb = len(data) / (1024 * 1024)
    if len(data) < 20 or int.from_bytes(data[0:4], "little") != 0x46546C67:
        print(f"  ERROR: not a valid GLB")
        return
    json_len = int.from_bytes(data[12:16], "little")
    js = data[20 : 20 + json_len].decode("utf-8", errors="ignore").rstrip("\x00 \n\r,")
    gltf = json.loads(js)
    meshes = gltf.get("meshes", [])
    animations = gltf.get("animations", [])
    skins = gltf.get("skins", [])
    accessors = gltf.get("accessors", [])
    morph_names = set()
    for m in meshes:
        morph_names.update(m.get("extras", {}).get("targetNames", []))
        for p in m.get("primitives", []):
            morph_names.update(p.get("extras", {}).get("targetNames", []))
    anim_names = [a.get("name", f"anim_{i}") for i, a in enumerate(animations)]
    tex_count = len(gltf.get("textures", []))
    total_tris = 0
    for m in meshes:
        for p in m.get("primitives", []):
            idx = p.get("indices")
            if idx is not None and idx < len(accessors):
                total_tris += accessors[idx].get("count", 0) // 3
    joint_count = len(skins[0]["joints"]) if skins else 0
    tex_count = len(gltf.get("textures", []))
    print(f"  Size:       {size_mb:.2f} MB")
    print(f"  Clips:      {len(anim_names)}")
    print(f"  Triangles:  ~{total_tris:,}")
    print(f"  Joints:     {joint_count}")
    print(f"  Textures:   {tex_count}")
    print(f"  Animations: {anim_names}")
    print(f"  Morphs({len(morph_names)}): {sorted(morph_names)}")


# ====================================================================
# Main
# ====================================================================


def generate_species(name):
    config = SPECIES[name]
    print(f"\n{'='*60}\n  Generating {name}.glb\n{'='*60}")
    clean_scene()
    body = build_body_mesh(config)
    apply_subsurf(body, config["subsurf"])
    add_face_features(body, config)
    add_uv_projection(body)
    add_morph_targets(body, config)
    armature = build_armature(config)
    eyes = create_eye_objects(config, armature)
    assign_weights(body, armature)
    mod = body.modifiers.new("Armature", "ARMATURE")
    mod.object = armature
    body.parent = armature
    create_materials(body, eyes, config)
    create_animations(armature, config)
    # Fix facing: character built facing -Y (Blender), glTF Y-up maps
    # -Y to +Z. Spec wants -Z, so rotate root 180 deg around Z.
    root_empty = bpy.data.objects.new("Root", None)
    bpy.context.collection.objects.link(root_empty)
    root_empty.rotation_euler = (0, 0, math.pi)
    armature.parent = root_empty
    filepath = os.path.join(OUTPUT_DIR, f"{name}.glb")
    export_glb(filepath)
    print(f"  Exported: {filepath}")
    verify_glb(filepath)


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    print(f"Output directory: {OUTPUT_DIR}")
    for name in SPECIES:
        try:
            generate_species(name)
        except Exception as exc:
            print(f"\n!!! ERROR generating {name}: {exc}")
            import traceback

            traceback.print_exc()
    human_path = os.path.join(OUTPUT_DIR, "human.glb")
    fallback_path = os.path.join(OUTPUT_DIR, "character.glb")
    if os.path.exists(human_path):
        shutil.copy2(human_path, fallback_path)
        print(f"\nCopied human.glb -> character.glb")
    print(f"\nDone. Files in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()

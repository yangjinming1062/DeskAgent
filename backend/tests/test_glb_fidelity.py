import json
import struct

import numpy as np
import pytest
from services.companion.glb_fidelity import (
    GlbFidelityError,
    _accessor,
    _copy_accessor,
    _parse_glb,
    add_source_vertex_uv,
    assert_preserves_display,
    restore_preserved_vertex_attributes,
)


def _pack(gltf: dict, binary: bytes) -> bytes:
    json_bytes = json.dumps(gltf, separators=(",", ":")).encode()
    json_bytes += b" " * ((-len(json_bytes)) % 4)
    binary = binary + b"\x00" * ((-len(binary)) % 4)
    return (
        struct.pack("<III", 0x46546C67, 2, 28 + len(json_bytes) + len(binary))
        + struct.pack("<II", len(json_bytes), 0x4E4F534A)
        + json_bytes
        + struct.pack("<II", len(binary), 0x004E4942)
        + binary
    )


def _source_glb() -> bytes:
    positions = np.array([(0, 0, 0), (1, 0, 0), (0, 1, 0)], dtype="<f4")
    normals = np.array([(0, 0, 1), (0, 0, 2), (0, 0, 3)], dtype="<f4")
    uv = np.array([(0, 0), (0.5, 0), (0, 0.5)], dtype="<f4")
    uv1 = np.array([(1, 0), (0.5, 1), (1, 0.5)], dtype="<f4")
    indices = np.array([0, 1, 2], dtype="<u2")
    binary = positions.tobytes() + normals.tobytes() + uv.tobytes() + uv1.tobytes() + indices.tobytes()
    offsets = [0, 36, 72, 96]
    gltf = {
        "asset": {"version": "2.0"},
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {
                            "POSITION": 0,
                            "NORMAL": 1,
                            "TEXCOORD_0": 2,
                            "TEXCOORD_1": 3,
                        },
                        "indices": 4,
                        "material": 0,
                    },
                ],
            },
        ],
        "materials": [{"alphaMode": "MASK"}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5126, "count": 3, "type": "VEC2"},
            {"bufferView": 3, "componentType": 5126, "count": 3, "type": "VEC2"},
            {"bufferView": 4, "componentType": 5123, "count": 3, "type": "SCALAR"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": offsets[0], "byteLength": 36},
            {"buffer": 0, "byteOffset": offsets[1], "byteLength": 36},
            {"buffer": 0, "byteOffset": offsets[2], "byteLength": 24},
            {"buffer": 0, "byteOffset": offsets[3], "byteLength": 24},
            {"buffer": 0, "byteOffset": 120, "byteLength": 6},
        ],
        "buffers": [{"byteLength": len(binary)}],
    }
    return _pack(gltf, binary)


def _processed_reorder(source: bytes, *, by_source_uv: bool = False) -> bytes:
    gltf, _binary = _parse_glb(source)
    positions = np.array([(1, 0, 0), (0, 1, 0), (0, 0, 0)], dtype="<f4")
    source_uv = np.array([(0, 0), (0.5, 0), (0, 0.5)], dtype="<f4")
    mapping = source_uv[[1, 2, 0]] if by_source_uv else np.array([(1, 1), (2, 1), (0, 1)], dtype="<f4")
    mapping_name = "TEXCOORD_0" if by_source_uv else "TEXCOORD_2"
    joints = np.array([(1, 0, 0, 0), (2, 0, 0, 0), (0, 0, 0, 0)], dtype="<u1")
    weights = np.array([(0.2, 0, 0, 0), (0.3, 0, 0, 0), (0.5, 0, 0, 0)], dtype="<f4")
    indices = np.array([2, 0, 1], dtype="<u2")
    payload = positions.tobytes() + mapping.tobytes() + joints.tobytes() + weights.tobytes() + indices.tobytes()
    offsets = [0, 36, 60, 72, 120]
    gltf["meshes"][0]["primitives"][0] = {
        "attributes": {"POSITION": 0, mapping_name: 1, "JOINTS_0": 2, "WEIGHTS_0": 3},
        "indices": 4,
        "material": 0,
    }
    gltf["materials"] = [{"alphaMode": "BLEND"}]
    gltf["accessors"] = [
        {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
        {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC2"},
        {"bufferView": 2, "componentType": 5121, "count": 3, "type": "VEC4"},
        {"bufferView": 3, "componentType": 5126, "count": 3, "type": "VEC4"},
        {"bufferView": 4, "componentType": 5123, "count": 3, "type": "SCALAR"},
    ]
    gltf["bufferViews"] = [
        {"buffer": 0, "byteOffset": offsets[0], "byteLength": 36},
        {"buffer": 0, "byteOffset": offsets[1], "byteLength": 24},
        {"buffer": 0, "byteOffset": offsets[2], "byteLength": 12},
        {"buffer": 0, "byteOffset": offsets[3], "byteLength": 48},
        {"buffer": 0, "byteOffset": offsets[4], "byteLength": 6},
    ]
    gltf["buffers"] = [{"byteLength": len(payload)}]
    return _pack(gltf, payload)


def _attribute_values(glb: bytes, name: str, dtype: str, count: int, components: int = 1):
    gltf, binary = _parse_glb(glb)
    accessor_index = gltf["meshes"][0]["primitives"][0]["attributes"][name]
    view = gltf["bufferViews"][gltf["accessors"][accessor_index]["bufferView"]]
    return np.frombuffer(binary, dtype=dtype, count=count * components, offset=view["byteOffset"])


def test_source_mapping_selects_free_uv_and_restores_vertex_order():
    source = _source_glb()
    tagged = add_source_vertex_uv(source)
    tagged_attributes = _parse_glb(tagged)[0]["meshes"][0]["primitives"][0]["attributes"]
    assert "TEXCOORD_2" in tagged_attributes

    restored = restore_preserved_vertex_attributes(source, _processed_reorder(source))
    attributes = _parse_glb(restored)[0]["meshes"][0]["primitives"][0]["attributes"]
    assert "TEXCOORD_2" not in attributes
    assert _attribute_values(restored, "POSITION", "<f4", 3, 3).reshape(-1, 3)[:, 0].tolist() == [0, 1, 0]
    assert _attribute_values(restored, "NORMAL", "<f4", 3, 3).reshape(-1, 3)[:, 2].tolist() == [1, 2, 3]
    assert _attribute_values(restored, "JOINTS_0", "|u1", 3, 4).reshape(-1, 4)[:, 0].tolist() == [0, 1, 2]
    assert _attribute_values(restored, "WEIGHTS_0", "<f4", 3, 4).reshape(-1, 4)[:, 0].tolist() == pytest.approx([0.5, 0.2, 0.3])
    source_positions = _attribute_values(source, "POSITION", "<f4", 3, 3)
    assert np.array_equal(_attribute_values(restored, "POSITION", "<f4", 3, 3), source_positions)
    source_gltf, _ = _parse_glb(source)
    restored_gltf, _ = _parse_glb(restored)
    source_gltf, source_binary = _parse_glb(source)
    restored_gltf, restored_binary = _parse_glb(restored)
    source_indices = _accessor(source_gltf, source_binary, source_gltf["meshes"][0]["primitives"][0]["indices"])[0]
    restored_indices = _accessor(
        restored_gltf,
        restored_binary,
        restored_gltf["meshes"][0]["primitives"][0]["indices"],
    )[0]
    assert np.array_equal(restored_indices, source_indices)
    assert _parse_glb(restored)[0]["materials"] == [{"alphaMode": "MASK"}]
    assert_preserves_display(source, restored)


def test_matching_source_uv_restores_vertex_order_without_temp_channel():
    source = _source_glb()
    restored = restore_preserved_vertex_attributes(source, _processed_reorder(source, by_source_uv=True))

    assert _attribute_values(restored, "POSITION", "<f4", 3, 3).reshape(-1, 3)[:, 0].tolist() == [0, 1, 0]
    assert _attribute_values(restored, "NORMAL", "<f4", 3, 3).reshape(-1, 3)[:, 2].tolist() == [1, 2, 3]
    assert _attribute_values(restored, "JOINTS_0", "|u1", 3, 4).reshape(-1, 4)[:, 0].tolist() == [0, 1, 2]
    assert _attribute_values(restored, "WEIGHTS_0", "<f4", 3, 4).reshape(-1, 4)[:, 0].tolist() == pytest.approx([0.5, 0.2, 0.3])
    assert_preserves_display(source, restored)


def test_accessor_supports_interleaved_buffers_and_sparse_values():
    base = np.zeros((3, 6), dtype="<f4")
    base[:, 0] = [0, 1, 2]
    base[:, 3] = [0, 1, 2]
    sparse_indices = np.array([2], dtype="<u2")
    sparse_values = np.array([9], dtype="<f4")
    binary = base.tobytes() + sparse_indices.tobytes() + sparse_values.tobytes()
    gltf = {
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 72, "byteStride": 24},
            {"buffer": 0, "byteOffset": 72, "byteLength": 2},
            {"buffer": 0, "byteOffset": 74, "byteLength": 4},
        ],
        "accessors": [
            {
                "bufferView": 0,
                "byteOffset": 0,
                "componentType": 5126,
                "count": 3,
                "type": "SCALAR",
            },
            {
                "bufferView": 0,
                "byteOffset": 12,
                "componentType": 5126,
                "count": 3,
                "type": "SCALAR",
                "sparse": {
                    "count": 1,
                    "indices": {"bufferView": 1, "componentType": 5123},
                    "values": {"bufferView": 2},
                },
            },
        ],
    }

    assert _accessor(gltf, binary, 0)[0].tolist() == [0, 1, 2]
    assert _accessor(gltf, binary, 1)[0].tolist() == [0, 1, 9]


def test_sparse_accessor_reordering_stays_sparse():
    indices = np.array([1, 2], dtype="<u2")
    values = np.array([20, 30], dtype="<f4")
    binary = indices.tobytes() + values.tobytes()
    gltf = {
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": 4},
            {"buffer": 0, "byteOffset": 4, "byteLength": 8},
        ],
        "accessors": [
            {
                "componentType": 5126,
                "count": 3,
                "type": "SCALAR",
                "sparse": {
                    "count": 2,
                    "indices": {"bufferView": 0, "componentType": 5123},
                    "values": {"bufferView": 1},
                },
            },
        ],
    }
    target = {"accessors": [], "bufferViews": [], "buffers": [{"byteLength": 0}]}
    appended = bytearray()

    copied_index = _copy_accessor(gltf, binary, target, appended, 0, np.array([2, 0, 1]))
    target["buffers"][0]["byteLength"] = len(appended)

    assert target["accessors"][copied_index]["sparse"]["count"] == 2
    assert _accessor(target, bytes(appended), copied_index)[0].tolist() == [20, 30, 0]


def test_display_guard_rejects_changed_geometry():
    source = _source_glb()
    changed_gltf, changed_binary = _parse_glb(source)
    changed_gltf["meshes"][0]["primitives"][0]["material"] = None
    changed = _pack(changed_gltf, changed_binary)

    with pytest.raises(GlbFidelityError, match="material assignment"):
        assert_preserves_display(source, changed)

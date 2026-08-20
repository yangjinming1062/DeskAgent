import base64
import copy
import hashlib
import json
import re
import struct
from dataclasses import dataclass
from typing import Any

import numpy as np

from .asset_store import decompress_glb_if_needed

_COMPONENT_DTYPES = {5120: "<i1", 5121: "<u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
_COMPONENT_COUNTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
_TEXCOORD_ATTRIBUTE = re.compile(r"^TEXCOORD_(\d+)$")
_MAX_EXACT_FLOAT_INTEGER = 16_777_216


class GlbFidelityError(ValueError):
    """后处理阶段改动了受保护的展示数据。"""


@dataclass(frozen=True)
class _PrimitiveSignature:
    vertex_count: int
    triangles: bytes
    vertex_attributes: tuple[tuple[str, bytes], ...]
    material_index: int | None
    target_names: tuple[str, ...]


@dataclass(frozen=True)
class _GlbSignature:
    primitives: tuple[_PrimitiveSignature, ...]
    materials: tuple[str, ...]
    textures: tuple[str, ...]
    samplers: tuple[str, ...]
    images: tuple[bytes, ...]


def _parse_glb(data: bytes) -> tuple[dict[str, Any], bytes]:
    raw = decompress_glb_if_needed(data)
    if len(raw) < 20 or raw[:4] != b"glTF":
        raise GlbFidelityError("input is not a GLB")
    json_length = struct.unpack_from("<I", raw, 12)[0]
    if raw[16:20] != b"JSON" or 20 + json_length > len(raw):
        raise GlbFidelityError("GLB JSON chunk is malformed")
    try:
        gltf = json.loads(raw[20 : 20 + json_length])
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GlbFidelityError(f"GLB JSON chunk is malformed: {error}") from error

    binary = b""
    offset = 20 + json_length + ((-json_length) % 4)
    if offset + 8 <= len(raw):
        chunk_length, chunk_type = struct.unpack_from("<II", raw, offset)
        if chunk_type == 0x004E4942:
            if offset + 8 + chunk_length > len(raw):
                raise GlbFidelityError("GLB binary chunk is truncated")
            binary = raw[offset + 8 : offset + 8 + chunk_length]
    return gltf, binary


def _view_bytes(gltf: dict[str, Any], binary: bytes, view_index: int) -> bytes:
    try:
        view = gltf["bufferViews"][view_index]
        buffer_index = view.get("buffer", 0)
        if buffer_index != 0 or gltf["buffers"][buffer_index].get("uri") is not None:
            raise GlbFidelityError("GLB fidelity requires mesh data in the embedded GLB buffer")
        start = view.get("byteOffset", 0)
        end = start + view.get("byteLength", 0)
        if start < 0 or end > len(binary):
            raise GlbFidelityError("GLB bufferView is out of bounds")
    except (KeyError, IndexError, TypeError) as error:
        raise GlbFidelityError("GLB bufferView is malformed") from error
    return binary[start:end]


def _accessor(gltf: dict[str, Any], binary: bytes, accessor_index: int) -> tuple[np.ndarray, dict[str, Any]]:
    try:
        accessor = gltf["accessors"][accessor_index]
        count = accessor["count"]
        dtype = np.dtype(_COMPONENT_DTYPES[accessor["componentType"]])
        component_count = _COMPONENT_COUNTS[accessor["type"]]
    except (KeyError, IndexError, TypeError) as error:
        raise GlbFidelityError("GLB accessor is malformed") from error
    if count < 0:
        raise GlbFidelityError("GLB accessor count is negative")

    element_size = dtype.itemsize * component_count
    if "bufferView" not in accessor:
        values = np.zeros(count * component_count, dtype=dtype)
    else:
        view = gltf["bufferViews"][accessor["bufferView"]]
        stride = int(view.get("byteStride", element_size))
        start = view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        if stride < element_size or start < 0:
            raise GlbFidelityError("GLB accessor layout is invalid")
        byte_count = max(0, (count - 1) * stride + element_size)
        try:
            raw = np.frombuffer(binary, dtype=np.uint8, count=byte_count, offset=start)
        except ValueError as error:
            raise GlbFidelityError("GLB accessor is out of bounds") from error
        if byte_count:
            rows = np.lib.stride_tricks.as_strided(raw, shape=(count, element_size), strides=(stride, 1))
            values = np.ascontiguousarray(rows).reshape(-1).view(dtype)
        else:
            values = np.empty(0, dtype=dtype)

    sparse = accessor.get("sparse")
    if sparse is not None:
        sparse_count = sparse["count"]
        index_dtype = np.dtype(_COMPONENT_DTYPES[sparse["indices"]["componentType"]])
        indices = np.frombuffer(_view_bytes(gltf, binary, sparse["indices"]["bufferView"]), dtype=index_dtype, count=sparse_count).astype(np.int64)
        value_dtype = np.dtype(_COMPONENT_DTYPES[accessor["componentType"]])
        sparse_values = np.frombuffer(_view_bytes(gltf, binary, sparse["values"]["bufferView"]), dtype=value_dtype, count=sparse_count * component_count)
        if indices.min(initial=0) < 0 or indices.max(initial=-1) >= count or np.unique(indices).size != indices.size:
            raise GlbFidelityError("GLB sparse accessor indices are invalid")
        values = values.reshape(count, component_count)
        values[indices] = sparse_values.reshape(sparse_count, component_count)

    return values.reshape(-1), accessor


def _triangle_signature(gltf: dict[str, Any], binary: bytes, primitive: dict[str, Any], positions: np.ndarray) -> bytes:
    if primitive.get("mode", 4) != 4:
        raise GlbFidelityError("companion GLB fidelity only supports TRIANGLES primitives")
    indices_index = primitive.get("indices")
    if indices_index is None:
        indices = np.arange(len(positions), dtype=np.int64)
    else:
        indices = _accessor(gltf, binary, indices_index)[0].astype(np.int64, copy=False)
    if len(indices) % 3 or indices.min(initial=0) < 0 or indices.max(initial=-1) >= len(positions):
        raise GlbFidelityError("mesh primitive has invalid triangle indices")

    triangles = indices.reshape(-1)
    return triangles.tobytes()


def _image_bytes(gltf: dict[str, Any], binary: bytes, image: dict[str, Any]) -> bytes:
    uri = image.get("uri")
    if isinstance(uri, str) and uri.startswith("data:"):
        payload = uri.split(",", 1)[1] if "," in uri else ""
        try:
            return base64.b64decode(payload, validate=True)
        except (ValueError, base64.binascii.Error) as error:
            raise GlbFidelityError("GLB embedded image payload is malformed") from error
    view_index = image.get("bufferView")
    if view_index is None:
        return uri.encode("utf-8") if isinstance(uri, str) else b""
    return _view_bytes(gltf, binary, view_index)


def _target_names(mesh: dict[str, Any], primitive: dict[str, Any]) -> tuple[str, ...]:
    names = primitive.get("extras", {}).get("targetNames", mesh.get("extras", {}).get("targetNames", []))
    return tuple(str(name) for name in names)


def _signature(data: bytes) -> _GlbSignature:
    gltf, binary = _parse_glb(data)
    primitives: list[_PrimitiveSignature] = []
    for mesh in gltf.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            position_index = primitive.get("attributes", {}).get("POSITION")
            if position_index is None:
                raise GlbFidelityError("mesh primitive has no POSITION attribute")
            positions, accessor = _accessor(gltf, binary, position_index)
            positions = positions.reshape(-1, 3).astype(np.float64, copy=False)
            attributes = tuple(
                (name, _accessor(gltf, binary, accessor_index)[0].tobytes())
                for name, accessor_index in sorted(primitive.get("attributes", {}).items())
                if not name.startswith(("JOINTS_", "WEIGHTS_"))
            )
            primitives.append(
                _PrimitiveSignature(
                    vertex_count=accessor["count"],
                    triangles=_triangle_signature(gltf, binary, primitive, positions),
                    vertex_attributes=attributes,
                    material_index=primitive.get("material"),
                    target_names=_target_names(mesh, primitive),
                )
            )

    def canonical(value: list[dict[str, Any]]) -> tuple[str, ...]:
        return tuple(json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value)

    return _GlbSignature(
        primitives=tuple(primitives),
        materials=canonical(gltf.get("materials", [])),
        textures=canonical(gltf.get("textures", [])),
        samplers=canonical(gltf.get("samplers", [])),
        images=tuple(sorted(hashlib.sha256(_image_bytes(gltf, binary, image)).digest() for image in gltf.get("images", []))),
    )


def _compact_glb_buffer(gltf: dict[str, Any], binary: bytes) -> tuple[dict[str, Any], bytes]:
    """只把被引用的内嵌 bufferView 复制进新的二进制缓冲区。"""
    buffers = gltf.get("buffers", [])
    if len(buffers) != 1 or buffers[0].get("uri") is not None:
        raise GlbFidelityError("GLB compaction requires a single embedded buffer")
    compacted = bytearray()
    view_map: dict[int, int] = {}

    def copy_view(view_index: int) -> int:
        if view_index in view_map:
            return view_map[view_index]
        view = copy.deepcopy(gltf["bufferViews"][view_index])
        payload = _view_bytes(gltf, binary, view_index)
        compacted.extend(b"\\x00" * ((-len(compacted)) % 4))
        view.update({"buffer": 0, "byteOffset": len(compacted), "byteLength": len(payload)})
        compacted.extend(payload)
        gltf["bufferViews"].append(view)
        view_map[view_index] = len(gltf["bufferViews"]) - 1
        return view_map[view_index]

    accessor_indices: set[int] = set()
    for mesh in gltf.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            accessor_indices.update(primitive.get("attributes", {}).values())
            if "indices" in primitive:
                accessor_indices.add(primitive["indices"])
            for target in primitive.get("targets", []):
                accessor_indices.update(target.values())
    for skin in gltf.get("skins", []):
        if "inverseBindMatrices" in skin:
            accessor_indices.add(skin["inverseBindMatrices"])
    for animation in gltf.get("animations", []):
        for sampler in animation.get("samplers", []):
            accessor_indices.update(sampler.values())

    accessor_map = {accessor_index: new_index for new_index, accessor_index in enumerate(sorted(accessor_indices))}
    for mesh in gltf.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            primitive["attributes"] = {name: accessor_map[index] for name, index in primitive.get("attributes", {}).items()}
            if "indices" in primitive:
                primitive["indices"] = accessor_map[primitive["indices"]]
            primitive["targets"] = [{name: accessor_map[index] for name, index in target.items()} for target in primitive.get("targets", [])]
    for skin in gltf.get("skins", []):
        if "inverseBindMatrices" in skin:
            skin["inverseBindMatrices"] = accessor_map[skin["inverseBindMatrices"]]
    for animation in gltf.get("animations", []):
        for sampler in animation.get("samplers", []):
            for key, accessor_index in list(sampler.items()):
                sampler[key] = accessor_map[accessor_index]
    gltf["accessors"] = [copy.deepcopy(gltf["accessors"][index]) for index in sorted(accessor_indices)]

    for accessor in gltf["accessors"]:
        if "bufferView" in accessor:
            accessor["bufferView"] = copy_view(accessor["bufferView"])
        sparse = accessor.get("sparse")
        if sparse is not None:
            sparse["indices"]["bufferView"] = copy_view(sparse["indices"]["bufferView"])
            sparse["values"]["bufferView"] = copy_view(sparse["values"]["bufferView"])
    for image in gltf.get("images", []):
        if "bufferView" in image:
            image["bufferView"] = copy_view(image["bufferView"])

    gltf["bufferViews"] = [gltf["bufferViews"][index] for index in sorted(set(view_map.values()))]
    remap = {old: new for new, old in enumerate(sorted(view_map.values()))}
    for accessor in gltf.get("accessors", []):
        if "bufferView" in accessor:
            accessor["bufferView"] = remap[accessor["bufferView"]]
        sparse = accessor.get("sparse")
        if sparse is not None:
            sparse["indices"]["bufferView"] = remap[sparse["indices"]["bufferView"]]
            sparse["values"]["bufferView"] = remap[sparse["values"]["bufferView"]]
    for image in gltf.get("images", []):
        if "bufferView" in image:
            image["bufferView"] = remap[image["bufferView"]]
    buffers[0]["byteLength"] = len(compacted)
    return gltf, bytes(compacted)


def _pack_glb(gltf: dict[str, Any], binary: bytes) -> bytes:
    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((-len(json_bytes)) % 4)
    binary = binary + b"\x00" * ((-len(binary)) % 4)
    return b"".join(
        (
            struct.pack("<III", 0x46546C67, 2, 28 + len(json_bytes) + len(binary)),
            struct.pack("<II", len(json_bytes), 0x4E4F534A),
            json_bytes,
            struct.pack("<II", len(binary), 0x004E4942),
            binary,
        )
    )


def _append_view(gltf: dict[str, Any], appended: bytearray, data: bytes, byte_stride: int | None = None) -> int:
    appended.extend(b"\x00" * ((-len(appended)) % 4))
    offset = len(appended)
    appended.extend(data)
    view: dict[str, Any] = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
    if byte_stride is not None:
        view["byteStride"] = byte_stride
    gltf["bufferViews"].append(view)
    return len(gltf["bufferViews"]) - 1


def add_source_vertex_uv(source: bytes) -> bytes:
    """把源顶点索引编码进第一个空闲的临时 UV 通道。"""
    gltf, binary = _parse_glb(source)
    if not gltf.get("buffers") or gltf["buffers"][0].get("uri") is not None:
        raise GlbFidelityError("source vertex mapping requires an embedded GLB buffer")
    appended = bytearray(binary)
    for mesh in gltf.get("meshes", []):
        for primitive in mesh.get("primitives", []):
            attributes = primitive.setdefault("attributes", {})
            channels = {int(match.group(1)) for name in attributes if (match := _TEXCOORD_ATTRIBUTE.match(name))}
            channel = next(index for index in range(1, len(channels) + 2) if index not in channels)
            position_index = attributes.get("POSITION")
            if position_index is None:
                raise GlbFidelityError("mesh primitive has no POSITION attribute")
            count = gltf["accessors"][position_index]["count"]
            if count >= _MAX_EXACT_FLOAT_INTEGER:
                raise GlbFidelityError("mesh has too many vertices for an exact float32 source mapping")
            values = np.column_stack((np.arange(count, dtype="<f4"), np.ones(count, dtype="<f4"))).astype("<f4", copy=False)
            view_index = _append_view(gltf, appended, values.tobytes(), values.shape[1] * 4)
            gltf["accessors"].append({"bufferView": view_index, "componentType": 5126, "count": count, "type": "VEC2"})
            attributes[f"TEXCOORD_{channel}"] = len(gltf["accessors"]) - 1

    gltf["buffers"][0]["byteLength"] = len(appended)
    return _pack_glb(gltf, bytes(appended))


def _source_vertex_order(
    source_attributes: dict[str, int], processed_attributes: dict[str, int], processed_gltf: dict[str, Any], processed_binary: bytes, source_vertex_count: int
) -> tuple[str | None, np.ndarray | None]:
    for name, accessor_index in processed_attributes.items():
        if name in source_attributes or _TEXCOORD_ATTRIBUTE.fullmatch(name) is None:
            continue
        values, accessor = _accessor(processed_gltf, processed_binary, accessor_index)
        if accessor["type"] != "VEC2":
            continue
        values = values.reshape(-1, 2)
        if not (np.all(values[:, 1] == 1.0) or np.all(values[:, 1] == 0.0)):
            continue
        order = values[:, 0].astype(np.float64, copy=False)
        if not np.all(order == np.rint(order)):
            continue
        order = order.astype(np.int64, copy=False)
        if order.min(initial=0) >= 0 and order.max(initial=-1) < source_vertex_count and np.unique(order).size == len(order):
            return name, order
    return None, None


def _copy_accessor(
    gltf: dict[str, Any], binary: bytes, target_gltf: dict[str, Any], appended: bytearray, accessor_index: int, order: np.ndarray | None = None, index_map: np.ndarray | None = None
) -> int:
    accessor = gltf["accessors"][accessor_index]
    sparse = accessor.get("sparse")
    if sparse is not None and order is not None and "bufferView" not in accessor:
        index_metadata = sparse["indices"]
        index_dtype = np.dtype(_COMPONENT_DTYPES[index_metadata["componentType"]])
        old_indices = np.frombuffer(_view_bytes(gltf, binary, index_metadata["bufferView"]), dtype=index_dtype, count=sparse["count"]).astype(np.int64)
        if old_indices.min(initial=0) < 0 or old_indices.max(initial=-1) >= accessor["count"]:
            raise GlbFidelityError("GLB sparse accessor indices are invalid")
        new_indices = order[old_indices].astype(index_dtype.newbyteorder("<"))
        value_dtype = np.dtype(_COMPONENT_DTYPES[accessor["componentType"]])
        component_count = _COMPONENT_COUNTS[accessor["type"]]
        value_bytes = _view_bytes(gltf, binary, sparse["values"]["bufferView"])
        expected_value_bytes = sparse["count"] * component_count * value_dtype.itemsize
        if len(value_bytes) < expected_value_bytes:
            raise GlbFidelityError("GLB sparse accessor values are truncated")
        copied = copy.deepcopy(accessor)
        copied["sparse"] = {
            "count": sparse["count"],
            "indices": {"bufferView": _append_view(target_gltf, appended, new_indices.tobytes()), "componentType": index_metadata["componentType"]},
            "values": {"bufferView": _append_view(target_gltf, appended, value_bytes[:expected_value_bytes])},
        }
        target_gltf["accessors"].append(copied)
        return len(target_gltf["accessors"]) - 1

    values, accessor = _accessor(gltf, binary, accessor_index)
    values = values.reshape(accessor["count"], -1)
    if index_map is not None:
        values = index_map[values.astype(np.int64, copy=False)]
    elif order is not None:
        values = values[order]
    copied = copy.deepcopy(accessor)
    copied.pop("sparse", None)
    copied.pop("byteOffset", None)
    copied["bufferView"] = _append_view(target_gltf, appended, values.tobytes(), values.dtype.itemsize * values.shape[1])
    target_gltf["accessors"].append(copied)
    return len(target_gltf["accessors"]) - 1


def _copy_image_view(source_gltf: dict[str, Any], source_binary: bytes, processed_gltf: dict[str, Any], appended: bytearray, view_index: int) -> int:
    view = copy.deepcopy(source_gltf["bufferViews"][view_index])
    payload = _view_bytes(source_gltf, source_binary, view_index)
    copied_index = _append_view(processed_gltf, appended, payload)
    copied_view = processed_gltf["bufferViews"][copied_index]
    view.update({"buffer": 0, "byteOffset": copied_view["byteOffset"], "byteLength": len(payload)})
    view.pop("byteStride", None)
    processed_gltf["bufferViews"][copied_index] = view
    return copied_index


def _restore_targets(
    source_mesh: dict[str, Any],
    source_primitive: dict[str, Any],
    processed_mesh: dict[str, Any],
    processed_primitive: dict[str, Any],
    source_gltf: dict[str, Any],
    source_binary: bytes,
    processed_gltf: dict[str, Any],
    processed_binary: bytes,
    appended: bytearray,
    order: np.ndarray,
) -> None:
    source_targets = source_primitive.get("targets", [])
    processed_targets = processed_primitive.get("targets", [])
    if len(processed_targets) < len(source_targets):
        raise GlbFidelityError("post-processing removed morph targets")

    source_names = _target_names(source_mesh, source_primitive)
    processed_names = _target_names(processed_mesh, processed_primitive)
    if source_targets and not (source_names and processed_names):
        raise GlbFidelityError("morph target names are required to restore existing targets")
    restored: list[dict[str, int] | None] = [None] * len(processed_targets)
    for name, target in zip(source_names, source_targets):
        if name not in processed_names:
            raise GlbFidelityError("post-processing removed a named morph target")
        restored[processed_names.index(name)] = {
            attribute: _copy_accessor(source_gltf, source_binary, processed_gltf, appended, accessor_index) for attribute, accessor_index in target.items()
        }

    for index, target in enumerate(processed_targets):
        if restored[index] is None:
            restored[index] = {
                attribute: _copy_accessor(processed_gltf, processed_binary, processed_gltf, appended, accessor_index, order) for attribute, accessor_index in target.items()
            }
    processed_primitive["targets"] = restored


def restore_preserved_vertex_attributes(source: bytes, processed: bytes) -> bytes:
    """恢复源模型的展示属性，同时保留后处理新增的骨骼与 morph 数据。"""
    source_gltf, source_binary = _parse_glb(source)
    processed_gltf, processed_binary = _parse_glb(processed)
    appended = bytearray(processed_binary)
    source_meshes = source_gltf.get("meshes", [])
    processed_meshes = processed_gltf.get("meshes", [])
    if len(source_meshes) != len(processed_meshes):
        raise GlbFidelityError("mesh count changed while restoring attributes")

    for source_mesh, processed_mesh in zip(source_meshes, processed_meshes):
        source_primitives = source_mesh.get("primitives", [])
        processed_primitives = processed_mesh.get("primitives", [])
        if len(source_primitives) != len(processed_primitives):
            raise GlbFidelityError("primitive count changed while restoring attributes")
        for source_primitive, processed_primitive in zip(source_primitives, processed_primitives):
            source_attributes = source_primitive.get("attributes", {})
            processed_attributes = processed_primitive.get("attributes", {})
            source_position_index = source_attributes.get("POSITION")
            processed_position_index = processed_attributes.get("POSITION")
            if source_position_index is None or processed_position_index is None:
                raise GlbFidelityError("mesh primitive has no POSITION attribute")
            source_count = source_gltf["accessors"][source_position_index]["count"]
            mapping_attribute, processed_to_source = _source_vertex_order(source_attributes, processed_attributes, processed_gltf, processed_binary, source_count)
            if processed_to_source is None:
                if processed_gltf["accessors"][processed_position_index]["count"] != source_count:
                    raise GlbFidelityError("primitive vertex count changed without a source mapping")
                processed_to_source = np.arange(source_count, dtype=np.int64)
            if len(processed_to_source) != source_count:
                raise GlbFidelityError("source vertex mapping does not cover the source primitive")
            source_to_processed = np.empty(source_count, dtype=np.int64)
            source_to_processed[processed_to_source] = np.arange(source_count)

            restored_attributes: dict[str, int] = {}
            for name, accessor_index in processed_attributes.items():
                if name in source_attributes or name == mapping_attribute:
                    continue
                restored_attributes[name] = _copy_accessor(processed_gltf, processed_binary, processed_gltf, appended, accessor_index, source_to_processed)
            for name, accessor_index in source_attributes.items():
                restored_attributes[name] = _copy_accessor(source_gltf, source_binary, processed_gltf, appended, accessor_index)
            processed_primitive["attributes"] = restored_attributes
            if "material" in source_primitive or "material" in processed_primitive:
                processed_primitive["material"] = source_primitive.get("material")
            if "indices" in source_primitive:
                processed_primitive["indices"] = _copy_accessor(source_gltf, source_binary, processed_gltf, appended, source_primitive["indices"])
            else:
                processed_primitive.pop("indices", None)
            _restore_targets(
                source_mesh, source_primitive, processed_mesh, processed_primitive, source_gltf, source_binary, processed_gltf, processed_binary, appended, source_to_processed
            )

    restored_images: list[dict[str, Any]] = []
    for image in source_gltf.get("images", []):
        restored_image = copy.deepcopy(image)
        if "bufferView" in restored_image:
            restored_image["bufferView"] = _copy_image_view(source_gltf, source_binary, processed_gltf, appended, restored_image["bufferView"])
        restored_images.append(restored_image)
    if not processed_gltf.get("buffers"):
        raise GlbFidelityError("processed GLB has no embedded buffer")
    processed_gltf["images"] = restored_images
    processed_gltf["samplers"] = copy.deepcopy(source_gltf.get("samplers", []))
    processed_gltf["textures"] = copy.deepcopy(source_gltf.get("textures", []))
    processed_gltf["materials"] = copy.deepcopy(source_gltf.get("materials", []))
    processed_gltf["buffers"][0]["byteLength"] = len(appended)
    processed_gltf, compacted_binary = _compact_glb_buffer(processed_gltf, bytes(appended))
    return _pack_glb(processed_gltf, compacted_binary)


def assert_preserves_display(source: bytes, processed: bytes) -> None:
    """若后处理删除或改写了受保护的展示数据则抛错。"""
    try:
        source_signature = _signature(source)
        processed_signature = _signature(processed)
    except (KeyError, IndexError, TypeError, ValueError, struct.error) as error:
        raise GlbFidelityError(f"GLB fidelity signature failed: {error}") from error

    if len(source_signature.primitives) != len(processed_signature.primitives):
        raise GlbFidelityError("mesh primitive count changed")
    for index, (before, after) in enumerate(zip(source_signature.primitives, processed_signature.primitives)):
        if before.vertex_count != after.vertex_count:
            raise GlbFidelityError(f"primitive {index} vertex count changed")
        if before.triangles != after.triangles:
            raise GlbFidelityError(f"primitive {index} triangle topology changed")
        if before.vertex_attributes != after.vertex_attributes:
            raise GlbFidelityError(f"primitive {index} preserved vertex attributes changed")
        if before.material_index != after.material_index:
            raise GlbFidelityError(f"primitive {index} material assignment changed")
        if not set(before.target_names).issubset(after.target_names):
            raise GlbFidelityError(f"primitive {index} morph target was removed")
    if source_signature.materials != processed_signature.materials:
        raise GlbFidelityError("material graph changed")
    if source_signature.textures != processed_signature.textures:
        raise GlbFidelityError("texture graph changed")
    if source_signature.samplers != processed_signature.samplers:
        raise GlbFidelityError("texture sampler graph changed")
    if source_signature.images != processed_signature.images:
        raise GlbFidelityError("embedded image payload changed")

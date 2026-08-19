from typing import Any


def _bone_segment_distance(point: Any, bone: Any) -> float:
    head = bone.head_local
    tail = bone.tail_local
    delta = tail - head
    position = max(0.0, min(1.0, (point - head).dot(delta) / max(delta.length_squared, 1e-12)))
    return (head + position * delta - point).length


def sanitize_head_weights(meshes: list[Any], arm_obj: Any) -> int:
    """Keep appendage influences out of the head and neck volume."""
    head = arm_obj.data.bones.get("Head")
    neck = arm_obj.data.bones.get("Neck")
    if head is None or neck is None:
        return 0

    allowed = {head.name, neck.name, *(bone.name for bone in head.children_recursive)}
    neck_base = arm_obj.matrix_world @ neck.head_local
    corrected = 0
    for obj in meshes:
        invalid: dict[str, list[int]] = {}
        for vertex in obj.data.vertices:
            if (obj.matrix_world @ vertex.co).z < neck_base.z:
                continue
            invalid_weights = {
                obj.vertex_groups[group.group].name: group.weight for group in vertex.groups if group.weight > 1e-6 and obj.vertex_groups[group.group].name not in allowed
            }
            if sum(invalid_weights.values()) < 1e-6:
                continue
            valid_weights = {obj.vertex_groups[group.group].name: group.weight for group in vertex.groups if group.weight > 1e-6 and obj.vertex_groups[group.group].name in allowed}
            for name in invalid_weights:
                invalid.setdefault(name, []).append(vertex.index)
            if valid_weights:
                total = sum(valid_weights.values())
                for name, weight in valid_weights.items():
                    obj.vertex_groups[name].add([vertex.index], weight / total, "REPLACE")
            else:
                point = arm_obj.matrix_world.inverted() @ (obj.matrix_world @ vertex.co)
                nearest = min((arm_obj.data.bones[name] for name in allowed if name in arm_obj.data.bones), key=lambda bone: _bone_segment_distance(point, bone))
                nearest_group = obj.vertex_groups.get(nearest.name)
                if nearest_group is None:
                    nearest_group = obj.vertex_groups.new(name=nearest.name)
                nearest_group.add([vertex.index], 1.0, "REPLACE")
            corrected += 1

        for group_name, vertices in invalid.items():
            obj.vertex_groups[group_name].remove(vertices)
    return corrected

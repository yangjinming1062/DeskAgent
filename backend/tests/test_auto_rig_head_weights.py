import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_HELPER_PATH = Path(__file__).resolve().parents[1] / "assets" / "animations" / "auto_rig_helpers.py"
_SPEC = spec_from_file_location("auto_rig_helpers", _HELPER_PATH)
auto_rig_helpers = module_from_spec(_SPEC)
_SPEC.loader.exec_module(auto_rig_helpers)
sanitize_head_weights = auto_rig_helpers.sanitize_head_weights


class FakeVector:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z

    def __sub__(self, other):
        return FakeVector(self.x - other.x, self.y - other.y, self.z - other.z)

    def __add__(self, other):
        return FakeVector(self.x + other.x, self.y + other.y, self.z + other.z)

    def __mul__(self, scalar):
        return FakeVector(self.x * scalar, self.y * scalar, self.z * scalar)

    __rmul__ = __mul__

    def dot(self, other):
        return self.x * other.x + self.y * other.y + self.z * other.z

    @property
    def length_squared(self):
        return self.dot(self)

    @property
    def length(self):
        return self.length_squared**0.5


class IdentityMatrix:
    def __matmul__(self, value):
        return value

    def inverted(self):
        return self


class FakeVertex:
    def __init__(self, index, co):
        self.index = index
        self.co = co
        self.groups = []


class FakeGroup:
    def __init__(self, group, weight):
        self.group = group
        self.weight = weight


class FakeVertexGroup:
    def __init__(self, name):
        self.name = name
        self.assigned = {}

    def add(self, vertices, weight, _type):
        for index in vertices:
            self.assigned[index] = weight

    def remove(self, vertices):
        for index in vertices:
            self.assigned.pop(index, None)


class FakeBone:
    def __init__(self, name, head, tail):
        self.name = name
        self.head_local = head
        self.tail_local = tail
        self.parent = None
        self.children_recursive = []


class FakeArmature:
    def __init__(self, bones):
        self.bones = bones
        self.data = types.SimpleNamespace(bones=bones)


class FakeGroupCollection(list):
    def __getitem__(self, key):
        if isinstance(key, str):
            return next(group for group in self if group.name == key)
        return super().__getitem__(key)

    def get(self, name):
        return next((group for group in self if group.name == name), None)

    def new(self, name):
        group = FakeVertexGroup(name)
        self.append(group)
        return group


class FakeObject:
    def __init__(self, groups, vertices):
        self.vertex_groups = FakeGroupCollection(groups)
        self.data = types.SimpleNamespace(vertices=vertices)
        self.matrix_world = IdentityMatrix()


def make_armature():
    armature = FakeArmature(
        {
            "Head": FakeBone("Head", FakeVector(0, 0, 0.8), FakeVector(0, 0, 0.9)),
            "Neck": FakeBone("Neck", FakeVector(0, 0, 0.7), FakeVector(0, 0, 0.8)),
            "RightForeArm": FakeBone("RightForeArm", FakeVector(0.3, 0, 0.4), FakeVector(0.5, 0, 0.4)),
        }
    )
    armature.matrix_world = IdentityMatrix()
    return armature


def make_mesh(armature, coordinate):
    groups = {name: FakeVertexGroup(name) for name in armature.bones}
    vertex = FakeVertex(7, coordinate)
    vertex.groups = [FakeGroup(2, 1.0)]
    return FakeObject(list(groups.values()), [vertex]), groups


def test_head_vertex_with_appendage_weight_is_reassigned():
    armature = make_armature()
    mesh, groups = make_mesh(armature, FakeVector(0.01, 0, 0.85))

    assert sanitize_head_weights([mesh], armature) == 1
    assert groups["RightForeArm"].assigned == {}
    assert groups["Head"].assigned[7] == 1.0


def test_head_vertex_creates_missing_head_group():
    armature = make_armature()
    appendage = FakeVertexGroup("RightForeArm")
    vertex = FakeVertex(9, FakeVector(0.01, 0, 0.85))
    vertex.groups = [FakeGroup(0, 1.0)]
    mesh = FakeObject([appendage], [vertex])

    assert sanitize_head_weights([mesh], armature) == 1
    assert appendage.assigned == {}
    assert mesh.vertex_groups.get("Head").assigned[9] == 1.0


def test_body_vertex_keeps_appendage_weight():
    armature = make_armature()
    mesh, groups = make_mesh(armature, FakeVector(0.4, 0, 0.4))

    assert sanitize_head_weights([mesh], armature) == 0
    assert groups["RightForeArm"].assigned == {}
    assert groups["Head"].assigned == {}


def test_missing_head_bones_is_noop():
    assert sanitize_head_weights([], FakeArmature({})) == 0

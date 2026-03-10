import math
from mathutils import Euler
from ..constants import MI_SCALE

def apply_default_transform(obj, node, disable_scale=False):
    """Apply the default_values from MI as the object's rest transform."""
    dv = node.default_values

    # Position:  MI UI X → BL X,  MI UI Y (Up) → BL Z,  MI UI Z (Depth) → BL -Y
    px = dv.get("POS_X", 0.0) * MI_SCALE
    py = dv.get("POS_Y", 0.0) * MI_SCALE  # UI Y (Up) → BL Z
    pz = dv.get("POS_Z", 0.0) * MI_SCALE  # UI Z (Depth) → BL -Y
    obj.location = (px, -pz, py)

    # Rotation
    rx = math.radians(dv.get("ROT_X", 0.0))
    ry = math.radians(dv.get("ROT_Y", 0.0))  # UI Y (Yaw) → BL Z
    rz = math.radians(dv.get("ROT_Z", 0.0))  # UI Z (Roll) → BL -Y
    obj.rotation_mode = 'XYZ'
    # MI camera zero-rot (Yaw=0) faces south (-Y in BL); Blender camera zero-rot faces -Z.
    obj.rotation_euler = Euler((rx, -rz, ry), 'XYZ')

    # Scale
    if not disable_scale:
        sx = dv.get("SCA_X", 1.0)
        sy = dv.get("SCA_Y", 1.0)  # UI Y → BL Z
        sz = dv.get("SCA_Z", 1.0)  # UI Z → BL Y
        obj.scale = (sx, sz, sy)
    else:
        obj.scale = (1.0, 1.0, 1.0)

"""
utils/transforms.py — Blender object transform helpers for mi2bl.

Key design note:
    Mine-Imator `default_values` = the position where the user *placed* the
    object when creating it in MI (the "creation placement").  It is NOT a set
    of property defaults.  We must NOT apply it as a Blender rest transform.

    Keyframe animation values (POS/ROT/SCA in node.keyframes) are relative to
    the MI hard-defaults (0 pos, 0 rot, 1 scale) — they already encode the full
    animated trajectory.  When keyframes exist, the object's Blender location is
    set by the animator; when there are no keyframes the object sits at origin.

    `default_values` is stored as raw custom properties for reference via
    `store_mi_placement()` in scene/props.py.
"""

import math
from mathutils import Euler
from ..constants import MI_SCALE


def clear_transform(obj):
    """Set the Blender object transform to identity (origin, no rotation, unit scale)."""
    obj.location = (0.0, 0.0, 0.0)
    obj.rotation_mode = 'XYZ'
    obj.rotation_euler = Euler((0.0, 0.0, 0.0), 'XYZ')
    obj.scale = (1.0, 1.0, 1.0)

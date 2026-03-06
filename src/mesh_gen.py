"""
MI-style mesh generators for Blender.
Produces geometry that matches Mine-Imator conventions:
  - Cube: origin determined by rot_point (default: bottom-center)
  - Surface (Plane): origin determined by rot_point (default: bottom-center),
    standing upright facing -Y

rot_point is an offset from the geometric center in MI local space,
with a range of [-8, 8] per axis for a default 16-unit shape.
  - [0, -8, 0] = bottom-center (MI default for cubes/surfaces)
  - [0,  0, 0] = geometric center
"""

import bpy
import bmesh
import math
from mathutils import Matrix


def _rot_point_to_translation(rot_point, size):
    """
    Convert an MI rot_point (offset from geometric center) into a
    Blender-space vertex translation that places the origin at
    the rot_point position.

    MI local axes → Blender local axes:
      MI X → BL X
      MI Y (up) → BL Z
      MI Z (depth) → BL -Y

    To make the rot_point the new origin, shift vertices by
    the negation of the offset, mapped to Blender axes.
    """
    rx, ry, rz = rot_point
    dx = -rx / 16.0 * size      # MI X → BL X  (negated)
    dy =  rz / 16.0 * size      # MI Z → BL -Y (neg-of-neg = pos)
    dz = -ry / 16.0 * size      # MI Y → BL Z  (negated)
    return Matrix.Translation((dx, dy, dz))


# Default rot_point for cubes / surfaces when none is specified.
# [0, -8, 0] = bottom-center of a 16×16×16 shape.
_DEFAULT_ROT_POINT = [0.0, -8.0, 0.0]


def create_mi_surface(name="MI_Surface", size=1.0, rot_point=None, collection=None):
    """
    Create a Mine-Imator style upright plane.
    The plane faces -Y (towards camera). Origin is placed according to rot_point.
    """
    mesh = bpy.data.meshes.new(name + "_Mesh")
    obj = bpy.data.objects.new(name, mesh)

    target_col = collection or bpy.context.collection
    target_col.objects.link(obj)

    bm = bmesh.new()
    # create_grid size is half-width, so size/2 gives us a 'size x size' quad
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=size / 2.0)

    # Rotate 90° around X to stand upright (face -Y)
    rot_mat = Matrix.Rotation(math.radians(90.0), 4, 'X')
    bmesh.ops.transform(bm, matrix=rot_mat, verts=bm.verts)

    # Shift vertices so origin sits at rot_point
    rp = rot_point if rot_point is not None else _DEFAULT_ROT_POINT
    bmesh.ops.transform(bm, matrix=_rot_point_to_translation(rp, size),
                        verts=bm.verts)

    bm.to_mesh(mesh)
    bm.free()

    return obj


def create_mi_cube(name="MI_Cube", size=1.0, rot_point=None, collection=None):
    """
    Create a Mine-Imator style cube.
    Origin is placed according to rot_point.
    """
    mesh = bpy.data.meshes.new(name + "_Mesh")
    obj = bpy.data.objects.new(name, mesh)

    target_col = collection or bpy.context.collection
    target_col.objects.link(obj)

    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)

    # Shift vertices so origin sits at rot_point
    rp = rot_point if rot_point is not None else _DEFAULT_ROT_POINT
    bmesh.ops.transform(bm, matrix=_rot_point_to_translation(rp, size),
                        verts=bm.verts)

    bm.to_mesh(mesh)
    bm.free()

    return obj

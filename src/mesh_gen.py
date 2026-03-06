"""
MI-style mesh generators for Blender.
Produces geometry that matches Mine-Imator conventions:
  - Cube: origin at bottom-center
  - Surface (Plane): origin at bottom-center, standing upright facing -Y
"""

import bpy
import bmesh
import math
from mathutils import Matrix


def create_mi_surface(name="MI_Surface", size=1.0, collection=None):
    """
    Create a Mine-Imator style upright plane.
    Origin is at the bottom-center edge. The plane faces -Y (towards camera).
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

    # Shift up by half so the bottom edge sits at Z=0
    trans_mat = Matrix.Translation((0.0, 0.0, size / 2.0))
    bmesh.ops.transform(bm, matrix=trans_mat, verts=bm.verts)

    bm.to_mesh(mesh)
    bm.free()

    return obj


def create_mi_cube(name="MI_Cube", size=1.0, collection=None):
    """
    Create a Mine-Imator style cube.
    Origin is at the bottom-center face.
    """
    mesh = bpy.data.meshes.new(name + "_Mesh")
    obj = bpy.data.objects.new(name, mesh)

    target_col = collection or bpy.context.collection
    target_col.objects.link(obj)

    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=size)

    # Shift up by half so the bottom face sits at Z=0
    trans_mat = Matrix.Translation((0.0, 0.0, size / 2.0))
    bmesh.ops.transform(bm, matrix=trans_mat, verts=bm.verts)

    bm.to_mesh(mesh)
    bm.free()

    return obj

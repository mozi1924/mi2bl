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
from mathutils import Matrix, Vector

def _rot_point_to_translation(rot_point, scale_vec):
    """
    Convert an MI rot_point (offset from geometric center) into a
    Blender-space vertex translation that places the origin at
    the rot_point position.
    """
    rx, ry, rz = rot_point
    dx = -rx / 16.0 * scale_vec.x      # MI X → BL X  (negated)
    dy =  rz / 16.0 * scale_vec.y      # MI Z → BL -Y (neg-of-neg = pos)
    dz = -ry / 16.0 * scale_vec.z      # MI Y → BL Z  (negated)
    return Matrix.Translation((dx, dy, dz))

def mi_uv_to_blender(u_pixel, v_pixel, tex_w, tex_h):
    return (u_pixel / tex_w, 1.0 - (v_pixel / tex_h))

_DEFAULT_ROT_POINT = [0.0, -8.0, 0.0]
_DEFAULT_BLOCK_ROT_POINT = [8.0, 0.0, 8.0]

def create_mi_surface(name="MI_Surface", size=1.0, size_3d=None, rot_point=None, collection=None, 
                      uv_offset=(0, 0), uv_repeat=(1, 1), texture_size=(64, 64), 
                      hmirror=False, vmirror=False):
    """
    Create a Mine-Imator style upright plane.
    """
    mesh = bpy.data.meshes.new(name + "_Mesh")
    obj = bpy.data.objects.new(name, mesh)
    target_col = collection or bpy.context.collection
    target_col.objects.link(obj)

    s_x, s_z = size_3d if size_3d else (size * 16.0, size * 16.0)

    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=1.0) # 1x1 grid [-0.5, 0.5]
    
    # Scale width/height independently
    # MI Surface is upright, facing -Y in Blender
    # Plane created by create_grid is on XY plane. Rotate to face -Y.
    bmesh.ops.scale(bm, vec=Vector((s_x / 16.0, s_z / 16.0, 1.0)), verts=bm.verts)
    rot_mat = Matrix.Rotation(math.radians(90.0), 4, 'X')
    bmesh.ops.transform(bm, matrix=rot_mat, verts=bm.verts)

    uv_layer = bm.loops.layers.uv.verify()

    # MI UV space: (0,0) top-left.
    def mi_to_bl_uv(uv):
        u, v = uv
        if hmirror: u = 1.0 - u
        if vmirror: v = 1.0 - v
        return (u, 1.0 - v)

    for face in bm.faces:
        for loop in face.loops:
            vx, vy, vz = loop.vert.co
            bl_width = s_x / 16.0
            bl_height = s_z / 16.0
            
            # Normalized local face coordinates [0, 1]
            u_loc = (vx / bl_width if bl_width else 0.0) + 0.5
            v_loc = 0.5 - (vz / bl_height if bl_height else 0.0)
            
            final_u = uv_offset[0] + u_loc * uv_repeat[0]
            final_v = uv_offset[1] + v_loc * uv_repeat[1]
            
            loop[uv_layer].uv = mi_to_bl_uv((final_u, final_v))

    # Base translation
    rp = rot_point if rot_point is not None else _DEFAULT_ROT_POINT
    scale_vec = Vector((s_x / 16.0, 1.0, s_z / 16.0))
    bmesh.ops.transform(bm, matrix=_rot_point_to_translation(rp, scale_vec), verts=bm.verts)

    bm.to_mesh(mesh)
    bm.free()
    return obj

def create_mi_cube(name="MI_Cube", size=1.0, size_3d=None, rot_point=None, collection=None, 
                   mapped=False, uv_offset=(0, 0), uv_repeat=(1, 1), 
                   texture_size=(64, 64), hmirror=False, vmirror=False, invert=False):
    """
    Create a Mine-Imator style Cube.
    If mapped=True, use 3x2 UI mapping grid.
    Otherwise, apply simple face-uniform UVs using offsets and repeats.
    """
    mesh = bpy.data.meshes.new(name + "_Mesh")
    obj = bpy.data.objects.new(name, mesh)
    target_col = collection or bpy.context.collection
    target_col.objects.link(obj)

    # size_3d defaults to MI 16x16x16 if not provided
    sx, sy, sz = size_3d if size_3d else (size * 16.0, size * 16.0, size * 16.0)

    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    
    # Scale it to MI units (converted to Blender units 1/16)
    # MI Y is UP (BL Z), MI Z is DEPTH (BL Y)
    # We use size_3d as (X, Y, Z) in MI terms
    bl_scale = Vector((sx / 16.0, sz / 16.0, sy / 16.0))
    bmesh.ops.scale(bm, vec=bl_scale, verts=bm.verts)

    uv_layer = bm.loops.layers.uv.verify()
    
    # MI UV space: (0,0) is top-left. Blender: (0,1) is top-left.
    # We'll work in MI-normalized [0,1] and then convert via (u, 1-v)
    
    def mi_to_bl_uv(uv):
        u, v = uv
        if hmirror: u = 1.0 - u
        if vmirror: v = 1.0 - v
        return (u, 1.0 - v)

    # 3x2 Mapped Grid layout (MI face -> normalized UV origin)
    # Row 0: [ Y+ (Top) ] [ X- (Left) ] [ X+ (Right) ]
    # Row 1: [ Y- (Bot) ] [ Z+ (Front)] [ Z- (Back)  ]

    tw, th = 1/3, 1/2
    grid = {
        "Y+": (0, 0),    "X-": (tw, 0),   "X+": (2*tw, 0),
        "Y-": (0, th),   "Z+": (tw, th),  "Z-": (2*tw, th)
    }

    if invert:
        grid["X+"], grid["X-"] = grid["X-"], grid["X+"]

    for face in bm.faces:
        normal = face.normal
        face_key = None

        # BL Z+ = MI Y+, BL Z- = MI Y-, BL Y+ = MI Z+, BL Y- = MI Z-
        if abs(normal.z - 1.0) < 0.1:   face_key = "Y+"
        elif abs(normal.z + 1.0) < 0.1: face_key = "Y-"
        elif abs(normal.x - 1.0) < 0.1: face_key = "X+"
        elif abs(normal.x + 1.0) < 0.1: face_key = "X-"
        elif abs(normal.y - 1.0) < 0.1: face_key = "Z+"
        elif abs(normal.y + 1.0) < 0.1: face_key = "Z-"

        for loop in face.loops:
            co = loop.vert.co
            # MI coords: mi_x_n = co.x/s+0.5, mi_y_n = co.z/s+0.5, mi_z_n = co.y/s+0.5
            if face_key == "Y+": # Top: u=mi_x_n, v=1-mi_z_n
                u_loc = (co.x / bl_scale.x) + 0.5
                v_loc = 0.5 - (co.y / bl_scale.y)
            elif face_key == "Y-": # Bottom: u=1-mi_x_n, v=1-mi_z_n
                u_loc = 0.5 - (co.x / bl_scale.x)
                v_loc = 0.5 - (co.y / bl_scale.y)
            elif face_key == "X+": # Right: u=1-mi_y_n, v=1-mi_z_n
                u_loc = 0.5 - (co.z / bl_scale.z)
                v_loc = 0.5 - (co.y / bl_scale.y)
            elif face_key == "X-": # Left: u=mi_y_n, v=1-mi_z_n
                u_loc = (co.z / bl_scale.z) + 0.5
                v_loc = 0.5 - (co.y / bl_scale.y)
            elif face_key == "Z+": # Front: u=mi_x_n, v=mi_y_n
                u_loc = (co.x / bl_scale.x) + 0.5
                v_loc = (co.z / bl_scale.z) + 0.5
            elif face_key == "Z-": # Back: u=mi_x_n, v=1-mi_y_n
                u_loc = (co.x / bl_scale.x) + 0.5
                v_loc = 0.5 - (co.z / bl_scale.z)
            else:
                u_loc, v_loc = 0.5, 0.5

            if mapped:
                gx, gy = grid[face_key]
                final_u = gx + u_loc * tw
                final_v = gy + v_loc * th
            else:
                final_u = uv_offset[0] + u_loc * uv_repeat[0]
                final_v = uv_offset[1] + v_loc * uv_repeat[1]
            
            loop[uv_layer].uv = mi_to_bl_uv((final_u, final_v))

    rp = rot_point if rot_point is not None else _DEFAULT_ROT_POINT
    bmesh.ops.transform(bm, matrix=_rot_point_to_translation(rp, bl_scale), verts=bm.verts)
    bm.to_mesh(mesh)
    bm.free()
    return obj

def create_mi_block(name="MI_Block", size_3d=(16.0, 16.0, 16.0), rot_point=None, collection=None, uv=(0, 0), texture_size=(64, 64), texture_mirror=False):
    """
    Create a Mine-Imator Block.
    Geometrically identical to a cube, but applies Minecraft cross-fold UV.
    size_3d matches MI bounds (e.g. 8x8x8 or 4x12x4).
    """
    mesh = bpy.data.meshes.new(name + "_Mesh")
    obj = bpy.data.objects.new(name, mesh)
    target_col = collection or bpy.context.collection
    target_col.objects.link(obj)

    bm = bmesh.new()
    # Normalize size
    sx, sy, sz = size_3d
    
    bmesh.ops.create_cube(bm, size=1.0)
    # create_cube creates a 1x1x1 cube [-0.5, +0.5]. Scale it:
    # MI Space maps bounding box dynamically
    scaled_sz = (sx / 16.0, sz / 16.0, sy / 16.0) # Map MI Z to Bl Y, MI Y to Bl Z
    bmesh.ops.scale(bm, vec=Vector(scaled_sz), verts=bm.verts)

    uv_layer = bm.loops.layers.uv.verify()
    tex_w, tex_h = texture_size
    base_u, base_v = uv

    # Minecraft Layout Box mapping algorithm:
    # X axis mapping determines the U boundaries
    for face in bm.faces:
        normal = face.normal
        for loop in face.loops:
            vx, vy, vz = loop.vert.co
            rel_u = 0
            rel_v = 0
            # Compare normals to determine which face this is
            if abs(normal.z - 1.0) < 0.1: # BL Z+ -> MI Y+ (Up/Top)
                rel_u = base_u + sx + (vx / scaled_sz[0]) * sx
                rel_v = base_v + (vy / scaled_sz[1]) * sy
            elif abs(normal.z + 1.0) < 0.1: # BL Z- -> MI Y- (Down/Bottom)
                rel_u = base_u + sx + sx + (vx / scaled_sz[0]) * sx
                rel_v = base_v + (vy / scaled_sz[1]) * sy
            elif abs(normal.y + 1.0) < 0.1: # BL Y- -> MI Z+ (Front/South)
                rel_u = base_u + sy + (vx / scaled_sz[0] + 0.5) * sx
                rel_v = base_v + sy + (-vz / scaled_sz[2] + 0.5) * sz
            elif abs(normal.y - 1.0) < 0.1: # BL Y+ -> MI Z- (Back/North)
                rel_u = base_u + sy + sx + sy + (vx / scaled_sz[0] + 0.5) * sx
                rel_v = base_v + sy + (-vz / scaled_sz[2] + 0.5) * sz
            elif abs(normal.x + 1.0) < 0.1: # BL X- -> MI X- (West/Right)
                rel_u = base_u + (vy / scaled_sz[1] + 0.5) * sy
                rel_v = base_v + sy + (-vz / scaled_sz[2] + 0.5) * sz
            elif abs(normal.x - 1.0) < 0.1: # BL X+ -> MI X+ (East/Left)
                rel_u = base_u + sy + sx + (vy / scaled_sz[1] + 0.5) * sy
                rel_v = base_v + sy + (-vz / scaled_sz[2] + 0.5) * sz
                
            loop[uv_layer].uv = mi_uv_to_blender(rel_u, rel_v, tex_w, tex_h)

    # MI rot point for block is [0, 16], default bottom-left instead of [-8, 8]
    rp = rot_point if rot_point is not None else _DEFAULT_BLOCK_ROT_POINT
    remapped = [v - 8.0 for v in rp]
    
    # Needs translating based on actual size multiplier for MI local scale
    # Normally a block size is 1.0 in terms of the multiplier
    bmesh.ops.transform(bm, matrix=_rot_point_to_translation(remapped, Vector((1.0, 1.0, 1.0))), verts=bm.verts)

    bm.to_mesh(mesh)
    bm.free()
    return obj

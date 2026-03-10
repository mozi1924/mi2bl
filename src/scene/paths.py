"""
scene/paths.py — Build Blender NURBS curve objects from MI path/pathpoint nodes.

Mine-Imator's Path is a **Quadratic B-Spline** (Order 3 NURBS in Blender).
Each `pathpoint` child carries its position in the first keyframe (frame 0).

Coordinate mapping follows the same MI→Blender convention used elsewhere:
    MI POS_X →  BL X
    MI POS_Z → -BL Y   (after the Y/Z swap fix in the parser)
    MI POS_Y →  BL Z

The path itself is positioned via the `path` node's default_values (creation
placement).  Since pathpoints are children of the path node, their positions
are relative to the path node origin in MI.

Follow Path:
    When a non-pathpoint MI object has PATH_OBJ / PATH_OFFSET in its keyframes,
    we add a Blender `FOLLOW_PATH` constraint pointing at the curve object and
    drive the `offset_factor` with keyframed values (0–100 in MI → 0.0–1.0 in
    Blender).  Curve Twist Method is set to MINIMUM to match MI's parallel
    transport algorithm.
"""

import bpy
import math
from ..constants import MI_SCALE


# ── Coordinate conversion ─────────────────────────────────────────────────────

def mi_pos_to_bl(px, py, pz):
    """Convert an MI (post-Y/Z-swap) position to Blender world coords (Blender units)."""
    return (
        px * MI_SCALE,
        -pz * MI_SCALE,   # MI UI depth (post-swap Z) → Blender -Y
        py * MI_SCALE,    # MI UI up   (post-swap Y) → Blender Z
    )


# ── Control-point extraction ──────────────────────────────────────────────────

def _get_pathpoint_pos(pp_node):
    """
    Return the (px, py, pz) position of a pathpoint node at frame 0
    (the static path shape).  The parser has already applied fix_mi_yz_swap,
    so POS_Y = up, POS_Z = depth.
    """
    kf0 = pp_node.keyframes.get(0, {})
    px = kf0.get("POS_X", 0.0)
    py = kf0.get("POS_Y", 0.0)
    pz = kf0.get("POS_Z", 0.0)
    return px, py, pz


# ── Curve creation ────────────────────────────────────────────────────────────

def create_path_curve(path_node, pathpoint_nodes, name, collection):
    """
    Create a Blender NURBS curve that reproduces MI's quadratic B-spline.

    Parameters
    ----------
    path_node       : MINode  (type == "path")
    pathpoint_nodes : list[MINode]  — ordered pathpoints (children of path_node)
    name            : str
    collection      : bpy.types.Collection

    Returns
    -------
    curve_obj : bpy.types.Object  (the NURBS curve object)
    """
    path_cfg = path_node.path_data or {}
    is_closed = path_cfg.get("closed", False)
    is_smooth  = path_cfg.get("smooth", True)

    # ── Collect control points in MI space ───────────────────────────────────
    ctrl_pts = [_get_pathpoint_pos(pp) for pp in pathpoint_nodes]

    if len(ctrl_pts) < 2:
        # Degenerate path — create an empty to avoid errors
        obj = bpy.data.objects.new(name, None)
        obj.empty_display_type = 'PLAIN_AXES'
        collection.objects.link(obj)
        obj["mi_path_degenerate"] = True
        return obj

    # ── Create NURBS curve data ───────────────────────────────────────────────
    curve_data = bpy.data.curves.new(name=name + "_CurveData", type='CURVE')
    curve_data.dimensions = '3D'
    curve_data.twist_mode = 'MINIMUM'   # matches MI parallel-transport algorithm

    if is_smooth:
        # ── Smooth path → Order-3 NURBS ──────────────────────────────────────
        # An Order-3 NURBS with the MI control points is mathematically
        # identical to MI's quadratic B-spline.
        spline = curve_data.splines.new(type='NURBS')
        spline.use_cyclic_u = is_closed
        # 'use_endpoint_u' clamps the curve to start/end at the first/last
        # control point, matching MI's open-path behaviour.
        spline.use_endpoint_u = not is_closed

        # Resize spline points (NURBS spline starts with 1 point)
        spline.points.add(len(ctrl_pts) - 1)

        for i, (px, py, pz) in enumerate(ctrl_pts):
            bx, by, bz = mi_pos_to_bl(px, py, pz)
            spline.points[i].co = (bx, by, bz, 1.0)   # NURBS uses (x,y,z,w)

        spline.order_u = 3   # Quadratic B-spline ≡ Order 3 in Blender

    else:
        # ── Non-smooth path → Poly (straight-line segments) ──────────────────
        spline = curve_data.splines.new(type='POLY')
        spline.use_cyclic_u = is_closed
        spline.points.add(len(ctrl_pts) - 1)

        for i, (px, py, pz) in enumerate(ctrl_pts):
            bx, by, bz = mi_pos_to_bl(px, py, pz)
            spline.points[i].co = (bx, by, bz, 1.0)

    # ── Create curve object ───────────────────────────────────────────────────
    curve_obj = bpy.data.objects.new(name, curve_data)
    collection.objects.link(curve_obj)

    # Store MI path metadata as custom props
    curve_obj["mi_path_smooth"] = is_smooth
    curve_obj["mi_path_closed"] = is_closed
    curve_obj["mi_path_detail"] = path_cfg.get("detail", 6)

    return curve_obj


# ── Follow Path constraint ────────────────────────────────────────────────────

def apply_follow_path_constraint(
    obj, curve_obj,
    path_offsets,          # list of (blender_frame, offset_0_to_100)
    start_frame, fps_scale,
):
    """
    Add a `FOLLOW_PATH` constraint to *obj* that targets *curve_obj*,
    then keyframe `offset_factor` from the MI PATH_OFFSET values.

    MI PATH_OFFSET is in the range [0, 100] (percentage along path).
    Blender's `offset_factor` is [0.0, 1.0].

    Parameters
    ----------
    obj          : bpy.types.Object — the object that follows the path
    curve_obj    : bpy.types.Object — the NURBS curve object
    path_offsets : list of (frame_num, offset_value)  — frame_num is MI frame
    start_frame  : int
    fps_scale    : float
    """
    # ── Add constraint ────────────────────────────────────────────────────────
    con = obj.constraints.new(type='FOLLOW_PATH')
    con.target = curve_obj
    con.use_curve_follow = True   # orient object along curve tangent
    con.use_fixed_location = True

    # ── Keyframe offset_factor ────────────────────────────────────────────────
    if not path_offsets:
        con.offset_factor = 0.0
        return

    for frame_num, offset_val in sorted(path_offsets):
        bl_frame = start_frame + (frame_num * fps_scale)
        # MI offset range [0, 100] → Blender [0.0, 1.0]
        con.offset_factor = max(0.0, min(1.0, offset_val / 100.0))
        con.keyframe_insert("offset_factor", frame=bl_frame)

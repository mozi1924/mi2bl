"""
scene/animator.py — Apply MI keyframe animation data onto Blender objects.

The parser (miobject_parser.MINode) merges all keyframe data with MI_HARD_DEFAULTS
before storing them (per-frame values override the hard defaults).  Every keyframe
in node.keyframes is guaranteed to contain ALL keys.

Key design point:
    `node.default_values` = MI object creation placement — it is IGNORED by the
    animator.  Objects start at Blender origin; keyframes define the full trajectory.

Transform tracks (location / rotation / scale) are written directly to Blender.
Material / appearance tracks are written as keyframe-able custom properties
via the props module.  The animator handles the *transform* channels here
and delegates everything else to apply_node_custom_props (called by builder).
"""

import math
from ..utils.core import MIBaseImporter, apply_mi_transition
from ..constants import MI_SCALE
from ..utils.color import hex_to_rgb


def _math_radians(deg):
    return math.radians(deg)


def apply_keyframes(obj, node, start_frame, fps_scale):
    """
    Apply keyframe animation data from the MINode onto the Blender object.

    Writes:
      - location  (POS_X/Y/Z)
      - rotation_euler  (ROT_X/Y/Z)
      - scale  (SCA_X/Y/Z)

    Material / appearance keyframes (ALPHA, VISIBLE, RGB_MUL, etc.) are
    written via apply_node_custom_props() called from builder.py so that
    they share the same frame set and easing.

    Returns a list of (time, transition_info) tuples for the interpolation pass.
    """
    kf_trans_list = []
    if not node.keyframes:
        return kf_trans_list

    for frame_num in sorted(node.keyframes.keys()):
        values = node.keyframes[frame_num]
        time = start_frame + (frame_num * fps_scale)

        # ── Transition info (for later interpolation pass) ──────────────────
        trans_type = values.get("TRANSITION", "linear")
        t_info = {
            "type": trans_type,
            "ease_in": (values.get("EASE_IN_X", 1.0),
                        values.get("EASE_IN_Y", 0.0)),
            "ease_out": (values.get("EASE_OUT_X", 0.0),
                         values.get("EASE_OUT_Y", 1.0)),
        }
        kf_trans_list.append((time, t_info))

        # ── Position ────────────────────────────────────────────────────────
        # MI coordinate system after Y/Z swap (done in parser):
        #   POS_X → BL X  (unchanged)
        #   POS_Z → BL -Y (UI Depth → BL -Y)
        #   POS_Y → BL Z  (UI Up → BL Z)
        loc = list(obj.location)
        loc[0] = values.get("POS_X", loc[0] / MI_SCALE) * MI_SCALE
        loc[1] = -values.get("POS_Z", -loc[1] / MI_SCALE) * MI_SCALE
        loc[2] = values.get("POS_Y", loc[2] / MI_SCALE) * MI_SCALE
        obj.location = tuple(loc)
        obj.keyframe_insert("location", frame=time)

        # ── Rotation ────────────────────────────────────────────────────────
        # MI after Y/Z swap:
        #   ROT_X → BL X  (roll in MI X-plane)
        #   ROT_Z → BL -Y (UI Z roll → BL -Y)
        #   ROT_Y → BL Z  (UI Y yaw → BL Z)
        obj.rotation_mode = 'XYZ'
        rx = _math_radians(values.get("ROT_X", 0.0))
        ry = _math_radians(-values.get("ROT_Z", 0.0))   # UI Z → BL -Y
        rz = _math_radians(values.get("ROT_Y", 0.0))    # UI Y → BL Z
        obj.rotation_euler = (rx, ry, rz)
        obj.keyframe_insert("rotation_euler", frame=time)

        # ── Scale ───────────────────────────────────────────────────────────
        # MI after Y/Z swap:
        #   SCA_X → BL X
        #   SCA_Z → BL Y  (UI Z depth → BL Y)
        #   SCA_Y → BL Z  (UI Y up → BL Z)
        sx = values.get("SCA_X", 1.0)
        sy = values.get("SCA_Z", 1.0)   # UI Z → BL Y
        sz = values.get("SCA_Y", 1.0)   # UI Y → BL Z
        obj.scale = (sx, sy, sz)
        obj.keyframe_insert("scale", frame=time)

    return kf_trans_list


def apply_interpolation_to_obj(obj, kf_trans_list):
    """Apply MI easing interpolation to the object's fcurves."""
    if not obj.animation_data or not obj.animation_data.action:
        return
    action = obj.animation_data.action
    for fcurve in action.fcurves:
        # Include transform, camera, light, and MI custom properties
        dp = fcurve.data_path
        is_mi_custom = dp.startswith('[\"mi_')
        if is_mi_custom or dp in (
            "location", "rotation_euler", "scale",
            "lens", "dof.focus_distance", "dof.aperture_fstop",
            "dof.aperture_rotation", "dof.aperture_ratio",
            "energy", "color", "shadow_soft_size", "specular_factor",
            "cutoff_distance", "spot_size", "spot_blend",
        ):
            for i in range(1, len(fcurve.keyframe_points)):
                kf0 = fcurve.keyframe_points[i - 1]
                kf1 = fcurve.keyframe_points[i]
                target_time = kf0.co.x

                best_t_info = None
                min_dist = 0.05
                for t, info in kf_trans_list:
                    dist = abs(t - target_time)
                    if dist < min_dist:
                        min_dist = dist
                        best_t_info = info

                if not best_t_info:
                    continue

                t_type = best_t_info["type"]
                if t_type == "instant":
                    kf0.interpolation = 'CONSTANT'
                elif t_type == "linear":
                    kf0.interpolation = 'LINEAR'
                elif t_type == "bezier":
                    MIBaseImporter.apply_bezier_handles(kf0, kf1, best_t_info)
                else:
                    apply_mi_transition(kf0, t_type, kf1)
            fcurve.update()

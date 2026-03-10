"""
scene/lights.py — MI Light native-property mapping and keyframing.

Native Blender light properties written here:
  - energy         ← LIGHT_STRENGTH × LIGHT_RANGE² × 4π  (physically-based conversion)
  - cutoff_distance← LIGHT_RANGE (use_custom_distance = True)
  - color          ← LIGHT_COLOR (hex int → RGB)
  - shadow_soft_size← LIGHT_SIZE
  - specular_factor← LIGHT_SPECULAR_STRENGTH
  - spot_size      ← LIGHT_SPOT_RADIUS  (spotlight only)
  - spot_blend     ← LIGHT_SPOT_SHARPNESS  (spotlight only)

Additional MI-only light props that don't map to native Blender properties are
written as keyframe-able custom properties via props.apply_node_custom_props()
which is called by builder.py.
"""

import math
from ..constants import MI_SCALE
from ..utils.color import hex_to_rgb
from .props import apply_node_custom_props, apply_node_static_props

# Hard defaults for light values (from tl_value_default.gml)
_LIGHT_DEFAULTS = {
    "LIGHT_COLOR":             16777215,  # c_white
    "LIGHT_STRENGTH":          1.0,
    "LIGHT_SPECULAR_STRENGTH": 1.0,
    "LIGHT_SIZE":              2.0,
    "LIGHT_RANGE":             250.0,
    "LIGHT_FADE_SIZE":         0.5,
    "LIGHT_SPOT_RADIUS":       50.0,
    "LIGHT_SPOT_SHARPNESS":    0.5,
}


def _get_energy(strength, l_range):
    """Convert MI light strength + range to a Blender point-light energy value."""
    range_m = l_range * MI_SCALE
    return max(0.0, float(strength) * (range_m ** 2) * 4.0 * math.pi)


def _get_shadow_soft_size(size_val):
    return max(0.0, float(size_val) * MI_SCALE)


def _get_spot_size(radius_val):
    """Convert MI spot radius (degrees, half-cone) to Blender spot_size (radians, full-cone)."""
    return max(0.0001, min(math.pi, math.radians(float(radius_val) * 2.0)))


def _get_spot_blend(sharpness_val):
    return 1.0 - max(0.0, min(1.0, float(sharpness_val)))


def apply_light_properties(light_obj, node, start_frame, fps_scale):
    """
    Apply light properties and keyframes to the Blender light data object.

    Called by builder.py for the *child* light data object (not the pivot empty).
    """
    l_data = light_obj.data
    l_data.use_custom_distance = True

    # Base values from MI hard defaults only.
    # `node.default_values` = object creation placement — NOT used here.
    base = dict(_LIGHT_DEFAULTS)

    # Collect all frames; always include frame 0
    frames = set(node.keyframes.keys()) if node.keyframes else set()
    frames.add(0)

    for frame_num in sorted(frames):
        time = start_frame + (frame_num * fps_scale)
        # node.keyframes entries contain MI_HARD_DEFAULTS + per-frame overrides (from parser).
        # For frame 0 with no keyframes fall back to base (MI hard defaults).
        kf = node.keyframes.get(frame_num, {})
        cv = dict(base)
        cv.update(kf)  # per-frame values override (or re-apply parser-merged values)

        # ── Energy ──────────────────────────────────────────────────────────
        l_data.energy = _get_energy(cv["LIGHT_STRENGTH"], cv["LIGHT_RANGE"])
        l_data.keyframe_insert("energy", frame=time)

        # ── Cutoff distance ──────────────────────────────────────────────────
        l_data.cutoff_distance = float(cv["LIGHT_RANGE"]) * MI_SCALE
        l_data.keyframe_insert("cutoff_distance", frame=time)

        # ── Colour ───────────────────────────────────────────────────────────
        l_data.color = hex_to_rgb(cv["LIGHT_COLOR"])
        l_data.keyframe_insert("color", frame=time)

        # ── Shadow soft size ─────────────────────────────────────────────────
        l_data.shadow_soft_size = _get_shadow_soft_size(cv["LIGHT_SIZE"])
        l_data.keyframe_insert("shadow_soft_size", frame=time)

        # ── Specular factor ──────────────────────────────────────────────────
        l_data.specular_factor = max(0.0, float(cv["LIGHT_SPECULAR_STRENGTH"]))
        l_data.keyframe_insert("specular_factor", frame=time)

        # ── Spotlight-only properties ────────────────────────────────────────
        if l_data.type == 'SPOT':
            l_data.spot_size  = _get_spot_size(cv["LIGHT_SPOT_RADIUS"])
            l_data.spot_blend = _get_spot_blend(cv["LIGHT_SPOT_SHARPNESS"])
            l_data.keyframe_insert("spot_size",  frame=time)
            l_data.keyframe_insert("spot_blend", frame=time)

    # ── MI custom props (light extras + common) ──────────────────────────────
    # Called here for the LIGHT object; builder.py calls it again for the
    # pivot empty (which holds the MI common props).
    apply_node_custom_props(light_obj, node, start_frame, fps_scale)

    # ── Static appearance flags ──────────────────────────────────────────────
    apply_node_static_props(light_obj, node)

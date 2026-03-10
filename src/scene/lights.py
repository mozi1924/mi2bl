import math
from ..constants import MI_SCALE
from ..utils.color import hex_to_rgb
from .props import apply_node_custom_props

def get_light_props():
    # Placeholder for future light-specific MI properties
    return {}, {}, {}

def apply_light_properties(light_obj, node, start_frame, fps_scale):
    """Apply light properties and keyframes based on Mine-imator logic."""
    l_data = light_obj.data
    dv = node.default_values

    def get_energy(strength, l_range):
        range_m = l_range * MI_SCALE
        return max(0.0, strength * (range_m ** 2) * 4.0 * math.pi)

    def get_radius(size_val):
        return max(0.0, size_val * MI_SCALE)

    def get_spot_size(radius_val):
        return max(0.0001, min(math.pi, math.radians(radius_val * 2.0)))

    def get_spot_blend(sharpness_val):
        return 1.0 - max(0.0, min(1.0, sharpness_val))

    l_data.use_custom_distance = True
    frames = set(node.keyframes.keys()) if node.keyframes else {0}
    frames.add(0)

    base_values = {
        "LIGHT_COLOR": dv.get("LIGHT_COLOR", 16777215),
        "LIGHT_STRENGTH": dv.get("LIGHT_STRENGTH", 1.0),
        "LIGHT_SPECULAR_STRENGTH": dv.get("LIGHT_SPECULAR_STRENGTH", 1.0),
        "LIGHT_SIZE": dv.get("LIGHT_SIZE", 2.0),
        "LIGHT_RANGE": dv.get("LIGHT_RANGE", 250.0),
        "LIGHT_SPOT_RADIUS": dv.get("LIGHT_SPOT_RADIUS", 50.0),
        "LIGHT_SPOT_SHARPNESS": dv.get("LIGHT_SPOT_SHARPNESS", 0.5),
    }

    for frame_num in sorted(frames):
        time = start_frame + (frame_num * fps_scale)
        cv = dict(base_values)
        if node.keyframes:
            cv.update(node.keyframes.get(frame_num, {}))

        s = float(cv["LIGHT_STRENGTH"])
        r = float(cv["LIGHT_RANGE"])
        l_data.energy = get_energy(s, r)
        l_data.keyframe_insert("energy", frame=time)

        l_data.cutoff_distance = r * MI_SCALE
        l_data.keyframe_insert("cutoff_distance", frame=time)

        l_data.color = hex_to_rgb(cv["LIGHT_COLOR"])
        l_data.keyframe_insert("color", frame=time)

        l_data.shadow_soft_size = get_radius(float(cv["LIGHT_SIZE"]))
        l_data.keyframe_insert("shadow_soft_size", frame=time)

        l_data.specular_factor = max(0.0, float(cv["LIGHT_SPECULAR_STRENGTH"]))
        l_data.keyframe_insert("specular_factor", frame=time)

        if l_data.type == 'SPOT':
            l_data.spot_size = get_spot_size(float(cv["LIGHT_SPOT_RADIUS"]))
            l_data.spot_blend = get_spot_blend(float(cv["LIGHT_SPOT_SHARPNESS"]))
            l_data.keyframe_insert("spot_size", frame=time)
            l_data.keyframe_insert("spot_blend", frame=time)

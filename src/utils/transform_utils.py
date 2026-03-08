import bpy
import math
from mathutils import Euler
from ..utils import core

MI_SCALE = 1.0 / 16.0
apply_mi_transition = core.apply_mi_transition
MIBaseImporter = core.MIBaseImporter

def _apply_default_transform(obj, node, disable_scale=False):
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
    sx = dv.get("SCA_X", 1.0)
    sy = dv.get("SCA_Y", 1.0)  # UI Y → BL Z
    sz = dv.get("SCA_Z", 1.0)  # UI Z → BL Y
    obj.scale = (sx, sz, sy)


def _hex_to_rgb(hex_val):
    if isinstance(hex_val, (int, float)):
        hex_val = int(hex_val)
        r = ((hex_val >> 16) & 0xFF) / 255.0
        g = ((hex_val >> 8) & 0xFF) / 255.0
        b = (hex_val & 0xFF) / 255.0
        return (r, g, b)
    return (1.0, 1.0, 1.0)

def _apply_light_properties(light_obj, node, start_frame, fps_scale):
    """Apply light properties and keyframes."""
    l_data = light_obj.data
    dv = node.default_values
    
    # MI light defaults
    MI_DEFAULT_COLOR = 16777215
    MI_DEFAULT_STRENGTH = 1.0
    MI_DEFAULT_SPEC_STRENGTH = 1.0
    MI_DEFAULT_SIZE = 16.0
    MI_DEFAULT_RANGE = 250.0
    MI_DEFAULT_SPOT_RADIUS = 45.0
    MI_DEFAULT_SPOT_SHARPNESS = 0.5

    def get_val(values, key, default):
        return values.get(key, default)
        
    def _calc_energy(strength, l_range):
        # Calculate a reasonable Watts energy based on Strength and Range
        # A flat multiplier of 5000 * strength usually matches visible scale, 
        # but factoring in range gives more physically accurate falloff equivalence.
        distance_meters = l_range * MI_SCALE
        return strength * (distance_meters ** 2) * 50.0 + (strength * 1000.0)

    # helper to set and keyframe property if present
    def process_frames():
        # First gather all frame times including frame 0 for default
        frames = set(node.keyframes.keys()) if node.keyframes else set()
        frames.add(0) # ensure base frame
        
        for frame_num in sorted(frames):
            time = start_frame + (frame_num * fps_scale)
            if frame_num == 0:
                values = dict(dv)
                if node.keyframes and 0 in node.keyframes:
                    values.update(node.keyframes[0])
            else:
                values = node.keyframes.get(frame_num, {})
            
            # Set properties (using MI defaults if missing on frame 0)
            if "LIGHT_COLOR" in values or frame_num == 0:
                l_data.color = _hex_to_rgb(get_val(values, "LIGHT_COLOR", MI_DEFAULT_COLOR))
                l_data.keyframe_insert("color", frame=time)
                
            if "LIGHT_STRENGTH" in values or "LIGHT_RANGE" in values or frame_num == 0:
                strength = get_val(values, "LIGHT_STRENGTH", MI_DEFAULT_STRENGTH)
                l_range = get_val(values, "LIGHT_RANGE", MI_DEFAULT_RANGE)
                l_data.energy = _calc_energy(strength, l_range)
                l_data.keyframe_insert("energy", frame=time)
                
            if "LIGHT_SPECULAR_STRENGTH" in values or frame_num == 0:
                l_data.specular_factor = get_val(values, "LIGHT_SPECULAR_STRENGTH", MI_DEFAULT_SPEC_STRENGTH)
                l_data.keyframe_insert("specular_factor", frame=time)
                
            # Point/Spot properties
            if "LIGHT_SIZE" in values or frame_num == 0:
                l_data.shadow_soft_size = get_val(values, "LIGHT_SIZE", MI_DEFAULT_SIZE) * MI_SCALE
                l_data.keyframe_insert("shadow_soft_size", frame=time)
                
            if "LIGHT_RANGE" in values or frame_num == 0:
                l_data.cutoff_distance = get_val(values, "LIGHT_RANGE", MI_DEFAULT_RANGE) * MI_SCALE
                l_data.keyframe_insert("cutoff_distance", frame=time)
                if hasattr(l_data, "use_custom_distance"):
                    l_data.use_custom_distance = True

            # Spot specific properties
            if l_data.type == 'SPOT':
                if "LIGHT_SPOT_RADIUS" in values or frame_num == 0:
                    l_data.spot_size = math.radians(get_val(values, "LIGHT_SPOT_RADIUS", MI_DEFAULT_SPOT_RADIUS) * 2.0)
                    l_data.keyframe_insert("spot_size", frame=time)
                    
                if "LIGHT_SPOT_SHARPNESS" in values or frame_num == 0:
                    sharp = get_val(values, "LIGHT_SPOT_SHARPNESS", MI_DEFAULT_SPOT_SHARPNESS)
                    blend = max(0.0, min(1.0, 1.0 - sharp))
                    l_data.spot_blend = blend
                    l_data.keyframe_insert("spot_blend", frame=time)

    process_frames()



def _apply_keyframes(obj, node, start_frame, fps_scale, disable_scale=False):
    """
    Apply keyframe animation data from the MINode onto the Blender object.
    Returns a list of (time, transition_info) tuples for interpolation pass.
    """
    kf_trans_list = []

    for frame_num in sorted(node.keyframes.keys()):
        values = node.keyframes[frame_num]
        time = start_frame + (frame_num * fps_scale)

        # Transition info for later interpolation
        trans_type = values.get("TRANSITION", "linear")
        t_info = {
            "type": trans_type,
            "ease_in": (values.get("EASE_IN_X", 1.0),
                        values.get("EASE_IN_Y", 0.0)),
            "ease_out": (values.get("EASE_OUT_X", 0.0),
                         values.get("EASE_OUT_Y", 1.0))
        }
        kf_trans_list.append((time, t_info))

        # --- Position ---
        has_pos = False
        loc = list(obj.location)
        if "POS_X" in values:
            loc[0] = values["POS_X"] * MI_SCALE
            has_pos = True
        if "POS_Z" in values:
            loc[1] = -values["POS_Z"] * MI_SCALE   # UI Z (Depth) → BL -Y
            has_pos = True
        if "POS_Y" in values:
            loc[2] = values["POS_Y"] * MI_SCALE     # UI Y (Up) → BL Z
            has_pos = True
        if has_pos:
            obj.location = tuple(loc)
            obj.keyframe_insert("location", frame=time)

        # --- Rotation ---
        has_rot = False
        rot = list(obj.rotation_euler) if obj.rotation_mode == 'XYZ' \
            else [0.0, 0.0, 0.0]
        if "ROT_X" in values:
            rot[0] = math.radians(values["ROT_X"])
            has_rot = True
        if "ROT_Z" in values:
            rot[1] = math.radians(-values["ROT_Z"])  # UI Z (Roll) → BL -Y
            has_rot = True
        if "ROT_Y" in values:
            rot[2] = math.radians(values["ROT_Y"])    # UI Y (Yaw) → BL Z
            has_rot = True
        if has_rot:
            obj.rotation_mode = 'XYZ'
            obj.rotation_euler = Euler(tuple(rot), 'XYZ')
            obj.keyframe_insert("rotation_euler", frame=time)

        # --- Scale ---
        has_scl = False
        scl = list(obj.scale)
        if "SCA_X" in values:
            scl[0] = values["SCA_X"]
            has_scl = True
        if "SCA_Z" in values:
            scl[1] = values["SCA_Z"]   # UI Z (Depth) → BL Y
            has_scl = True
        if "SCA_Y" in values:
            scl[2] = values["SCA_Y"]   # UI Y (Up) → BL Z
            has_scl = True
        if has_scl:
            obj.scale = tuple(scl)
            obj.keyframe_insert("scale", frame=time)

    return kf_trans_list


def _apply_interpolation_to_obj(obj, kf_trans_list):
    """Apply MI easing interpolation to the object's fcurves."""
    if not obj.animation_data or not obj.animation_data.action:
        return
    action = obj.animation_data.action
    for fcurve in action.fcurves:
        if fcurve.data_path in ("location", "rotation_euler", "scale"):
            # Walk keyframe pairs
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

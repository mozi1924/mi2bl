import bpy
import json
import os
import math

# --- Constants ---
from ..constants import MI_SCALE

# Mine-Imator to Blender easing interpolation mapping
MI_TO_BLENDER_EASING_MAP = {
    # Sine
    "easeinsine": {"interpolation": "SINE", "easing": "EASE_IN"},
    "easeoutsine": {"interpolation": "SINE", "easing": "EASE_OUT"},
    "easeinoutsine": {"interpolation": "SINE", "easing": "EASE_IN_OUT"},
    # Quad
    "easeinquad": {"interpolation": "QUAD", "easing": "EASE_IN"},
    "easeoutquad": {"interpolation": "QUAD", "easing": "EASE_OUT"},
    "easeinoutquad": {"interpolation": "QUAD", "easing": "EASE_IN_OUT"},
    # Cubic
    "easeincubic": {"interpolation": "CUBIC", "easing": "EASE_IN"},
    "easeoutcubic": {"interpolation": "CUBIC", "easing": "EASE_OUT"},
    "easeinoutcubic": {"interpolation": "CUBIC", "easing": "EASE_IN_OUT"},
    # Quart
    "easeinquart": {"interpolation": "QUART", "easing": "EASE_IN"},
    "easeoutquart": {"interpolation": "QUART", "easing": "EASE_OUT"},
    "easeinoutquart": {"interpolation": "QUART", "easing": "EASE_IN_OUT"},
    # Quint
    "easeinquint": {"interpolation": "QUINT", "easing": "EASE_IN"},
    "easeoutquint": {"interpolation": "QUINT", "easing": "EASE_OUT"},
    "easeinoutquint": {"interpolation": "QUINT", "easing": "EASE_IN_OUT"},
    # Expo
    "easeinexpo": {"interpolation": "EXPO", "easing": "EASE_IN"},
    "easeoutexpo": {"interpolation": "EXPO", "easing": "EASE_OUT"},
    "easeinoutexpo": {"interpolation": "EXPO", "easing": "EASE_IN_OUT"},
    # Circ
    "easeincirc": {"interpolation": "CIRC", "easing": "EASE_IN"},
    "easeoutcirc": {"interpolation": "CIRC", "easing": "EASE_OUT"},
    "easeinoutcirc": {"interpolation": "CIRC", "easing": "EASE_IN_OUT"},
    # Back
    "easeinback": {"interpolation": "BACK", "easing": "EASE_IN"},
    "easeoutback": {"interpolation": "BACK", "easing": "EASE_OUT"},
    "easeinoutback": {"interpolation": "BACK", "easing": "EASE_IN_OUT"},
    # Bounce
    "easeinbounce": {"interpolation": "BOUNCE", "easing": "EASE_IN"},
    "easeoutbounce": {"interpolation": "BOUNCE", "easing": "EASE_OUT"},
    "easeinoutbounce": {"interpolation": "BOUNCE", "easing": "EASE_IN_OUT"},
    # Elastic
    "easeinelastic": {"interpolation": "ELASTIC", "easing": "EASE_IN"},
    "easeoutelastic": {"interpolation": "ELASTIC", "easing": "EASE_OUT"},
    "easeinoutelastic": {"interpolation": "ELASTIC", "easing": "EASE_IN_OUT"},
}

def apply_mi_transition(keyframe_point, mi_transition_str, kf1=None):
    """Apply a Mine-Imator easing transition to a Blender keyframe point."""
    mapped = MI_TO_BLENDER_EASING_MAP.get(mi_transition_str.lower())
    if mapped:
        keyframe_point.interpolation = mapped["interpolation"]
        keyframe_point.easing = mapped["easing"]
        
        # Mine-imator uses a normalized [0, 1] relative system.
        # Blender's `kf.amplitude` defaults to `0.0`, which causes its internal Penner computation
        # to fall back to an absolute `1.0`, resulting in a MASSIVE jump if the coordinate jump is small
        # (e.g. 0.03 rad delta will still jump an absolute 0.35 rad bounds)!
        # To match MI mathematically, we MUST explicitly set `kf.amplitude` to the delta value.
        if mapped["interpolation"] == 'ELASTIC' and kf1 is not None:
            dv = abs(kf1.co.y - keyframe_point.co.y)
            # Blender documentation says '0.0 defaults to 1.0', so setting it to 0.0 directly is unsafe.
            # Fallback to an extremely small value if the jump is exactly zero
            keyframe_point.amplitude = max(dv, 0.00001)
    else:
        keyframe_point.interpolation = 'LINEAR'

# --- Core Data Parsing (Transferred from configs.py) ---

def fix_mi_yz_swap(values):
    """Fixes the UI Y/Z swap bug in MI saved data."""
    fixed = {}
    for k, v in values.items():
        if k.endswith("_Y") and not k.startswith("EASE_"):
            fixed[k[:-2] + "_Z"] = v
        elif k.endswith("_Z") and not k.startswith("EASE_"):
            fixed[k[:-2] + "_Y"] = v
        else:
            fixed[k] = v
    return fixed

def fill_defaults(values):
    """Fill in missing transform keys with MI defaults."""
    for k in ("POS_X", "POS_Y", "POS_Z", "ROT_X", "ROT_Y", "ROT_Z"):
        values.setdefault(k, 0.0)
    for k in ("SCA_X", "SCA_Y", "SCA_Z"):
        values.setdefault(k, 1.0)
    values.setdefault("TRANSITION", "linear")
    return values

def parse_mi_file_data(data, char_index=0):
    """Normalizes .miframes and .miobject JSON data into a uniform structure."""
    if "keyframes" in data and isinstance(data["keyframes"], list):
        for kf in data["keyframes"]:
            raw_vals = kf.get("values", {})
            raw_vals = fill_defaults(raw_vals)
            kf["values"] = fix_mi_yz_swap(raw_vals)
        return data

    timelines = data.get("timelines", [])
    # Build template lookup for model name resolution
    template_map = {t["id"]: t for t in data.get("templates", []) if "id" in t}

    primary_id = None
    is_model = False
    primary_template = {}
    human_chars = [
        t for t in timelines
        if t.get("type") == "char"
        and template_map.get(t.get("temp", ""), {}).get("model", {}).get("name") == "human"
    ]
    if human_chars:
        idx = min(char_index, len(human_chars) - 1)
        t = human_chars[idx]
        primary_id = t.get("id")
        is_model = True
        primary_template = template_map.get(t.get("temp", ""), {})
            
    if not is_model:
        for t in timelines:
            parent = t.get("parent")
            if not parent or parent == "root":
                primary_id = t.get("id")
                break

    keyframes_list = []
    for tl in timelines:
        tl_id = tl.get("id")
        part_of = tl.get("part_of")
        
        if is_model:
            if tl_id == primary_id:
                part_name = "root"
            elif part_of == primary_id and "model_part_name" in tl:
                part_name = tl["model_part_name"]
            else:
                continue
        else:
            if tl_id == primary_id:
                part_name = "root"
            else:
                continue
            
        kf_dict = tl.get("keyframes", {})
        if not kf_dict:
            kf_dict["0"] = {}
            
        for frame_str, kf_vals in kf_dict.items():
            frame_num = int(frame_str)
            combined = dict(kf_vals)
            fill_defaults(combined)
            fixed_combined = fix_mi_yz_swap(combined)
            keyframes_list.append({
                "position": frame_num,
                "part_name": part_name,
                "values": fixed_combined
            })
            
    keyframes_list.sort(key=lambda x: x["position"])
    if len(keyframes_list) > 0:
        firstpos = keyframes_list[0]["position"]
        lastpos = keyframes_list[-1]["position"]
        for kf in keyframes_list:
            kf["position"] -= firstpos
        final_length = lastpos - firstpos
    else:
        final_length = 0
    
    result = {
        "format": data.get("format", 34),
        "is_model": is_model,
        "tempo": data.get("tempo", 24),
        "length": final_length,
        "keyframes": keyframes_list
    }
    # Prefer model info from the matched template; fall back to top-level "model" key
    model_info = primary_template.get("model") or data.get("model")
    if model_info:
        result["model"] = model_info
    return result

# --- Base Importer Mixin ---

class MIBaseImporter:
    """Shared Mine-Imator import logic for all operators."""
    
    def check_file(self, filepath, char_index=0):
        if not filepath or not os.path.exists(filepath):
            return None, "File not found."
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                raw_data = json.load(f)
                return parse_mi_file_data(raw_data, char_index=char_index), None
        except Exception as e:
            return None, f"JSON Error: {str(e)}"

    def setup_scene(self, context, data, start_frame, adjust_end):
        tempo = data.get("tempo", 24)
        fps_current = context.scene.render.fps
        fps_scale = fps_current / tempo
        length = data.get("length", 0)
        if adjust_end and length > 0:
            blender_end_frame = start_frame + (length * fps_scale)
            context.scene.frame_end = int(blender_end_frame)
        return tempo, fps_current, fps_scale

    @staticmethod
    def apply_bezier_handles(kf0, kf1, t_info):
        kf0.interpolation = 'BEZIER'
        dt = kf1.co.x - kf0.co.x
        dv = kf1.co.y - kf0.co.y
        x1, y1 = t_info["ease_in"]
        x2, y2 = t_info["ease_out"]
        
        # Clamp X to [0, 1] to prevent FCurve timeline flow issues
        x1 = max(0.0, min(1.0, float(x1)))
        x2 = max(0.0, min(1.0, float(x2)))
        
        kf0.handle_right_type = 'FREE'
        kf1.handle_left_type = 'FREE'
        kf0.handle_right = (kf0.co.x + (x1 * dt), kf0.co.y + (y1 * dv))
        kf1.handle_left = (kf0.co.x + (x2 * dt), kf0.co.y + (y2 * dv))

    def apply_interpolation(self, fcurve, trans_list):
        for i in range(1, len(fcurve.keyframe_points)):
            kf0 = fcurve.keyframe_points[i - 1]
            kf1 = fcurve.keyframe_points[i]
            target_time = kf0.co.x
            best_t_info = None
            min_dist = 0.05
            for t, info in trans_list:
                dist = abs(t - target_time)
                if dist < min_dist:
                    min_dist = dist
                    best_t_info = info
            if not best_t_info: continue
            t_type = best_t_info["type"]
            if t_type == "instant":
                kf0.interpolation = 'CONSTANT'
            elif t_type == "linear":
                kf0.interpolation = 'LINEAR'
            elif t_type == "bezier":
                self.apply_bezier_handles(kf0, kf1, best_t_info)
            else:
                apply_mi_transition(kf0, t_type, kf1)
        fcurve.update()

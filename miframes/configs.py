import math
from mathutils import Euler, Vector

# --- Constants ---
MI_SCALE = 1.0 / 16.0

# --- Specialized Bone Handlers ---

def handler_standard(bone, values, config, time):
    """Standard bone rotation using axis mapping and optional offsets."""
    axis_map = config.get("axis_map", {})
    axis_scale = config.get("axis_scale", {"X": 1.0, "Y": 1.0, "Z": 1.0})
    
    val_x = math.radians(values.get(axis_map.get("X", "ROT_X"), 0)) * axis_scale.get("X", 1.0)
    val_y = math.radians(values.get(axis_map.get("Y", "ROT_Y"), 0)) * axis_scale.get("Y", 1.0)
    val_z = math.radians(values.get(axis_map.get("Z", "ROT_Z"), 0)) * axis_scale.get("Z", 1.0)
    
    # Apply special offsets (e.g. for arms)
    offset = config.get("rotation_offset", (0, 0, 0))
    val_x += math.radians(offset[0])
    val_y += math.radians(offset[1])
    val_z += math.radians(offset[2])
    
    q = Euler((val_x, val_y, val_z), 'XYZ').to_quaternion()
    bone.rotation_mode = 'QUATERNION'
    bone.rotation_quaternion = q
    bone.keyframe_insert("rotation_quaternion", frame=time)

def handler_pos_scl(bone, values, config, time):
    """Position and Scale mapping."""
    pos_map = config.get("pos_map", {"X": "POS_X", "Y": "POS_Y", "Z": "POS_Z"})
    scl_map = config.get("scl_map", {"X": "SCA_X", "Y": "SCA_Y", "Z": "SCA_Z"})
    
    # Position
    has_pos = False
    v_pos = list(bone.location)
    for i, axis in enumerate(["X", "Y", "Z"]):
        key = pos_map.get(axis)
        if key in values:
            v_pos[i] = values[key] * MI_SCALE
            has_pos = True
            
    if has_pos:
        bone.location = (v_pos[0], v_pos[1], v_pos[2])
        bone.keyframe_insert("location", frame=time)

    # Scale
    has_scl = False
    v_scl = list(bone.scale)
    for i, axis in enumerate(["X", "Y", "Z"]):
        key = scl_map.get(axis)
        if key in values:
            v_scl[i] = values[key]
            has_scl = True

    if has_scl:
        bone.scale = (v_scl[0], v_scl[1], v_scl[2])
        bone.keyframe_insert("scale", frame=time)

HANDLERS = {
    "standard": handler_standard,
    "pos_scl": handler_pos_scl,
}

def fix_mi_yz_swap(values):
    """
    Mine-Imator has a bug where UI Y (Up) and UI Z (Depth) 
    are swapped in the saved JSON file for all transform properties.
    This wipes the bug's ass and restores them to the True UI Values.
    """
    fixed = {}
    for k, v in values.items():
        if k.endswith("_Y") and not k.startswith("EASE_"):
            fixed[k[:-2] + "_Z"] = v
        elif k.endswith("_Z") and not k.startswith("EASE_"):
            fixed[k[:-2] + "_Y"] = v
        else:
            fixed[k] = v
    return fixed

def _fill_defaults(values):
    """
    Fill in missing POS/ROT/SCA keys with their default values.
    MI only saves keys that differ from defaults, so missing keys
    must be treated as: POS=0, ROT=0, SCA=1.
    """
    for k in ("POS_X", "POS_Y", "POS_Z", "ROT_X", "ROT_Y", "ROT_Z"):
        values.setdefault(k, 0.0)
    for k in ("SCA_X", "SCA_Y", "SCA_Z"):
        values.setdefault(k, 1.0)
    values.setdefault("TRANSITION", "linear")
    return values

def parse_mi_file_data(data):
    """
    Normalizes both .miframes and .miobject JSON data into a uniform .miframes structure.
    Also handles the Y/Z swap bug and default value filling.
    
    NOTE: default_values in .miobject is the MI scene placement coordinates 
    and is NOT merged into keyframe values. Keyframe values already represent
    the exact values shown in the MI UI (offsets from origin).
    """
    if "keyframes" in data and isinstance(data["keyframes"], list):
        # Already a .miframes format
        for kf in data["keyframes"]:
            raw_vals = kf.get("values", {})
            raw_vals = _fill_defaults(raw_vals)
            kf["values"] = fix_mi_yz_swap(raw_vals)
        return data

    # Convert .miobject to .miframes format
    timelines = data.get("timelines", [])
    
    # Identify the primary target ID (the first character or the first root object)
    primary_id = None
    is_model = False
    for t in timelines:
        if t.get("type") == "char":
            primary_id = t.get("id")
            is_model = True
            break
            
    if not is_model:
        # If no character, look for the first root object
        for t in timelines:
            parent = t.get("parent")
            # In MI, parent can be "root" or null if it's a top-level object
            if not parent or parent == "root":
                primary_id = t.get("id")
                break

    keyframes_list = []
    
    for tl in timelines:
        tl_id = tl.get("id")
        tl_type = tl.get("type", "")
        part_of = tl.get("part_of")
        
        # --- Strict Filtering Logic ---
        if is_model:
            # For characters:
            if tl_id == primary_id:
                part_name = "root"
            elif part_of == primary_id and "model_part_name" in tl:
                part_name = tl["model_part_name"]
            else:
                # Ignore extraneous characters, folders, surfaces, etc.
                continue
        else:
            # For non-model objects:
            if tl_id == primary_id:
                part_name = "root"
            else:
                # We only want the primary transformation for single-object imports
                continue
            
        kf_dict = tl.get("keyframes", {})
        
        if not kf_dict:
            kf_dict["0"] = {}
            
        for frame_str, kf_vals in kf_dict.items():
            frame_num = int(frame_str)
            
            # Copy only the kf values (NOT default_values), then fill missing with defaults
            combined = dict(kf_vals)
            _fill_defaults(combined)

            fixed_combined = fix_mi_yz_swap(combined)
            keyframes_list.append({
                "position": frame_num,
                "part_name": part_name,
                "values": fixed_combined
            })
            
    keyframes_list.sort(key=lambda x: x["position"])
    
    if len(keyframes_list) > 0:
        # .miobject saves ABSOLUTE timeline positions (e.g. frame 60, 73, 400...)
        # We need to normalize them to relative positions starting from 0,
        # just like .miframes does during its export process (pos - firstpos).
        firstpos = keyframes_list[0]["position"]
        lastpos = keyframes_list[-1]["position"]
        
        for kf in keyframes_list:
            kf["position"] -= firstpos
        
        final_length = lastpos - firstpos
    else:
        final_length = 0
    
    return {
        "format": data.get("format", 34),
        "created_in": data.get("created_in", ""),
        "is_model": is_model,
        "tempo": data.get("tempo", 24),
        "length": final_length,
        "keyframes": keyframes_list
    }

# --- Model Registry ---

RIG2_STEVE = {
    "name": "Rig2 Steve (MI Direct)",
    "bones": {
        "root": {
            "target_rot": "MI_Root",
            "target_pos_scl": "MI_Root",
            "handler_rot": "standard",
            "handler_pos_scl": "pos_scl",
            "axis_map": {"X": "ROT_X", "Y": "ROT_Y", "Z": "ROT_Z"}
        },
        "head": {
            "target_rot": "MI_Head",
            "target_pos_scl": "MI_P_Head",
            "handler_rot": "standard",
            "handler_pos_scl": "pos_scl",
            "axis_map": {"X": "ROT_X", "Y": "ROT_Y", "Z": "ROT_Z"}
        },
        "body": {
            "target_rot": "MI_Body Lower",
            "target_pos_scl": "MI_Body",
            "handler_rot": "standard",
            "handler_pos_scl": "pos_scl",
            "axis_map": {"X": "ROT_X", "Y": "ROT_Y", "Z": "ROT_Z"}
        },
        "left_arm": {
            "target_rot": "MI_arm.upper.L",
            "target_pos_scl": "MI_arm.L",
            "handler_rot": "standard",
            "handler_pos_scl": "pos_scl",
            "axis_map": {"X": "ROT_X", "Y": "ROT_Y", "Z": "ROT_Z"}
        },
        "right_arm": {
            "target_rot": "MI_arm.upper.R",
            "target_pos_scl": "MI_arm.R",
            "handler_rot": "standard",
            "handler_pos_scl": "pos_scl",
            "axis_map": {"X": "ROT_X", "Y": "ROT_Y", "Z": "ROT_Z"}
        },
        "left_leg": {
            "target_rot": "MI_leg.upper.L",
            "target_pos_scl": "MI_leg.L",
            "handler_rot": "standard",
            "handler_pos_scl": "pos_scl",
            "axis_map": {"X": "ROT_X", "Y": "ROT_Y", "Z": "ROT_Z"},
            "axis_scale": {"X": 1.0, "Y": -1.0, "Z": -1.0}
        },
        "right_leg": {
            "target_rot": "MI_leg.upper.R",
            "target_pos_scl": "MI_leg.R",
            "handler_rot": "standard",
            "handler_pos_scl": "pos_scl",
            "axis_map": {"X": "ROT_X", "Y": "ROT_Y", "Z": "ROT_Z"},
            "axis_scale": {"X": 1.0, "Y": -1.0, "Z": -1.0}
        }
    },
    "bend_targets": {
        "left_arm": "MI_arm.lower.L",
        "right_arm": "MI_arm.lower.R",
        "left_leg": "MI_leg.lower.L",
        "right_leg": "MI_leg.lower.R",
        "body": "MI_Body Upper"
    }
}

MODELS = {
    "steve": RIG2_STEVE,
}

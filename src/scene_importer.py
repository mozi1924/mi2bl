"""
Scene importer for .miobject files.

Creates Blender objects (Empties, Cubes, Surfaces) with the correct
parent-child hierarchy and applies default_values as initial transforms
plus keyframe animation.
"""

import bpy
import json
import os
import math
from mathutils import Euler, Matrix

from . import miobject_parser
from . import mesh_gen

from . import core

MI_TO_BLENDER_EASING_MAP = core.MI_TO_BLENDER_EASING_MAP
apply_mi_transition = core.apply_mi_transition
MIBaseImporter = core.MIBaseImporter

# --- Constants ---
MI_SCALE = 1.0 / 16.0


# ---- Helpers ---------------------------------------------------------------

def _operator_exists(path):
    """Return True if a Blender operator path like 'rig2.append_rig' exists."""
    try:
        module_name, op_name = path.split(".", 1)
        return hasattr(getattr(bpy.ops, module_name), op_name)
    except Exception:
        return False


def _is_rig2_armature(obj):
    if not obj or obj.type != 'ARMATURE' or not obj.pose:
        return False
    logic = obj.pose.bones.get("logic")
    return bool(logic and logic.get("is_rig2") == 1)


def _miobject_has_character(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return False

    for tl in data.get("timelines", []):
        if tl.get("type") == "char":
            return True
    return False


def _find_all_char_anchors(filepath, supported_node_ids):
    """
    Return a list of anchor_ids (one per char timeline), in timeline order.
    Each entry is the nearest ancestor id present in supported_node_ids, or None.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []

    timelines = data.get("timelines", [])
    by_id = {tl.get("id"): tl for tl in timelines}
    result = []

    for tl in timelines:
        if tl.get("type") != "char":
            continue
        anchor_id = None
        parent_id = tl.get("parent")
        while parent_id and parent_id != "root":
            if parent_id in supported_node_ids:
                anchor_id = parent_id
                break
            parent = by_id.get(parent_id)
            if not parent:
                break
            parent_id = parent.get("parent")
        result.append(anchor_id)

    return result


def _char_parent_chain_has_scale(filepath, anchor_id):
    """
    Return True if anchor or any of its ancestors has explicit non-unit scale
    (default_values or keyframes) in the source .miobject timeline.
    """
    if not anchor_id:
        return False

    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return False

    by_id = {tl.get("id"): tl for tl in data.get("timelines", [])}
    node_id = anchor_id
    while node_id and node_id != "root":
        tl = by_id.get(node_id)
        if not tl:
            break

        dv = tl.get("default_values", {})
        if any(float(dv.get(k, 1.0)) != 1.0 for k in ("SCA_X", "SCA_Y", "SCA_Z")):
            return True

        for vals in tl.get("keyframes", {}).values():
            if any(k in vals and float(vals.get(k, 1.0)) != 1.0 for k in ("SCA_X", "SCA_Y", "SCA_Z")):
                return True

        node_id = tl.get("parent")

    return False


def _append_rig2_armatures(context, count):
    """Append `count` Rig2 rigs and return list of newly-added armatures."""
    if not _operator_exists("rig2.append_rig"):
        return None, "Rig2 addon not available (operator rig2.append_rig not found)"

    existing = {obj.name for obj in bpy.data.objects if obj.type == 'ARMATURE'}
    new_armatures = []

    for _ in range(count):
        result = bpy.ops.rig2.append_rig()
        if 'FINISHED' not in result:
            return None, "rig2.append_rig failed"
        # Find the armature added in this iteration
        for obj in bpy.data.objects:
            if obj.type == 'ARMATURE' and obj.name not in existing and _is_rig2_armature(obj):
                # Make armature data single-user so multiple rigs don't share pose bone properties
                if obj.data.users > 1:
                    obj.data = obj.data.copy()
                new_armatures.append(obj)
                existing.add(obj.name)
                break

    if len(new_armatures) != count:
        return None, f"Expected {count} Rig2 armatures, got {len(new_armatures)}"

    return new_armatures, None


def _pick_parent_anchor(root_objects):
    if not root_objects:
        return None
    for obj in root_objects:
        if obj and obj.type == 'EMPTY':
            return obj
    return root_objects[0]


def _parent_keep_world(child, parent):
    """Parent child to parent while preserving child world transform."""
    world_mtx = child.matrix_world.copy()
    child.parent = parent
    child.matrix_world = world_mtx


def _bind_childof_follow(child, parent):
    """
    Bind child to follow parent with Child Of constraint, including scale.
    Rig2 is appended at world origin, so inverse should be cleared.
    """
    child.parent = None

    # Reuse existing constraint if present.
    con = child.constraints.get("MI2BL_Follow")
    if con is None:
        con = child.constraints.new(type='CHILD_OF')
        con.name = "MI2BL_Follow"

    con.target = parent
    con.use_location_x = True
    con.use_location_y = True
    con.use_location_z = True
    con.use_rotation_x = True
    con.use_rotation_y = True
    con.use_rotation_z = True
    con.use_scale_x = True
    con.use_scale_y = True
    con.use_scale_z = True

    # Clear inverse to avoid extra offset/scale amplification.
    con.inverse_matrix = Matrix.Identity(4)


def _import_with_rig2_miframes(context, armature, filepath, start_frame):
    if not _operator_exists("mi.import_action"):
        return False, "Rig2 miframes importer not available (operator mi.import_action not found)"

    prev_active = context.view_layer.objects.active
    prev_selected = list(context.selected_objects)

    try:
        bpy.ops.object.select_all(action='DESELECT')
        armature.select_set(True)
        context.view_layer.objects.active = armature

        if hasattr(armature, "rig2_props"):
            armature.rig2_props.mi_start_frame = start_frame

        result = bpy.ops.mi.import_action(filepath=filepath)
        if 'FINISHED' not in result:
            return False, "mi.import_action cancelled"
        return True, None
    except Exception as exc:
        return False, str(exc)
    finally:
        try:
            bpy.ops.object.select_all(action='DESELECT')
            for obj in prev_selected:
                if obj and obj.name in bpy.data.objects:
                    obj.select_set(True)
            if prev_active and prev_active.name in bpy.data.objects:
                context.view_layer.objects.active = prev_active
        except Exception:
            pass

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


# ---- Recursive Tree → Blender Objects --------------------------------------

def _create_blender_object(node, collection):
    """
    Create the appropriate Blender object for an MINode.
    Returns the bpy object.
    """
    display_name = node.display_name

    # Try to grab dimensions and texture data if template mapping exists
    size_3d = (16.0, 16.0, 16.0)
    uv = (0.0, 0.0)
    uv_repeat = (1.0, 1.0)
    texture_size = (64.0, 64.0)
    texture_mirror = (False, False) # h, v
    mapped = False
    invert = False
    
    if hasattr(node, "template_data") and node.template_data:
        # Blocks use top-level fields
        t_uv = node.template_data.get("uv", [0.0, 0.0])
        uv = tuple(t_uv) if isinstance(t_uv, list) else (0.0, 0.0)
        
        t_ts = node.template_data.get("texture_size", [64.0, 64.0])
        texture_size = tuple(t_ts) if isinstance(t_ts, list) else (64.0, 64.0)
        
        texture_mirror = (node.template_data.get("tex_hmirror", False), 
                          node.template_data.get("tex_vmirror", False))
        
        t_sz = node.template_data.get("size", [16.0, 16.0, 16.0])
        size_3d = tuple(t_sz) if isinstance(t_sz, list) else (16.0, 16.0, 16.0)

        # Cubes and Surfaces use "shape" object for these
        shape = node.template_data.get("shape", {})
        if shape:
            mapped = shape.get("tex_mapped", False)
            invert = shape.get("invert", False)
            uv = (shape.get("tex_hoffset", 0.0), shape.get("tex_voffset", 0.0))
            uv_repeat = (shape.get("tex_hrepeat", 1.0), shape.get("tex_vrepeat", 1.0))
            texture_mirror = (shape.get("tex_hmirror", False), shape.get("tex_vmirror", False))

    if node.type == "folder":
        obj = bpy.data.objects.new(display_name, None)  # Empty
        obj.empty_display_type = 'PLAIN_AXES'
        obj.empty_display_size = 0.5
        collection.objects.link(obj)

    elif node.type == "cube":
        obj = mesh_gen.create_mi_cube(name=display_name, size=1.0, 
                                      rot_point=getattr(node, "rot_point", [0,-8,0]),
                                      collection=collection,
                                      mapped=mapped, uv_offset=uv, uv_repeat=uv_repeat,
                                      hmirror=texture_mirror[0], vmirror=texture_mirror[1],
                                      invert=invert)

    elif node.type == "surface":
        obj = mesh_gen.create_mi_surface(name=display_name, size=1.0, size_3d=(size_3d[0], size_3d[2]), 
                                         rot_point=getattr(node, "rot_point", [0,-8,0]),
                                         collection=collection, uv_offset=uv, uv_repeat=uv_repeat, 
                                         texture_size=texture_size,
                                         hmirror=texture_mirror[0], vmirror=texture_mirror[1])

    elif node.type == "block":
        obj = mesh_gen.create_mi_block(name=display_name, size_3d=size_3d, 
                                       rot_point=getattr(node, "rot_point", None),
                                       collection=collection, uv=uv, texture_size=texture_size, 
                                       texture_mirror=texture_mirror)
    elif node.type == "camera":
        # Create an Empty to hold the MI transform animation
        obj = bpy.data.objects.new(display_name, None)
        obj.empty_display_type = 'PLAIN_AXES'
        collection.objects.link(obj)
        
        # Create a Blender Camera Object setup properly, parented to the Empty
        cam_data = bpy.data.cameras.new(display_name + "_Data")
        cam_obj = bpy.data.objects.new(display_name + "_Lens", cam_data)
        collection.objects.link(cam_obj)
        
        # Parent the camera to the Empty
        cam_obj.parent = obj
        
        # MI cameras face South (-Y) and are upright (+Z) when rotation is 0.
        # Blender cameras face Down (-Z) with Up (+Y) when rotation is 0.
        # Rotating the camera lens itself by X=+90deg, Z=180deg perfectly aligns it to MI's default.
        cam_obj.rotation_mode = 'XYZ'
        cam_obj.rotation_euler = (math.pi/2, 0, math.pi)
        
        # Lock its transforms since it's just a proxy for the lens
        for i in range(3):
            cam_obj.lock_location[i] = True
            cam_obj.lock_rotation[i] = True
            cam_obj.lock_scale[i] = True
            
        # Attempt to inject properties
        if hasattr(node, "keyframes") and 0 in node.keyframes:
            f0 = node.keyframes[0]
            if "CAM_FOV" in f0:
                cam_data.angle = math.radians(f0["CAM_FOV"])
                
    elif node.type in ("pointlight", "spotlight"):
        obj = bpy.data.objects.new(display_name, None)
        obj.empty_display_type = 'PLAIN_AXES'
        collection.objects.link(obj)
        
        l_type = 'POINT' if node.type == "pointlight" else 'SPOT'
        light_data = bpy.data.lights.new(name=display_name + "_Data", type=l_type)
        light_obj = bpy.data.objects.new(display_name + "_Light", light_data)
        collection.objects.link(light_obj)
        light_obj.parent = obj
        
        # Spotlights, like cameras, point downwards by default and need offset
        if l_type == 'SPOT':
            light_obj.rotation_mode = 'XYZ'
            light_obj.rotation_euler = (math.pi/2, 0, math.pi)
            
        for i in range(3):
            light_obj.lock_location[i] = True
            light_obj.lock_rotation[i] = True
            light_obj.lock_scale[i] = True
                
    elif node.type == "audio":
        obj = bpy.data.objects.new(display_name, None)
        obj.empty_display_type = 'SPHERE'
        collection.objects.link(obj)
        
    elif node.type == "text":
        text_data = bpy.data.curves.new(type="FONT", name=display_name + "_TextData")
        text_data.body = display_name # placeholder text until properly hooked
        obj = bpy.data.objects.new(display_name, text_data)
        collection.objects.link(obj)
    
    elif node.type in ["char", "scenery", "item", "bodypart", "particle_spawner"]:
        # Character Root/Groups / Folders placeholder
        obj = bpy.data.objects.new(display_name, None)
        obj.empty_display_type = 'PLAIN_AXES'
        collection.objects.link(obj)
        
    else:
        # Unsupported – create an empty as placeholder
        obj = bpy.data.objects.new(display_name, None)
        obj.empty_display_type = 'CUBE'
        collection.objects.link(obj)

    return obj


def _build_tree(node, parent_obj, collection, start_frame, fps_scale, object_map=None, disable_scale_node_ids=None):
    """Recursively create Blender objects from an MINode tree."""
    obj = _create_blender_object(node, collection)
    if object_map is not None:
        object_map[node.id] = obj
    disable_scale = bool(disable_scale_node_ids and node.id in disable_scale_node_ids)

    # Parent
    if parent_obj is not None:
        obj.parent = parent_obj

    # Apply default values as rest transform
    _apply_default_transform(obj, node, disable_scale=disable_scale)

    # Apply keyframe animation
    if node.keyframes:
        kf_trans = _apply_keyframes(obj, node, start_frame, fps_scale, disable_scale=disable_scale)
        _apply_interpolation_to_obj(obj, kf_trans)
        
    if node.type in ("pointlight", "spotlight") and obj.children:
        _apply_light_properties(obj.children[0], node, start_frame, fps_scale)

    # Recurse children (skip children of char nodes — Rig2 handles their motion)
    if node.type != "char":
        for child in node.children:
            _build_tree(child, obj, collection, start_frame, fps_scale, object_map, disable_scale_node_ids)
    return obj


# ---- Operator --------------------------------------------------------------

class MI_OT_ImportMiobjectScene(bpy.types.Operator):
    """Import a .miobject file as a scene hierarchy (folders, cubes, surfaces)"""
    bl_idname = "mi.import_miobject_scene"
    bl_label = "Import MI Object Scene"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(
        default="*.miobject",
        options={'HIDDEN'},
        maxlen=255,
    )

    start_frame: bpy.props.IntProperty(
        name="Start Frame",
        description="Frame at which to start inserting animation",
        default=1,
        min=0,
    )

    adjust_end_frame: bpy.props.BoolProperty(
        name="Adjust End Frame",
        description="Automatically adjust the scene end frame to match animation length",
        default=True,
    )

    use_collection: bpy.props.BoolProperty(
        name="Create Collection",
        description="Put imported objects into a new collection named after the file",
        default=True,
    )

    auto_append_rig2: bpy.props.BoolProperty(
        name="Auto Append Rig2",
        description="Automatically append Rig2 binding after importing .miobject",
        default=True,
    )

    auto_import_rig2_action: bpy.props.BoolProperty(
        name="Auto Import Character Action",
        description="If the .miobject contains a character timeline, use Rig2 miframes importer for character motion",
        default=True,
    )

    def execute(self, context):
        if not self.filepath:
            self.report({'ERROR'}, "No file selected")
            return {'CANCELLED'}

        ext = os.path.splitext(self.filepath)[1].lower()
        if ext != ".miobject":
            self.report({'ERROR'}, "Only .miobject files are supported")
            return {'CANCELLED'}

        # Parse
        try:
            roots, all_nodes, _meta = miobject_parser.parse_miobject(self.filepath)
        except Exception as e:
            self.report({'ERROR'}, f"Parse error: {e}")
            return {'CANCELLED'}

        has_character_timeline = _miobject_has_character(self.filepath)
        if not roots and not has_character_timeline:
            self.report({'WARNING'}, "No supported objects found in file")
            return {'CANCELLED'}

        # Scene timing
        # MI default tempo is 24 fps
        # We don't have a top-level "tempo" in .miobject, default to scene fps
        fps_scale = 1.0  # 1:1 frame mapping (miobject frames = blender frames)

        if roots:
            # Find animation length from all keyframes
            max_frame = 0
            for node in all_nodes.values():
                if node.keyframes:
                    max_frame = max(max_frame, max(node.keyframes.keys()))

            if self.adjust_end_frame and max_frame > 0:
                end = self.start_frame + int(max_frame * fps_scale)
                context.scene.frame_end = max(context.scene.frame_end, end)

            # Collection
            if self.use_collection:
                col_name = os.path.splitext(os.path.basename(self.filepath))[0]
                collection = bpy.data.collections.new(col_name)
                context.collection.children.link(collection)
            else:
                collection = context.collection

            imported_root_objects = []
            imported_object_map = {}
            # Build tree
            for root_node in roots:
                root_obj = _build_tree(root_node, None, collection,
                                       self.start_frame, fps_scale, imported_object_map, None)
                imported_root_objects.append(root_obj)
        else:
            imported_root_objects = []
            imported_object_map = {}

        rig2_status = None
        if self.auto_append_rig2 and has_character_timeline:
            char_anchors = _find_all_char_anchors(
                self.filepath, set(imported_object_map.keys())
            ) if imported_object_map else []
            char_count = max(len(char_anchors), 1)

            armatures, err = _append_rig2_armatures(context, char_count)
            if err:
                rig2_status = f"Rig2: {err}"
            else:
                rig2_status = f"Rig2: appended {char_count}"
                for i, rig2_armature in enumerate(armatures):
                    anchor_id = char_anchors[i] if i < len(char_anchors) else None
                    anchor = imported_object_map.get(anchor_id) if anchor_id else None
                    if anchor is None:
                        anchor = _pick_parent_anchor(imported_root_objects)
                    if anchor:
                        try:
                            has_scale_chain = _char_parent_chain_has_scale(self.filepath, anchor_id)
                            if has_scale_chain:
                                _bind_childof_follow(rig2_armature, anchor)
                            else:
                                _parent_keep_world(rig2_armature, anchor)
                        except Exception as exc:
                            rig2_status += f", parent[{i}] failed ({exc})"
                    if self.auto_import_rig2_action:
                        if hasattr(rig2_armature, "rig2_props"):
                            rig2_armature.rig2_props.mi_char_index = i
                        ok, import_err = _import_with_rig2_miframes(
                            context, rig2_armature, self.filepath, self.start_frame,
                        )
                        if not ok:
                            rig2_status += f", miframes[{i}] failed ({import_err})"

        elif self.auto_append_rig2 and not has_character_timeline:
            rig2_status = "Rig2: skipped (no character timeline)"

        msg = (
            f"Imported {len(all_nodes)} objects from "
            f"{os.path.basename(self.filepath)}"
        )
        if rig2_status:
            msg += f" | {rig2_status}"
        self.report({'INFO'}, msg)
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


# ---- Menu entry ------------------------------------------------------------

def menu_func_import(self, context):
    self.layout.operator(
        MI_OT_ImportMiobjectScene.bl_idname,
        text="Mine-Imator Object (.miobject)",
        icon='IMPORT',
    )


# ---- Registration ----------------------------------------------------------

classes = (
    MI_OT_ImportMiobjectScene,
)


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception:
            pass
    try:
        bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    except Exception:
        pass


def unregister():
    try:
        bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    except Exception:
        pass
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass

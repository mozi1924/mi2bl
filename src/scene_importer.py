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

# Reuse easing helpers from miframes
from .miframes_bridge import importer as _mi_imp

MI_TO_BLENDER_EASING_MAP = _mi_imp.MI_TO_BLENDER_EASING_MAP
apply_mi_transition = _mi_imp.apply_mi_transition
MIBaseImporter = _mi_imp.MIBaseImporter

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


def _find_character_parent_anchor_id(filepath, supported_node_ids):
    """
    Find the nearest ancestor of a character timeline that exists in imported
    supported nodes (folder/cube/surface). Returns timeline id or None.
    """
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None

    timelines = data.get("timelines", [])
    by_id = {tl.get("id"): tl for tl in timelines}

    for tl in timelines:
        if tl.get("type") != "char":
            continue
        parent_id = tl.get("parent")
        while parent_id and parent_id != "root":
            if parent_id in supported_node_ids:
                return parent_id
            parent = by_id.get(parent_id)
            if not parent:
                break
            parent_id = parent.get("parent")

    return None


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


def _append_rig2_and_get_armature(context):
    if not _operator_exists("rig2.append_rig"):
        return None, "Rig2 addon not available (operator rig2.append_rig not found)"

    existing = {obj.name for obj in bpy.data.objects if obj.type == 'ARMATURE'}

    result = bpy.ops.rig2.append_rig()
    if 'FINISHED' not in result:
        return None, "rig2.append_rig failed"

    # Prefer newly-added Rig2 armature, fall back to current active armature.
    for obj in bpy.data.objects:
        if obj.type == 'ARMATURE' and obj.name not in existing and _is_rig2_armature(obj):
            return obj, None

    active = context.view_layer.objects.active
    if _is_rig2_armature(active):
        return active, None

    for obj in bpy.data.objects:
        if _is_rig2_armature(obj):
            return obj, None

    return None, "Rig2 appended but no Rig2 armature found"


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
    obj.rotation_euler = Euler((rx, -rz, ry), 'XYZ')

    # Scale
    sx = dv.get("SCA_X", 1.0)
    sy = dv.get("SCA_Y", 1.0)  # UI Y → BL Z
    sz = dv.get("SCA_Z", 1.0)  # UI Z → BL Y
    obj.scale = (sx, sz, sy)


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
                    apply_mi_transition(kf0, t_type)
            fcurve.update()


# ---- Recursive Tree → Blender Objects --------------------------------------

def _create_blender_object(node, collection):
    """
    Create the appropriate Blender object for an MINode.
    Returns the bpy object.
    """
    display_name = node.display_name

    if node.type == "folder":
        obj = bpy.data.objects.new(display_name, None)  # Empty
        obj.empty_display_type = 'PLAIN_AXES'
        obj.empty_display_size = 0.5
        collection.objects.link(obj)

    elif node.type == "cube":
        obj = mesh_gen.create_mi_cube(name=display_name, size=1.0,
                                       collection=collection)

    elif node.type == "surface":
        obj = mesh_gen.create_mi_surface(name=display_name, size=1.0,
                                          collection=collection)
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

    # Recurse children
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
                context.scene.collection.children.link(collection)
            else:
                collection = context.scene.collection

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
        if self.auto_append_rig2:
            rig2_armature, err = _append_rig2_and_get_armature(context)
            if err:
                rig2_status = f"Rig2: {err}"
            else:
                rig2_status = "Rig2: appended"
                anchor = None
                anchor_id = None
                if imported_object_map and has_character_timeline:
                    anchor_id = _find_character_parent_anchor_id(
                        self.filepath,
                        set(imported_object_map.keys()),
                    )
                    if anchor_id:
                        anchor = imported_object_map.get(anchor_id)
                if anchor is None:
                    anchor = _pick_parent_anchor(imported_root_objects)
                if anchor:
                    try:
                        has_scale_chain = _char_parent_chain_has_scale(self.filepath, anchor_id)
                        if has_scale_chain:
                            _bind_childof_follow(rig2_armature, anchor)
                            rig2_status += f", bound(child-of) to '{anchor.name}'"
                        else:
                            _parent_keep_world(rig2_armature, anchor)
                            rig2_status += f", parented to '{anchor.name}'"
                    except Exception as exc:
                        rig2_status += f", parent failed ({exc})"
                if self.auto_import_rig2_action and has_character_timeline:
                    ok, import_err = _import_with_rig2_miframes(
                        context,
                        rig2_armature,
                        self.filepath,
                        self.start_frame,
                    )
                    if ok:
                        rig2_status += ", character action imported via Rig2 miframes"
                    else:
                        rig2_status += f", miframes import failed ({import_err})"
                elif self.auto_import_rig2_action:
                    rig2_status += ", no character timeline found"

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
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

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
from mathutils import Euler

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

def _apply_default_transform(obj, node):
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


def _apply_keyframes(obj, node, start_frame, fps_scale):
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


def _build_tree(node, parent_obj, collection, start_frame, fps_scale):
    """Recursively create Blender objects from an MINode tree."""
    obj = _create_blender_object(node, collection)

    # Parent
    if parent_obj is not None:
        obj.parent = parent_obj

    # Apply default values as rest transform
    _apply_default_transform(obj, node)

    # Apply keyframe animation
    if node.keyframes:
        kf_trans = _apply_keyframes(obj, node, start_frame, fps_scale)
        _apply_interpolation_to_obj(obj, kf_trans)

    # Recurse children
    for child in node.children:
        _build_tree(child, obj, collection, start_frame, fps_scale)


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
            roots, all_nodes, meta = miobject_parser.parse_miobject(self.filepath)
        except Exception as e:
            self.report({'ERROR'}, f"Parse error: {e}")
            return {'CANCELLED'}

        if not roots:
            self.report({'WARNING'}, "No supported objects found in file")
            return {'CANCELLED'}

        # Scene timing
        # MI default tempo is 24 fps
        # We don't have a top-level "tempo" in .miobject, default to scene fps
        fps_current = context.scene.render.fps
        fps_scale = 1.0  # 1:1 frame mapping (miobject frames = blender frames)

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

        # Build tree
        for root_node in roots:
            _build_tree(root_node, None, collection,
                        self.start_frame, fps_scale)

        self.report({'INFO'},
                    f"Imported {len(all_nodes)} objects from "
                    f"{os.path.basename(self.filepath)}")
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

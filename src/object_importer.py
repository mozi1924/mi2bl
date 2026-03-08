import bpy
import json
import os
import math
import re
from mathutils import Euler, Vector

from . import core
from .core import MIBaseImporter, apply_mi_transition

# --- Constants ---
MI_SCALE = 1.0 / 16.0


class MI_OT_ImportObjectAction(bpy.types.Operator, MIBaseImporter):
    """Import a non-model .miframes file onto the active object's transforms"""
    bl_idname = "mi.import_object_action"
    bl_label = "Load Object .miframes"
    bl_options = {'REGISTER', 'UNDO'}
    
    confirmed: bpy.props.BoolProperty(default=False)
    
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(
        default="*.miframes;*.miobject",
        options={'HIDDEN'},
        maxlen=255
    )

    def execute(self, context):
        obj = context.active_object
        if not obj:
            self.report({'ERROR'}, "No active object selected")
            return {'CANCELLED'}

        data, err = self.check_file(self.filepath)
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}

        # Validate that this is a non-model miframes file
        is_model = data.get("is_model", True)
        if is_model:
            # If it's a character model, we SHOULD use Rig2.
            if hasattr(bpy.ops, "mi") and hasattr(bpy.ops.mi, "import_action"):
                self.report({'WARNING'},
                            "This file is a character model. Using Rig2 importer instead.")
                return bpy.ops.mi.import_action('INVOKE_DEFAULT', filepath=self.filepath)
            else:
                self.report({'ERROR'},
                            "This file contains a Steve/Alex character model. "
                            "No model or animation will be imported — please install Rig2.")
                return {'CANCELLED'}

        # --- Timing ---
        tempo, fps_current, fps_scale = self.setup_scene(
            context, data, 
            obj.mi_object_props.start_frame, 
            obj.mi_object_props.adjust_end_frame
        )
        start_frame = obj.mi_object_props.start_frame

        # Collect transition info per keyframe time for later curve adjustment
        kf_trans_list = []

        for kf in data.get("keyframes", []):
            time = start_frame + (kf.get("position", 0) * fps_scale)
            values = kf.get("values", {})

            # --- Extract Transition Info ---
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
                loc[1] = -values["POS_Z"] * MI_SCALE
                has_pos = True
            if "POS_Y" in values:
                loc[2] = values["POS_Y"] * MI_SCALE
                has_pos = True
            if has_pos:
                obj.location = (loc[0], loc[1], loc[2])
                obj.keyframe_insert("location", frame=time)

            # --- Rotation ---
            has_rot = False
            rot_vals = list(obj.rotation_euler) if obj.rotation_mode == 'XYZ' \
                else [0.0, 0.0, 0.0]
            if "ROT_X" in values:
                rot_vals[0] = math.radians(values["ROT_X"])
                has_rot = True
            if "ROT_Z" in values:
                rot_vals[1] = math.radians(-values["ROT_Z"])
                has_rot = True
            if "ROT_Y" in values:
                rot_vals[2] = math.radians(values["ROT_Y"])
            if has_rot:
                obj.rotation_mode = 'XYZ'
                obj.rotation_euler = Euler((rot_vals[0], rot_vals[1], rot_vals[2]), 'XYZ')
                obj.keyframe_insert("rotation_euler", frame=time)

            # --- Scale ---
            has_scl = False
            scl = list(obj.scale)
            if "SCA_X" in values:
                scl[0] = values["SCA_X"]
                has_scl = True
            if "SCA_Z" in values:
                scl[1] = values["SCA_Z"]
                has_scl = True
            if "SCA_Y" in values:
                scl[2] = values["SCA_Y"]
                has_scl = True
            if has_scl:
                obj.scale = (scl[0], scl[1], scl[2])
                obj.keyframe_insert("scale", frame=time)

        # --- Apply Interpolation ---
        if obj.animation_data and obj.animation_data.action:
            action = obj.animation_data.action
            for fcurve in action.fcurves:
                if fcurve.data_path in ("location", "rotation_euler", "scale"):
                    self.apply_interpolation(fcurve, kf_trans_list)

        self.report({'INFO'}, f"Imported successfully (tempo: {tempo})")
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

# --- Registration ---

classes = (
    MI_OT_ImportObjectAction,
)


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception:
            pass


def unregister():
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass

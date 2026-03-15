import bpy
import os
import json
from ..parsers import miobject_parser
from ..parsers.miobject_parser import parse_miobject
from ..utils.core import parse_mi_file_data
from ..utils.rig2_utils import (
    _miobject_has_character, _find_all_char_anchors, _char_parent_chain_has_scale,
    _append_rig2_armatures, _pick_parent_anchor, _bind_childof_follow,
    _parent_keep_world, _import_with_rig2_miframes, _bind_ik_copy_location,
    _bind_ik_constraint
)
from ..scene.builder import _build_tree, build_scene

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

            # Build scene tree (first pass: objects; second pass: Follow Path constraints)
            imported_object_map = build_scene(
                roots, all_nodes, collection,
                start_frame=self.start_frame,
                fps_scale=fps_scale,
            )
            imported_root_objects = [
                imported_object_map[n.id]
                for n in roots
                if n.id in imported_object_map
            ]
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

                    # --- IK Copy Location binding (scene_importer only) ---
                    # Now that imported_object_map is fully populated with MI id →
                    # Blender object mappings, bind the Rig2 IK bones with Copy
                    # Location constraints pointing to those scene objects.
                    ik_bound = _apply_ik_constraints_from_miobject(
                        rig2_armature, self.filepath, i, imported_object_map,
                    )
                    if ik_bound:
                        rig2_status += f", IK bound: {','.join(ik_bound)}"

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


def _apply_ik_constraints_from_miobject(armature, filepath, char_index, object_map):
    """
    Parse the miobject file, extract IK target / pole MI object IDs for
    the given character index, look them up in object_map, then bind the
    Rig2 IK bones with Copy Location constraints.

    Parameters
    ----------
    armature     : bpy.types.Object              — Rig2 armature
    filepath     : str                           — path to .miobject file
    char_index   : int                           — which character timeline (0-based)
    object_map   : dict[str, bpy.types.Object]   — MI id → Blender object

    Returns
    -------
    bound : list[str]  list of part_names that got IK constraints, or []
    """
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            raw_data = json.load(fh)
    except Exception:
        return []

    # parse_mi_file_data extracts ik_data with target_id / pole_id
    parsed = parse_mi_file_data(raw_data, char_index=char_index)
    ik_data = parsed.get("ik_data", {})
    if not ik_data:
        return []

    # Get ik_targets config from the active rig2 model config
    ik_targets_cfg = {}
    try:
        from rig2_addons.src.modules.rig_controls.miframes.configs import MODELS
        model_key = getattr(getattr(armature, "rig2_props", None), "mi_selected_model", "steve")
        cfg = MODELS.get(model_key, {})
        ik_targets_cfg = cfg.get("ik_targets", {})
    except Exception:
        pass

    if not ik_targets_cfg:
        # Fallback: hardcoded defaults matching ik_item.txt
        ik_targets_cfg = {
            "left_arm":  {"ik_target_bone": "MI_arm.ik.target.L", "ik_pole_bone": "MI_arm.ik.pt.L",  "logic_ik_prop": "mi_ik_arm.L"},
            "right_arm": {"ik_target_bone": "MI_arm.ik.target.R", "ik_pole_bone": "MI_arm.ik.pt.R",  "logic_ik_prop": "mi_ik_arm.R"},
            "left_leg":  {"ik_target_bone": "MI_leg.ik.target.L", "ik_pole_bone": "MI_leg.ik.pt.L",  "logic_ik_prop": "mi_ik_leg.L"},
            "right_leg": {"ik_target_bone": "MI_leg.ik.target.R", "ik_pole_bone": "MI_leg.ik.pt.R",  "logic_ik_prop": "mi_ik_leg.R"},
        }

    _bind_ik_copy_location(armature, ik_data, ik_targets_cfg, object_map)
    return _bind_ik_constraint(armature, ik_data, ik_targets_cfg, object_map)


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

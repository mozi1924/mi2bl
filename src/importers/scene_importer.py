import bpy
import os
from ..parsers import miobject_parser
from ..utils.rig2_utils import (
    _miobject_has_character, _find_all_char_anchors, _char_parent_chain_has_scale,
    _append_rig2_armatures, _pick_parent_anchor, _bind_childof_follow, _parent_keep_world, _import_with_rig2_miframes
)
from ..scene.builder import _build_tree

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

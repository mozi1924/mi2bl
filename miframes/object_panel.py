import bpy
from bpy.props import IntProperty, BoolProperty, PointerProperty


# ─── Per-Object properties for MI Object Animation ───────────────────────────

class MIObjectProperties(bpy.types.PropertyGroup):
    start_frame: IntProperty(
        name="Start Frame",
        description="Frame at which to start inserting the animation",
        default=1,
        min=0
    )

    adjust_end_frame: BoolProperty(
        name="Adjust End Frame",
        description="Automatically adjust scene end frame to match animation length",
        default=True
    )


# ─── N-Panel: Mine-Imator Object Animation ───────────────────────────────────

class MI_PT_ObjectAnimBase:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Rig/2'

    @classmethod
    def poll(cls, context):
        return context.active_object is not None


class MI_PT_ObjectAnimPanel(MI_PT_ObjectAnimBase, bpy.types.Panel):
    bl_label = "Object Animation (.mi*)"
    bl_idname = "MI_PT_object_anim_panel"
    bl_order = 100  # Show after other Rig/2 panels

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        if not obj:
            return

        mi_props = obj.mi_object_props

        # --- Header info ---
        layout.label(text=f"Target: {obj.name}", icon='OBJECT_DATA')
        layout.separator()

        # --- Settings ---
        box = layout.box()
        box.label(text="Settings", icon='PREFERENCES')
        col = box.column(align=True)
        row = col.row(align=True)
        row.prop(mi_props, "start_frame", text="Start At")
        row.prop(mi_props, "adjust_end_frame", text="Auto End", toggle=True)

        layout.separator()

        # --- Import Action ---
        box = layout.box()
        box.label(text="Import", icon='IMPORT')
        box.operator("mi.import_object_action",
                      text="Load Anim (.mi*)", icon='FILE_TICK')


# ─── Registration ────────────────────────────────────────────────────────────

classes = (
    MIObjectProperties,
    MI_PT_ObjectAnimPanel,
)


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            print(f"MI Object Panel: Error registering {cls}: {e}")
    try:
        bpy.types.Object.mi_object_props = PointerProperty(
            type=MIObjectProperties)
    except Exception as e:
        print(f"MI Object Panel: Error registering mi_object_props: {e}")


def unregister():
    try:
        del bpy.types.Object.mi_object_props
    except Exception:
        pass
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass

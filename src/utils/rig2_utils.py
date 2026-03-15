import bpy
import json
import math
from mathutils import Matrix

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
    in keyframes in the source .miobject timeline.

    NOTE: `default_values` is the MI creation placement — it is NOT checked here
    because mi2bl ignores creation placement when importing (objects are placed
    at origin; keyframes define the full animated trajectory).
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

        # Only check keyframes — default_values is creation placement, not property defaults
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


def _bind_ik_copy_location(armature, ik_data, ik_targets_cfg, object_map):
    """
    For each limb part that has IK references, add Copy Location constraints
    on the Rig2 IK target / pole target bones pointing to the corresponding
    imported scene objects, and activate the logic bone IK switch.

    Parameters
    ----------
    armature       : bpy.types.Object   — Rig2 armature
    ik_data        : dict               — from parse_mi_file_data()["ik_data"]
                     format: { part_name: { "target_id": str, "pole_id": str } }
    ik_targets_cfg : dict               — from config["ik_targets"]
    object_map     : dict[str, bpy.Object] — MI id → Blender object

    Returns
    -------
    bound : list[str]  — part names for which at least one constraint was added
    """
    if not armature or armature.type != 'ARMATURE':
        return []

    pose_bones = armature.pose.bones
    bound = []

    for part_name, ik_cfg in ik_targets_cfg.items():
        part_ik = ik_data.get(part_name)
        if not part_ik:
            continue

        target_bone_name = ik_cfg.get("ik_target_bone")
        pole_bone_name   = ik_cfg.get("ik_pole_bone")
        logic_prop       = ik_cfg.get("logic_ik_prop")
        did_bind = False

        # --- IK Target: Copy Location constraint ---
        target_mi_id = part_ik.get("target_id")
        if target_bone_name and target_mi_id:
            target_obj = object_map.get(target_mi_id)
            t_bone = pose_bones.get(target_bone_name)
            if target_obj and t_bone:
                con_name = f"MI2BL_IK_target"
                # Remove existing constraint with same name to allow re-import
                existing = t_bone.constraints.get(con_name)
                if existing:
                    t_bone.constraints.remove(existing)
                con = t_bone.constraints.new(type='COPY_LOCATION')
                con.name = con_name
                
                # For objects that have been converted to Pivot -> Data Proxy structure,
                # we want the bone to track the Pivot (target_obj), but if the user
                # specifically wants the constraint on a child, we could adjust.
                # However, the task states "让骨骼复制变换这个空物体" (let the bone copy
                # the transform of this empty object/pivot).
                con.target = target_obj
                con.use_offset = False
                did_bind = True

        # --- Pole Target: Copy Location constraint ---
        pole_mi_id = part_ik.get("pole_id")
        if pole_bone_name and pole_mi_id:
            pole_obj = object_map.get(pole_mi_id)
            p_bone = pose_bones.get(pole_bone_name)
            if pole_obj and p_bone:
                con_name = f"MI2BL_IK_pole"
                existing = p_bone.constraints.get(con_name)
                if existing:
                    p_bone.constraints.remove(existing)
                con = p_bone.constraints.new(type='COPY_LOCATION')
                con.name = con_name
                con.target = pole_obj
                con.use_offset = False
                did_bind = True

        # --- Logic IK switch: set to 1.0 when any binding succeeded ---
        if did_bind and logic_prop and "logic" in pose_bones:
            logic_bone = pose_bones["logic"]
            if logic_prop in logic_bone:
                logic_bone[logic_prop] = 1.0
            bound.append(part_name)

    return bound


def _bind_ik_constraint(armature, ik_data, ik_targets_cfg, object_map):
    """
    Apply standard IK constraints to the actual limb bones (lower parts),
    pointing to the targets and poles that are already linked via Copy Location.
    
    In Rig2, the IK structure is:
    - Target Bone: The bone that moves the limb (Copy Location to Scene Pivot).
    - Pole Bone: The bone that controls orientation (Copy Location to Scene Pivot).
    - Actual Bone (e.g. lower arm): Has the IK constraint targeting the Target/Pole bones.
    """
    if not armature or armature.type != 'ARMATURE':
        return []

    pose_bones = armature.pose.bones
    bound = []

    # Map MI part names to IK solver bones
    # From ik_item.txt:
    # MI_arm.lower.ik.L / R
    # MI_leg.lower.ik.L / R
    ik_solver_bones = {
        "left_arm":  "MI_arm.lower.ik.L",
        "right_arm": "MI_arm.lower.ik.R",
        "left_leg":  "MI_leg.lower.ik.L",
        "right_leg": "MI_leg.lower.ik.R",
    }

    for part_name, solver_bone_name in ik_solver_bones.items():
        part_ik = ik_data.get(part_name)
        ik_cfg = ik_targets_cfg.get(part_name)
        if not part_ik or not ik_cfg:
            continue

        s_bone = pose_bones.get(solver_bone_name)
        if not s_bone:
            continue

        con_name = "MI2BL_IK_Solver"
        existing = s_bone.constraints.get(con_name)
        if existing:
            s_bone.constraints.remove(existing)

        # Only add if we have at least a target
        target_mi_id = part_ik.get("target_id")
        if not target_mi_id:
            continue

        con = s_bone.constraints.new(type='IK')
        con.name = con_name
        con.target = armature
        con.subtarget = ik_cfg.get("ik_target_bone")
        con.chain_count = 2

        # Pole target
        pole_mi_id = part_ik.get("pole_id")
        if pole_mi_id:
            con.pole_target = armature
            con.pole_subtarget = ik_cfg.get("ik_pole_bone")
            
            # Base Pole Angle: -90 degrees as per report
            # We'll keyframe mi_angle_offset later to handle delta
            con.pole_angle = math.radians(-90.0)

        # Influence/Blend
        # Initial value, will be keyframed by mi_blend
        con.influence = 1.0
        
        bound.append(part_name)

    return bound


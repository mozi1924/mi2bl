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

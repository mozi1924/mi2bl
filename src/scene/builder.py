"""
scene/builder.py — Recursively build the Blender scene from an MINode tree.

Responsibilities:
  1. Create the appropriate Blender object for each MINode type.
  2. Set up parent-child relationships.
  3. Set the object's initial transform:  
     - Has keyframes → clear to identity, then apply_keyframes writes the full
       animated trajectory.  Keyframes already encode the full absolute position
       (parser merged: MI_HARD_DEFAULTS ← default_values ← per-frame delta).
     - No keyframes → apply default_values as the static rest transform.
       (default_values is layer-2 of the three-layer system; it is the exact
       position the user placed the object in MI.)
  4. Apply keyframe animation (transform channels via animator.apply_keyframes).
  5. Apply keyframe-able custom props for ALL MI value tracks (via props module).
  6. Apply static appearance flags as non-animated custom props.
  7. Handle special child-object setup for cameras and lights.
  8. Handle Path objects (NURBS curve) and Follow Path constraints.

Path system:
  - MI "path" node  → Blender NURBS curve (Order 3, matching MI quadratic B-spline).
  - MI "pathpoint" nodes are the curve's control points (children of "path").
    They are not created as Blender objects; their positions are baked into the
    NURBS spline at frame 0.
  - Any MI object whose keyframes contain PATH_OBJ + PATH_OFFSET receives a
    Blender FOLLOW_PATH constraint targeting the corresponding curve object.
"""

import bpy
import math
from ..scene import mesh_gen
from ..utils.transforms import clear_transform
from ..scene.animator import apply_keyframes, apply_interpolation_to_obj, apply_default_values_transform
from ..scene.props import apply_node_custom_props, apply_node_static_props, store_mi_placement
from ..scene.lights import apply_light_properties
from ..scene.cameras import apply_camera_properties
from ..scene.paths import create_path_curve, apply_follow_path_constraint


def _create_blender_object(node, collection):
    """
    Create the appropriate Blender object for an MINode.
    Returns the bpy object (always the "pivot" object for compound types).
    """
    display_name = node.display_name

    # ── Template / shape data ─────────────────────────────────────────────────
    size_3d       = (16.0, 16.0, 16.0)
    uv            = (0.0, 0.0)
    uv_repeat     = (1.0, 1.0)
    texture_size  = (64.0, 64.0)
    texture_mirror = (False, False)  # (h_mirror, v_mirror)
    mapped = False
    invert = False

    if hasattr(node, "template_data") and node.template_data:
        t_uv = node.template_data.get("uv", [0.0, 0.0])
        uv = tuple(t_uv) if isinstance(t_uv, list) else (0.0, 0.0)

        t_ts = node.template_data.get("texture_size", [64.0, 64.0])
        texture_size = tuple(t_ts) if isinstance(t_ts, list) else (64.0, 64.0)

        texture_mirror = (
            node.template_data.get("tex_hmirror", False),
            node.template_data.get("tex_vmirror", False),
        )

        t_sz = node.template_data.get("size", [16.0, 16.0, 16.0])
        size_3d = tuple(t_sz) if isinstance(t_sz, list) else (16.0, 16.0, 16.0)

        # Cube / Surface use a nested "shape" object
        shape = node.template_data.get("shape", {})
        if shape:
            mapped = shape.get("tex_mapped", False)
            invert = shape.get("invert", False)
            uv = (shape.get("tex_hoffset", 0.0), shape.get("tex_voffset", 0.0))
            uv_repeat = (shape.get("tex_hrepeat", 1.0), shape.get("tex_vrepeat", 1.0))
            texture_mirror = (
                shape.get("tex_hmirror", False),
                shape.get("tex_vmirror", False),
            )

    # ── Object creation by type ───────────────────────────────────────────────

    if node.type == "folder":
        obj = bpy.data.objects.new(display_name, None)
        obj.empty_display_type = 'PLAIN_AXES'
        obj.empty_display_size = 0.5
        collection.objects.link(obj)

    elif node.type == "cube":
        obj = mesh_gen.create_mi_cube(
            name=display_name, size=1.0,
            rot_point=getattr(node, "rot_point", [0, -8, 0]),
            collection=collection,
            mapped=mapped, uv_offset=uv, uv_repeat=uv_repeat,
            hmirror=texture_mirror[0], vmirror=texture_mirror[1],
            invert=invert,
        )

    elif node.type == "surface":
        obj = mesh_gen.create_mi_surface(
            name=display_name, size=1.0,
            size_3d=(size_3d[0], size_3d[2]),
            rot_point=getattr(node, "rot_point", [0, -8, 0]),
            collection=collection,
            uv_offset=uv, uv_repeat=uv_repeat,
            texture_size=texture_size,
            hmirror=texture_mirror[0], vmirror=texture_mirror[1],
        )

    elif node.type == "block":
        obj = mesh_gen.create_mi_block(
            name=display_name, size_3d=size_3d,
            rot_point=getattr(node, "rot_point", None),
            collection=collection,
            uv=uv, texture_size=texture_size,
            texture_mirror=texture_mirror,
        )

    elif node.type == "camera":
        # Pivot empty — holds MI transform animation
        obj = bpy.data.objects.new(display_name, None)
        obj.empty_display_type = 'PLAIN_AXES'
        collection.objects.link(obj)

        # Lens camera object — parented to the pivot empty
        cam_data = bpy.data.cameras.new(display_name + "_Data")
        cam_obj  = bpy.data.objects.new(display_name + "_Lens", cam_data)
        collection.objects.link(cam_obj)
        cam_obj.parent = obj

        # MI cameras face South (-Y) at zero rotation; Blender cameras face -Z.
        # Rotate the lens by X=+90°, Z=+180° to align with MI default.
        cam_obj.rotation_mode  = 'XYZ'
        cam_obj.rotation_euler = (math.pi / 2, 0, math.pi)

        # Lock lens transforms (it's a fixed proxy)
        for i in range(3):
            cam_obj.lock_location[i] = True
            cam_obj.lock_rotation[i] = True
            cam_obj.lock_scale[i]    = True

    elif node.type in ("pointlight", "spotlight"):
        # Pivot empty — holds MI transform animation
        obj = bpy.data.objects.new(display_name, None)
        obj.empty_display_type = 'PLAIN_AXES'
        collection.objects.link(obj)

        l_type     = 'POINT' if node.type == "pointlight" else 'SPOT'
        light_data = bpy.data.lights.new(name=display_name + "_Data", type=l_type)
        light_obj  = bpy.data.objects.new(display_name + "_Light", light_data)
        collection.objects.link(light_obj)
        light_obj.parent = obj

        # Spotlights face -Y in MI at zero rotation; Blender spotlights face -Z.
        # Same offset as cameras: X=+90°, Z=+180°.
        if l_type == 'SPOT':
            light_obj.rotation_mode  = 'XYZ'
            light_obj.rotation_euler = (math.pi / 2, 0, math.pi)

        for i in range(3):
            light_obj.lock_location[i] = True
            light_obj.lock_rotation[i] = True
            light_obj.lock_scale[i]    = True

    elif node.type == "audio":
        obj = bpy.data.objects.new(display_name, None)
        obj.empty_display_type = 'SPHERE'
        collection.objects.link(obj)

    elif node.type == "text":
        text_data      = bpy.data.curves.new(type="FONT", name=display_name + "_TextData")
        text_data.body = display_name   # placeholder; proper text handled later
        obj = bpy.data.objects.new(display_name, text_data)
        collection.objects.link(obj)

    elif node.type == "path":
        # Placeholder — actual NURBS curve is built later in _build_tree
        # after pathpoint children are collected.  Return a temporary empty
        # that will be replaced by the curve object.
        obj = bpy.data.objects.new(display_name + "_PathPlaceholder", None)
        obj.empty_display_type = 'PLAIN_AXES'
        collection.objects.link(obj)

    elif node.type == "pathpoint":
        # Pathpoints are baked into the parent path curve; they don't get their
        # own Blender objects.  We create a minimal empty as a placeholder so
        # the tree walk remains consistent.
        obj = bpy.data.objects.new(display_name + "_PP", None)
        obj.empty_display_type = 'SINGLE_ARROW'
        obj.empty_display_size = 0.1
        collection.objects.link(obj)

    elif node.type in ("char", "scenery", "item", "bodypart", "particle_spawner"):
        # Placeholder empties — Rig2 / future modules handle these
        obj = bpy.data.objects.new(display_name, None)
        obj.empty_display_type = 'PLAIN_AXES'
        collection.objects.link(obj)

    else:
        # Unsupported type — fallback placeholder
        obj = bpy.data.objects.new(display_name, None)
        obj.empty_display_type = 'CUBE'
        collection.objects.link(obj)

    return obj


def _extract_follow_path_data(node):
    """
    Scan a node's keyframes for PATH_OBJ / PATH_OFFSET keys.

    Returns
    -------
    path_obj_id : str | None   — MI id of the target path node (or None)
    offsets     : list[(frame_num, float)]  — list of (MI frame, offset 0..100)
    """
    path_obj_id = None
    offsets = []

    for frame_num, vals in sorted(node.keyframes.items()):
        po = vals.get("PATH_OBJ")
        if po and po not in ("", "null", None):
            # The first non-null PATH_OBJ encountered is the target
            if path_obj_id is None:
                path_obj_id = po
        poff = vals.get("PATH_OFFSET")
        if poff is not None:
            offsets.append((frame_num, float(poff)))

    return path_obj_id, offsets


def _build_tree(node, parent_obj, collection, start_frame, fps_scale,
                object_map=None, disable_scale_node_ids=None):
    """Recursively create Blender objects from an MINode tree."""

    # ── Path nodes: build the NURBS curve from pathpoint children ────────────
    if node.type == "path":
        return _build_path_node(
            node, parent_obj, collection, start_frame, fps_scale,
            object_map, disable_scale_node_ids,
        )

    # ── Pathpoints are handled inside _build_path_node; skip standalone ───────
    # (In a well-formed MI file a pathpoint always has a "path" parent.)
    if node.type == "pathpoint":
        # Create a minimal placeholder so the tree is consistent, but don't
        # recurse further (pathpoints have no meaningful children).
        obj = _create_blender_object(node, collection)
        if object_map is not None:
            object_map[node.id] = obj
        if parent_obj is not None:
            obj.parent = parent_obj
        clear_transform(obj)
        obj["mi_type"] = "pathpoint"
        return obj

    # ── Standard objects ──────────────────────────────────────────────────────
    obj = _create_blender_object(node, collection)
    if object_map is not None:
        object_map[node.id] = obj

    # ── Parent ────────────────────────────────────────────────────────────────
    if parent_obj is not None:
        obj.parent = parent_obj

    # ── Transform ────────────────────────────────────────────────────────────
    # MI Three-Layer Design:
    #   Layer 1: MI_HARD_DEFAULTS   (engine fallbacks; POS=0, SCA=1, ROT=0)
    #   Layer 2: default_values     (creation placement; merged into every
    #                                keyframe by the parser as the per-frame
    #                                baseline — an empty keyframe {} expands
    #                                to the default_values position)
    #   Layer 3: per-frame delta    (authored per-keyframe overrides)
    #
    # Has keyframes → apply_keyframes writes the full absolute trajectory.
    #   clear_transform first so we start from identity.
    # No keyframes  → default_values IS the static rest transform (layer 2).
    #
    # store_mi_placement saves raw default_values as informational custom props.
    store_mi_placement(obj, node)

    kf_trans = []
    if node.keyframes:
        clear_transform(obj)
        kf_trans = apply_keyframes(obj, node, start_frame, fps_scale)
    else:
        # No keyframes — default_values IS the complete static transform
        apply_default_values_transform(obj, node)
        apply_interpolation_to_obj(obj, kf_trans)

    # ── MI value-track custom props (pivot object) ────────────────────────────
    # For camera and light nodes the pivot obj receives common props; the child
    # (lens / light data obj) receives type-specific props inside the handlers below.
    # For all other types, all props go onto the pivot obj.
    _pivot_custom_prop_types = {
        "folder", "cube", "surface", "block", "audio", "text",
        "char", "scenery", "item", "bodypart", "particle_spawner",
    }
    if node.type in _pivot_custom_prop_types:
        apply_node_custom_props(obj, node, start_frame, fps_scale)
        apply_node_static_props(obj, node)
    elif node.type in ("camera", "pointlight", "spotlight"):
        # Pivot gets common props (ALPHA, VISIBLE, RGB_MUL, etc.)
        apply_node_custom_props(obj, node, start_frame, fps_scale)
        apply_node_static_props(obj, node)

    # ── Child-object handlers (camera lens, light data) ────────────────────────
    # obj.children is not yet populated at this point since we haven't returned;
    # instead we walk bpy.data.objects filtered by parent == obj.
    # A simpler approach: _create_blender_object returns the pivot; the child
    # was parented inside _create_blender_object, so obj.children IS available
    # immediately after _create_blender_object because Blender updates the tree.
    for child_obj in obj.children:
        if node.type in ("pointlight", "spotlight"):
            apply_light_properties(child_obj, node, start_frame, fps_scale)
            if kf_trans:
                apply_interpolation_to_obj(child_obj, kf_trans)
            # apply_light_properties already calls apply_node_custom_props +
            # apply_node_static_props on child_obj; no duplicate call needed.

        elif node.type == "camera":
            apply_camera_properties(child_obj, node, start_frame, fps_scale)
            if kf_trans:
                apply_interpolation_to_obj(child_obj, kf_trans)
            # apply_camera_properties already calls apply_node_custom_props +
            # apply_node_static_props on child_obj; no duplicate call needed.

    # ── Recurse children (char children handled by Rig2) ─────────────────────
    if node.type != "char":
        for child_node in node.children:
            _build_tree(
                child_node, obj, collection, start_frame, fps_scale,
                object_map, disable_scale_node_ids,
            )

    return obj


def _build_path_node(node, parent_obj, collection, start_frame, fps_scale,
                     object_map, disable_scale_node_ids):
    """
    Build a Blender NURBS curve from an MI "path" node and its pathpoint children.

    Steps:
      1. Separate pathpoint children from non-pathpoint children.
      2. Create a NURBS curve from the pathpoints.
      3. Apply common transform keyframes to the curve object.
      4. Recurse for non-pathpoint children.
    """
    display_name = node.display_name

    # ── Separate children ─────────────────────────────────────────────────────
    pathpoint_children = [c for c in node.children if c.type == "pathpoint"]
    other_children     = [c for c in node.children if c.type != "pathpoint"]

    # Sort pathpoints by their tree index (already sorted by parser, but be safe)
    pathpoint_children.sort(key=lambda n: n.parent_tree_index)

    # ── Create the NURBS curve ────────────────────────────────────────────────
    curve_obj = create_path_curve(
        path_node=node,
        pathpoint_nodes=pathpoint_children,
        name=display_name,
        collection=collection,
    )

    if object_map is not None:
        object_map[node.id] = curve_obj

    # Register pathpoint nodes in object_map with the curve obj as proxy
    # (they have no real Blender objects; use curve_obj as stand-in so
    # PATH_OBJ references can resolve to the curve).
    for pp in pathpoint_children:
        if object_map is not None:
            object_map[pp.id] = curve_obj

    # ── Parent ────────────────────────────────────────────────────────────────
    if parent_obj is not None:
        curve_obj.parent = parent_obj

    # ── Transform ────────────────────────────────────────────────────────────
    store_mi_placement(curve_obj, node)

    kf_trans = []
    if node.keyframes:
        clear_transform(curve_obj)
        kf_trans = apply_keyframes(curve_obj, node, start_frame, fps_scale)
        apply_interpolation_to_obj(curve_obj, kf_trans)
    else:
        apply_default_values_transform(curve_obj, node)

    apply_node_static_props(curve_obj, node)
    curve_obj["mi_type"] = "path"

    # ── Recurse non-pathpoint children ────────────────────────────────────────
    for child_node in other_children:
        _build_tree(
            child_node, curve_obj, collection, start_frame, fps_scale,
            object_map, disable_scale_node_ids,
        )

    return curve_obj


def _apply_follow_path_constraints(all_nodes, object_map, start_frame, fps_scale):
    """
    Second pass: for every non-pathpoint MI node whose keyframes contain
    PATH_OBJ + PATH_OFFSET, add a Follow Path constraint to the corresponding
    Blender object pointing at the curve object.

    Must be called AFTER the full tree has been built so that object_map is
    complete.
    """
    for node_id, node in all_nodes.items():
        # Skip path/pathpoint nodes themselves
        if node.type in ("path", "pathpoint"):
            continue

        path_obj_id, offsets = _extract_follow_path_data(node)
        if not path_obj_id or not offsets:
            continue

        # Look up the Blender objects
        obj       = object_map.get(node_id)
        curve_obj = object_map.get(path_obj_id)

        if obj is None or curve_obj is None:
            continue

        # Only apply if the target really is a curve (path nodes produce curves)
        if curve_obj.type != 'CURVE':
            continue

        # Store raw MI path reference for diagnostics
        obj["mi_path_target_id"] = path_obj_id
        try:
            obj.id_properties_ui("mi_path_target_id").update(
                description="MI: ID of the followed path timeline"
            )
        except Exception:
            pass

        apply_follow_path_constraint(
            obj=obj,
            curve_obj=curve_obj,
            path_offsets=offsets,
            start_frame=start_frame,
            fps_scale=fps_scale,
        )


def build_scene(roots, all_nodes, collection, start_frame=1, fps_scale=1.0,
                disable_scale_node_ids=None):
    """
    Top-level entry point: build the entire scene from an MINode tree.

    Parameters
    ----------
    roots                  : list[MINode]   — top-level nodes
    all_nodes              : dict[str, MINode]  — id → MINode flat lookup
    collection             : bpy.types.Collection
    start_frame            : int
    fps_scale              : float
    disable_scale_node_ids : set[str] | None

    Returns
    -------
    object_map : dict[str, bpy.types.Object]  — MI id → Blender object
    """
    object_map = {}

    # ── First pass: build the object tree ────────────────────────────────────
    for root_node in roots:
        _build_tree(
            root_node, None, collection, start_frame, fps_scale,
            object_map, disable_scale_node_ids,
        )

    # ── Second pass: Follow Path constraints ──────────────────────────────────
    _apply_follow_path_constraints(all_nodes, object_map, start_frame, fps_scale)

    return object_map

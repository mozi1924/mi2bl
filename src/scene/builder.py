"""
scene/builder.py — Recursively build the Blender scene from an MINode tree.

Responsibilities:
  1. Create the appropriate Blender object for each MINode type.
  2. Set up parent-child relationships.
  3. Zero the object transform (identity) — MI `default_values` is the creation
     placement, NOT a Blender rest transform.  It is stored as reference custom
     props via `store_mi_placement()` instead.
  4. Apply keyframe animation (transform channels via animator.apply_keyframes).
  5. Apply keyframe-able custom props for ALL MI value tracks (via props module).
  6. Apply static appearance flags as non-animated custom props.
  7. Handle special child-object setup for cameras and lights.
"""

import bpy
import math
from ..scene import mesh_gen
from ..utils.transforms import clear_transform
from ..scene.animator import apply_keyframes, apply_interpolation_to_obj
from ..scene.props import apply_node_custom_props, apply_node_static_props, store_mi_placement
from ..scene.lights import apply_light_properties
from ..scene.cameras import apply_camera_properties


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


def _build_tree(node, parent_obj, collection, start_frame, fps_scale,
                object_map=None, disable_scale_node_ids=None):
    """Recursively create Blender objects from an MINode tree."""

    obj = _create_blender_object(node, collection)
    if object_map is not None:
        object_map[node.id] = obj

    # ── Parent ────────────────────────────────────────────────────────────────
    if parent_obj is not None:
        obj.parent = parent_obj

    # ── Identity transform ───────────────────────────────────────────────────
    # MI `default_values` = object creation placement — NOT a property default.
    # We do NOT apply it as a Blender rest transform.  Instead we zero the
    # transform and store `default_values` as reference custom props.
    clear_transform(obj)
    store_mi_placement(obj, node)

    # ── Transform keyframes ───────────────────────────────────────────────────
    kf_trans = []
    if node.keyframes:
        kf_trans = apply_keyframes(obj, node, start_frame, fps_scale)
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

import bpy
import math
from ..scene import mesh_gen
from ..utils.transform_utils import _apply_default_transform, _apply_light_properties, _apply_keyframes, _apply_interpolation_to_obj

def _create_blender_object(node, collection):
    """
    Create the appropriate Blender object for an MINode.
    Returns the bpy object.
    """
    display_name = node.display_name

    # Try to grab dimensions and texture data if template mapping exists
    size_3d = (16.0, 16.0, 16.0)
    uv = (0.0, 0.0)
    uv_repeat = (1.0, 1.0)
    texture_size = (64.0, 64.0)
    texture_mirror = (False, False) # h, v
    mapped = False
    invert = False
    
    if hasattr(node, "template_data") and node.template_data:
        # Blocks use top-level fields
        t_uv = node.template_data.get("uv", [0.0, 0.0])
        uv = tuple(t_uv) if isinstance(t_uv, list) else (0.0, 0.0)
        
        t_ts = node.template_data.get("texture_size", [64.0, 64.0])
        texture_size = tuple(t_ts) if isinstance(t_ts, list) else (64.0, 64.0)
        
        texture_mirror = (node.template_data.get("tex_hmirror", False), 
                          node.template_data.get("tex_vmirror", False))
        
        t_sz = node.template_data.get("size", [16.0, 16.0, 16.0])
        size_3d = tuple(t_sz) if isinstance(t_sz, list) else (16.0, 16.0, 16.0)

        # Cubes and Surfaces use "shape" object for these
        shape = node.template_data.get("shape", {})
        if shape:
            mapped = shape.get("tex_mapped", False)
            invert = shape.get("invert", False)
            uv = (shape.get("tex_hoffset", 0.0), shape.get("tex_voffset", 0.0))
            uv_repeat = (shape.get("tex_hrepeat", 1.0), shape.get("tex_vrepeat", 1.0))
            texture_mirror = (shape.get("tex_hmirror", False), shape.get("tex_vmirror", False))

    if node.type == "folder":
        obj = bpy.data.objects.new(display_name, None)  # Empty
        obj.empty_display_type = 'PLAIN_AXES'
        obj.empty_display_size = 0.5
        collection.objects.link(obj)

    elif node.type == "cube":
        obj = mesh_gen.create_mi_cube(name=display_name, size=1.0, 
                                      rot_point=getattr(node, "rot_point", [0,-8,0]),
                                      collection=collection,
                                      mapped=mapped, uv_offset=uv, uv_repeat=uv_repeat,
                                      hmirror=texture_mirror[0], vmirror=texture_mirror[1],
                                      invert=invert)

    elif node.type == "surface":
        obj = mesh_gen.create_mi_surface(name=display_name, size=1.0, size_3d=(size_3d[0], size_3d[2]), 
                                         rot_point=getattr(node, "rot_point", [0,-8,0]),
                                         collection=collection, uv_offset=uv, uv_repeat=uv_repeat, 
                                         texture_size=texture_size,
                                         hmirror=texture_mirror[0], vmirror=texture_mirror[1])

    elif node.type == "block":
        obj = mesh_gen.create_mi_block(name=display_name, size_3d=size_3d, 
                                       rot_point=getattr(node, "rot_point", None),
                                       collection=collection, uv=uv, texture_size=texture_size, 
                                       texture_mirror=texture_mirror)
    elif node.type == "camera":
        # Create an Empty to hold the MI transform animation
        obj = bpy.data.objects.new(display_name, None)
        obj.empty_display_type = 'PLAIN_AXES'
        collection.objects.link(obj)
        
        # Create a Blender Camera Object setup properly, parented to the Empty
        cam_data = bpy.data.cameras.new(display_name + "_Data")
        cam_obj = bpy.data.objects.new(display_name + "_Lens", cam_data)
        collection.objects.link(cam_obj)
        
        # Parent the camera to the Empty
        cam_obj.parent = obj
        
        # MI cameras face South (-Y) and are upright (+Z) when rotation is 0.
        # Blender cameras face Down (-Z) with Up (+Y) when rotation is 0.
        # Rotating the camera lens itself by X=+90deg, Z=180deg perfectly aligns it to MI's default.
        cam_obj.rotation_mode = 'XYZ'
        cam_obj.rotation_euler = (math.pi/2, 0, math.pi)
        
        # Lock its transforms since it's just a proxy for the lens
        for i in range(3):
            cam_obj.lock_location[i] = True
            cam_obj.lock_rotation[i] = True
            cam_obj.lock_scale[i] = True
            
        # Attempt to inject properties
        if hasattr(node, "keyframes") and 0 in node.keyframes:
            f0 = node.keyframes[0]
            if "CAM_FOV" in f0:
                cam_data.angle = math.radians(f0["CAM_FOV"])
                
    elif node.type in ("pointlight", "spotlight"):
        obj = bpy.data.objects.new(display_name, None)
        obj.empty_display_type = 'PLAIN_AXES'
        collection.objects.link(obj)
        
        l_type = 'POINT' if node.type == "pointlight" else 'SPOT'
        light_data = bpy.data.lights.new(name=display_name + "_Data", type=l_type)
        light_obj = bpy.data.objects.new(display_name + "_Light", light_data)
        collection.objects.link(light_obj)
        light_obj.parent = obj
        
        # Spotlights, like cameras, point downwards by default and need offset
        if l_type == 'SPOT':
            light_obj.rotation_mode = 'XYZ'
            light_obj.rotation_euler = (math.pi/2, 0, math.pi)
            
        for i in range(3):
            light_obj.lock_location[i] = True
            light_obj.lock_rotation[i] = True
            light_obj.lock_scale[i] = True
                
    elif node.type == "audio":
        obj = bpy.data.objects.new(display_name, None)
        obj.empty_display_type = 'SPHERE'
        collection.objects.link(obj)
        
    elif node.type == "text":
        text_data = bpy.data.curves.new(type="FONT", name=display_name + "_TextData")
        text_data.body = display_name # placeholder text until properly hooked
        obj = bpy.data.objects.new(display_name, text_data)
        collection.objects.link(obj)
    
    elif node.type in ["char", "scenery", "item", "bodypart", "particle_spawner"]:
        # Character Root/Groups / Folders placeholder
        obj = bpy.data.objects.new(display_name, None)
        obj.empty_display_type = 'PLAIN_AXES'
        collection.objects.link(obj)
        
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
        
    if node.type in ("pointlight", "spotlight") and obj.children:
        _apply_light_properties(obj.children[0], node, start_frame, fps_scale)

    # Recurse children (skip children of char nodes — Rig2 handles their motion)
    if node.type != "char":
        for child in node.children:
            _build_tree(child, obj, collection, start_frame, fps_scale, object_map, disable_scale_node_ids)
    return obj

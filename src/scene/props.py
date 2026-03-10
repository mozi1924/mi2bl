from ..utils.color import hex_to_rgb

def coerce_to_float(value):
    """Coerce a value from MI data to float for custom property storage."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0

# --- Shared Properties (Common to all MI nodes) ---
_MI_COMMON_SCALAR_PROPS = {
    "ALPHA": 1.0,
    "GLOW": 0.0,
    "BRIGHTNESS": 0.0,
}

_MI_COMMON_COLOR_PROPS = {
    "RGB_ADD": (0.0, 0.0, 0.0),
    "RGB_MUL": (1.0, 1.0, 1.0),
}

_MI_COMMON_DESCRIPTIONS = {
    "ALPHA": "MI: Opacity (0.0=transparent, 1.0=opaque)",
    "GLOW": "MI: Glow strength",
    "BRIGHTNESS": "MI: Brightness offset",
    "RGB_ADD": "MI: Color add [R, G, B]",
    "RGB_MUL": "MI: Color multiply [R, G, B]",
}

def get_node_props(node_type):
    """
    Return (scalar_defs, color_defs, descriptions) for a node type.
    Includes common properties.
    """
    scalars = dict(_MI_COMMON_SCALAR_PROPS)
    colors = dict(_MI_COMMON_COLOR_PROPS)
    descs = dict(_MI_COMMON_DESCRIPTIONS)

    # Specific registrations
    from . import cameras, lights
    if node_type == "camera":
        s, c, d = cameras.get_camera_props()
        scalars.update(s)
        colors.update(c)
        descs.update(d)
    elif node_type in ("pointlight", "spotlight"):
        s, c, d = lights.get_light_props()
        scalars.update(s)
        colors.update(c)
        descs.update(d)
    
    return scalars, colors, descs

def apply_mi_custom_props(obj, node, start_frame, fps_scale, scalar_defs, color_defs=None, desk_dict=None):
    """Store MI node properties as keyframe-able custom properties."""
    if color_defs is None: color_defs = {}
    if desk_dict is None: desk_dict = {}
    dv = node.default_values

    base_scalar = {k: coerce_to_float(dv.get(k, v)) for k, v in scalar_defs.items()}
    base_color = {k: list(hex_to_rgb(dv.get(k))) if k in dv else list(v) for k, v in color_defs.items()}

    frames = set(node.keyframes.keys()) if node.keyframes else {0}
    frames.add(0)

    props_initialised = False
    for frame_num in sorted(frames):
        time = start_frame + (frame_num * fps_scale)
        cv = node.keyframes.get(frame_num, {})

        for mi_key, value in scalar_defs.items():
            prop_name = "mi_" + mi_key.lower()
            val = coerce_to_float(cv[mi_key]) if mi_key in cv else base_scalar[mi_key]
            obj[prop_name] = val
            if not props_initialised:
                desc = desk_dict.get(mi_key, f"Mine-Imator: {mi_key}")
                try: obj.id_properties_ui(prop_name).update(description=desc)
                except: pass
            obj.keyframe_insert(f'["{prop_name}"]', frame=time)

        for mi_key, default_rgb in color_defs.items():
            prop_name = "mi_" + mi_key.lower()
            rgb = list(hex_to_rgb(cv[mi_key])) if mi_key in cv else base_color[mi_key]
            obj[prop_name] = rgb
            if not props_initialised:
                desc = desk_dict.get(mi_key, f"Mine-Imator: {mi_key}")
                try: obj.id_properties_ui(prop_name).update(description=desc, subtype='COLOR')
                except: pass
            for ch in range(3):
                obj.keyframe_insert(f'["{prop_name}"]', index=ch, frame=time)
        props_initialised = True

def apply_node_custom_props(obj, node, start_frame, fps_scale):
    """Convenience wrapper for builder.py to apply all props (common + unique)."""
    scalars, colors, descs = get_node_props(node.type)
    apply_mi_custom_props(obj, node, start_frame, fps_scale, scalars, colors, descs)

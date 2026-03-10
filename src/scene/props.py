"""
scene/props.py — Unified MI property registry and keyframe-able custom prop writer.

This module is the single source of truth for ALL Mine-Imator value tracks that
need to be stored as Blender custom properties (i.e. anything that isn't directly
mapped to a native Blender property like location/rotation/scale).

Design
------
Each MI value track is described in one of four typed registry dicts:
  - _*_BOOL:  Boolean properties → Blender bool custom property
  - _*_INT:   Integer properties → Blender int custom property
  - _*_FLOAT: Float/percentage properties → Blender float custom property
  - _*_COLOR: Color properties → Blender float[3] with subtype='COLOR'

This ensures Blender's custom property UI shows the correct widget type
(checkbox for bool, integer spinner for int, slider for float, colour picker
for colour), rather than treating everything as a float.

Public API
----------
- `get_node_props(node_type)` → (bool_defs, int_defs, float_defs, color_defs, desc_dict)
- `apply_node_custom_props(obj, node, start_frame, fps_scale)` — writes all props with keyframes
- `apply_node_static_props(obj, node)` — writes static TL flags (non-animated)
- `store_mi_placement(obj, node)` — stores creation-placement as reference props
- `apply_mi_custom_props(...)` — low-level writer (called by apply_node_custom_props)

Note: Color properties (MI stores as int 0xRRGGBB or '#RRGGBB' string) are stored
as float[3] arrays with subtype='COLOR' in Blender.
"""

from ..utils.color import hex_to_rgb


# ─────────────────────────────────────────────────────────────────────────────
# Value coercers
# ─────────────────────────────────────────────────────────────────────────────

def _to_bool(value):
    """Coerce a MI keyframe value to Python bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.lower() not in ("false", "0", "no", "")
    return False


def _to_int(value):
    """Coerce a MI keyframe value to Python int."""
    if isinstance(value, bool):
        return 1 if value else 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _to_float(value):
    """Coerce a MI keyframe value to Python float."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _resolve_color(raw_value, default_rgb):
    """Resolve a MI color value (int, hex string, or None) to an [R,G,B] list."""
    if raw_value is None:
        return list(default_rgb)
    if isinstance(raw_value, (int, float, str)):
        return list(hex_to_rgb(raw_value))
    # Already a list/tuple
    if hasattr(raw_value, '__len__') and len(raw_value) >= 3:
        return [float(raw_value[0]), float(raw_value[1]), float(raw_value[2])]
    return list(default_rgb)


# ─────────────────────────────────────────────────────────────────────────────
# Common properties — present on ALL MI timeline types
# ─────────────────────────────────────────────────────────────────────────────

# Boolean: {MI_KEY: (default_bool, description)}
_COMMON_BOOL = {
    "VISIBLE": (True, "MI: Visibility"),
}

# Integer: {MI_KEY: (default_int, description)}
_COMMON_INT = {}

# Float: {MI_KEY: (default_float, description)}
_COMMON_FLOAT = {
    "ALPHA":                    (1.0,  "MI: Opacity (0.0=transparent, 1.0=opaque)"),
    "EMISSIVE":                 (0.0,  "MI: Emissive brightness (Bloom source strength)"),
    "METALLIC":                 (0.0,  "MI: Metallic/Specular weight (PBR)"),
    "ROUGHNESS":                (1.0,  "MI: Roughness (1=rough, 0=smooth, PBR)"),
    "SUBSURFACE":               (0.0,  "MI: Sub-surface scattering weight"),
    "SUBSURFACE_RADIUS_RED":    (1.0,  "MI: SSS radius (Red channel)"),
    "SUBSURFACE_RADIUS_GREEN":  (1.0,  "MI: SSS radius (Green channel)"),
    "SUBSURFACE_RADIUS_BLUE":   (1.0,  "MI: SSS radius (Blue channel)"),
    "MIX_PERCENT":              (0.0,  "MI: Mix colour percentage (0–1)"),
}

# Color: {MI_KEY: (default_rgb_tuple, description)}
_COMMON_COLOR = {
    "RGB_ADD":          ((0.0, 0.0, 0.0), "MI: Additive colour mix [R,G,B]"),
    "RGB_MUL":          ((1.0, 1.0, 1.0), "MI: Multiplicative colour mix [R,G,B]"),
    "GLOW_COLOR":       ((1.0, 1.0, 1.0), "MI: Emissive glow colour [R,G,B]"),
    "SUBSURFACE_COLOR": ((1.0, 1.0, 1.0), "MI: SSS colour [R,G,B]"),
}


# ─────────────────────────────────────────────────────────────────────────────
# Bend properties — body parts only
# ─────────────────────────────────────────────────────────────────────────────

_BEND_BOOL  = {}
_BEND_INT   = {}
_BEND_FLOAT = {
    "BEND_ANGLE_X": (0.0, "MI: Bend angle X (degrees)"),
    "BEND_ANGLE_Y": (0.0, "MI: Bend angle Y (degrees)"),
    "BEND_ANGLE_Z": (0.0, "MI: Bend angle Z (degrees)"),
}
_BEND_COLOR = {}


# ─────────────────────────────────────────────────────────────────────────────
# Camera properties (extras beyond native Blender camera/DOF props)
# ─────────────────────────────────────────────────────────────────────────────

_CAMERA_BOOL = {
    "CAM_BLOOM":            (False, "MI: Bloom effect enabled"),
    "CAM_VIGNETTE":         (False, "MI: Vignette effect enabled"),
    "CAM_CA":               (False, "MI: Chromatic aberration enabled"),
    "CAM_COLOR_CORRECTION": (False, "MI: Colour correction enabled"),
}

_CAMERA_INT = {
    "CAM_SHAKE_MODE":   (1, "MI: Camera shake mode"),
    "CAM_BLADE_AMOUNT": (0, "MI: Aperture blade count (0=circular)"),
    "CAM_TONEMAPPER":   (0, "MI: Tonemapping algorithm index"),
}

_CAMERA_FLOAT = {
    # Bloom
    "CAM_BLOOM_THRESHOLD":  (0.85, "MI: Bloom threshold"),
    "CAM_BLOOM_INTENSITY":  (0.4,  "MI: Bloom intensity"),
    "CAM_BLOOM_RADIUS":     (1.0,  "MI: Bloom radius"),
    "CAM_BLOOM_RATIO":      (0.0,  "MI: Bloom ratio"),
    # Lens dirt
    "CAM_LENS_DIRT_BLOOM":     (1.0, "MI: Lens dirt (Bloom)"),
    "CAM_LENS_DIRT_GLOW":      (1.0, "MI: Lens dirt (Glow)"),
    "CAM_LENS_DIRT_RADIUS":    (0.5, "MI: Lens dirt radius"),
    "CAM_LENS_DIRT_INTENSITY": (0.8, "MI: Lens dirt intensity"),
    "CAM_LENS_DIRT_POWER":     (1.5, "MI: Lens dirt power"),
    # Colour correction
    "CAM_CONTRAST":    (0.0, "MI: Contrast"),
    "CAM_BRIGHTNESS":  (0.0, "MI: Brightness"),
    "CAM_SATURATION":  (1.0, "MI: Saturation"),
    "CAM_VIBRANCE":    (0.0, "MI: Vibrance"),
    # Grain
    "CAM_GRAIN_STRENGTH":   (0.10, "MI: Grain strength"),
    "CAM_GRAIN_SATURATION": (0.10, "MI: Grain saturation"),
    "CAM_GRAIN_SIZE":       (1.0,  "MI: Grain size"),
    # Vignette
    "CAM_VIGNETTE_RADIUS":   (1.0, "MI: Vignette radius"),
    "CAM_VIGNETTE_SOFTNESS": (0.5, "MI: Vignette softness"),
    "CAM_VIGNETTE_STRENGTH": (1.0, "MI: Vignette strength"),
    # Chromatic aberration
    "CAM_CA_BLUR_AMOUNT":  (0.05, "MI: CA blur amount"),
    "CAM_CA_RED_OFFSET":   (0.12, "MI: CA red offset"),
    "CAM_CA_GREEN_OFFSET": (0.08, "MI: CA green offset"),
    "CAM_CA_BLUE_OFFSET":  (0.04, "MI: CA blue offset"),
    # Distortion
    "CAM_DISTORT_ZOOM_AMOUNT": (1.0,  "MI: Distort zoom amount"),
    "CAM_DISTORT_AMOUNT":      (0.05, "MI: Distort amount"),
    # DOF extras (beyond native Blender DOF)
    "CAM_DOF_RANGE":              (200.0,  "MI: DoF in-focus range"),
    "CAM_DOF_FADE_SIZE":          (100.0,  "MI: DoF fade size"),
    "CAM_DOF_BIAS":               (0.0,    "MI: DoF bias"),
    "CAM_DOF_THRESHOLD":          (0.0,    "MI: DoF threshold"),
    "CAM_DOF_GAIN":               (0.0,    "MI: DoF gain"),
    "CAM_DOF_FRINGE_RED":         (1.0,    "MI: DoF fringe (Red)"),
    "CAM_DOF_FRINGE_GREEN":       (1.0,    "MI: DoF fringe (Green)"),
    "CAM_DOF_FRINGE_BLUE":        (1.0,    "MI: DoF fringe (Blue)"),
    "CAM_DOF_FRINGE_ANGLE_RED":   (90.0,   "MI: DoF fringe angle (Red)"),
    "CAM_DOF_FRINGE_ANGLE_GREEN": (-135.0, "MI: DoF fringe angle (Green)"),
    "CAM_DOF_FRINGE_ANGLE_BLUE":  (-45.0,  "MI: DoF fringe angle (Blue)"),
    # Camera shake
    "CAM_SHAKE_STRENGTH_X": (1.0, "MI: Camera shake strength X"),
    "CAM_SHAKE_STRENGTH_Y": (1.0, "MI: Camera shake strength Y"),
    "CAM_SHAKE_STRENGTH_Z": (1.0, "MI: Camera shake strength Z"),
    "CAM_SHAKE_SPEED_X":    (1.0, "MI: Camera shake speed X"),
    "CAM_SHAKE_SPEED_Y":    (1.0, "MI: Camera shake speed Y"),
    "CAM_SHAKE_SPEED_Z":    (1.0, "MI: Camera shake speed Z"),
    # Resolution
    "CAM_SIZE_KEEP_ASPECT_RATIO": (1.0, "MI: Keep aspect ratio"),
}

_CAMERA_COLOR = {
    "CAM_BLOOM_BLEND":    ((1.0, 1.0, 1.0), "MI: Bloom blend colour [R,G,B]"),
    "CAM_VIGNETTE_COLOR": ((0.0, 0.0, 0.0), "MI: Vignette colour [R,G,B]"),
    "CAM_COLOR_BURN":     ((1.0, 1.0, 1.0), "MI: Colour burn [R,G,B]"),
}


# ─────────────────────────────────────────────────────────────────────────────
# Light properties (extras beyond native Blender energy/color/spot_size)
# ─────────────────────────────────────────────────────────────────────────────

_LIGHT_BOOL  = {}
_LIGHT_INT   = {}
_LIGHT_FLOAT = {
    "LIGHT_FADE_SIZE": (0.5, "MI: Light fade size (attenuation)"),
}
_LIGHT_COLOR = {}


# ─────────────────────────────────────────────────────────────────────────────
# Public API: property registry
# ─────────────────────────────────────────────────────────────────────────────

def get_node_props(node_type):
    """
    Return (bool_defs, int_defs, float_defs, color_defs, descriptions) for a node type.

    bool_defs  : {MI_KEY: default_bool}
    int_defs   : {MI_KEY: default_int}
    float_defs : {MI_KEY: default_float}
    color_defs : {MI_KEY: default_rgb_tuple}
    descriptions: {MI_KEY: description_string}

    All dicts include the common properties first, then type-specific ones.
    """
    bool_defs  = {}
    int_defs   = {}
    float_defs = {}
    color_defs = {}
    descs      = {}

    # ── Common ──────────────────────────────────────────────────────────────
    for k, (d, desc) in _COMMON_BOOL.items():
        bool_defs[k] = d;  descs[k] = desc
    for k, (d, desc) in _COMMON_INT.items():
        int_defs[k] = d;   descs[k] = desc
    for k, (d, desc) in _COMMON_FLOAT.items():
        float_defs[k] = d; descs[k] = desc
    for k, (d, desc) in _COMMON_COLOR.items():
        color_defs[k] = d; descs[k] = desc

    # ── Type-specific ────────────────────────────────────────────────────────
    if node_type == "bodypart":
        for k, (d, desc) in _BEND_BOOL.items():
            bool_defs[k] = d;  descs[k] = desc
        for k, (d, desc) in _BEND_INT.items():
            int_defs[k] = d;   descs[k] = desc
        for k, (d, desc) in _BEND_FLOAT.items():
            float_defs[k] = d; descs[k] = desc
        for k, (d, desc) in _BEND_COLOR.items():
            color_defs[k] = d; descs[k] = desc

    elif node_type == "camera":
        for k, (d, desc) in _CAMERA_BOOL.items():
            bool_defs[k] = d;  descs[k] = desc
        for k, (d, desc) in _CAMERA_INT.items():
            int_defs[k] = d;   descs[k] = desc
        for k, (d, desc) in _CAMERA_FLOAT.items():
            float_defs[k] = d; descs[k] = desc
        for k, (d, desc) in _CAMERA_COLOR.items():
            color_defs[k] = d; descs[k] = desc

    elif node_type in ("pointlight", "spotlight"):
        for k, (d, desc) in _LIGHT_BOOL.items():
            bool_defs[k] = d;  descs[k] = desc
        for k, (d, desc) in _LIGHT_INT.items():
            int_defs[k] = d;   descs[k] = desc
        for k, (d, desc) in _LIGHT_FLOAT.items():
            float_defs[k] = d; descs[k] = desc
        for k, (d, desc) in _LIGHT_COLOR.items():
            color_defs[k] = d; descs[k] = desc

    return bool_defs, int_defs, float_defs, color_defs, descs


# ─────────────────────────────────────────────────────────────────────────────
# Public API: cameras/lights compatibility shims
# ─────────────────────────────────────────────────────────────────────────────

def get_camera_props():
    """Compatibility shim: return camera scalar/color/desc dicts (old API)."""
    scalar_defs, color_defs, descriptions = {}, {}, {}
    for k, (d, desc) in {**_CAMERA_BOOL, **_CAMERA_INT, **_CAMERA_FLOAT}.items():
        scalar_defs[k] = d
        descriptions[k] = desc
    for k, (d, desc) in _CAMERA_COLOR.items():
        color_defs[k] = d
        descriptions[k] = desc
    return scalar_defs, color_defs, descriptions


def get_light_props():
    """Compatibility shim: return light scalar/color/desc dicts (old API)."""
    scalar_defs, color_defs, descriptions = {}, {}, {}
    for k, (d, desc) in {**_LIGHT_BOOL, **_LIGHT_INT, **_LIGHT_FLOAT}.items():
        scalar_defs[k] = d
        descriptions[k] = desc
    for k, (d, desc) in _LIGHT_COLOR.items():
        color_defs[k] = d
        descriptions[k] = desc
    return scalar_defs, color_defs, descriptions


# ─────────────────────────────────────────────────────────────────────────────
# Placement storage (creation position — for reference only)
# ─────────────────────────────────────────────────────────────────────────────

def store_mi_placement(obj, node):
    """
    Store `default_values` (the MI object creation placement) as raw
    non-animated custom properties for reference.

    Prefixed `mi_placement_` to distinguish from animated value-track props
    (prefixed `mi_`).  NOT used for Blender transform — purely informational.

    IMPORTANT: `default_values` is the position where the user placed the
    object in MI when creating it.  It is NOT a "property default" and must
    NOT be merged into keyframe data or used as a Blender rest transform.

    Type mapping:
      bool  → Blender bool custom prop
      float → Blender float custom prop
      int   → Blender int custom prop
      other → string custom prop
    """
    dv = getattr(node, "default_values", {})
    if not dv:
        return
    for k, v in dv.items():
        prop_name = "mi_placement_" + k.lower()
        if isinstance(v, bool):
            obj[prop_name] = bool(v)
        elif isinstance(v, int):
            obj[prop_name] = int(v)
        elif isinstance(v, float):
            obj[prop_name] = float(v)
        else:
            obj[prop_name] = str(v)
        try:
            obj.id_properties_ui(prop_name).update(
                description=f"MI creation placement: {k} (informational only)"
            )
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Core writer: keyframe-able custom properties
# ─────────────────────────────────────────────────────────────────────────────

def _write_prop_with_ui(obj, prop_name, value, desc, subtype=None, initialised=False):
    """Write a custom property value and (on first call) set UI metadata."""
    obj[prop_name] = value
    if not initialised:
        try:
            ui = obj.id_properties_ui(prop_name)
            if subtype:
                ui.update(description=desc, subtype=subtype)
            else:
                ui.update(description=desc)
        except Exception:
            pass


def apply_mi_custom_props(
    obj, node, start_frame, fps_scale,
    bool_defs=None, int_defs=None, float_defs=None,
    color_defs=None, desc_dict=None,
    # Legacy scalar_defs API (treated as float_defs for backward compat)
    scalar_defs=None,
):
    """
    Write MI value tracks as keyframe-able Blender custom properties.

    Iterates over EVERY keyframe in node.keyframes plus frame 0.
    Properties use proper Blender types:
      - bool  → bool custom property (checkbox in UI)
      - int   → int custom property (integer spinner)
      - float → float custom property (slider)
      - color → float[3] with subtype='COLOR' (colour picker)

    IMPORTANT: `node.default_values` is the MI creation placement — it is NOT
    used here.  The baseline values come from the *_defs arguments (hard defaults).

    Parameters
    ----------
    obj         : bpy.types.Object
    node        : MINode  (keyframes pre-merged with MI_HARD_DEFAULTS)
    start_frame : int
    fps_scale   : float
    bool_defs   : {MI_KEY: bool}
    int_defs    : {MI_KEY: int}
    float_defs  : {MI_KEY: float}  (or scalar_defs for legacy callers)
    color_defs  : {MI_KEY: (R,G,B)}
    desc_dict   : {MI_KEY: str}
    scalar_defs : deprecated alias for float_defs
    """
    if bool_defs  is None: bool_defs  = {}
    if int_defs   is None: int_defs   = {}
    if float_defs is None: float_defs = {}
    if color_defs is None: color_defs = {}
    if desc_dict  is None: desc_dict  = {}
    # Legacy backward-compat: scalar_defs treated as additional float_defs
    if scalar_defs:
        float_defs = dict(float_defs)
        float_defs.update(scalar_defs)

    # Collect all frames; always include frame 0
    frames = set(node.keyframes.keys()) if node.keyframes else set()
    frames.add(0)

    props_initialised = False
    for frame_num in sorted(frames):
        time = start_frame + (frame_num * fps_scale)
        # node.keyframes[frame_num] contains MI_HARD_DEFAULTS + per-frame overrides.
        # For the injected frame 0 (no authored keyframe), use {}.
        kf_values = node.keyframes.get(frame_num, {})

        # ── Boolean props ────────────────────────────────────────────────────
        for mi_key, default_val in bool_defs.items():
            prop_name = "mi_" + mi_key.lower()
            raw = kf_values.get(mi_key, default_val)
            val = _to_bool(raw)
            _write_prop_with_ui(obj, prop_name, val,
                                desc_dict.get(mi_key, f"Mine-Imator: {mi_key}"),
                                initialised=props_initialised)
            obj.keyframe_insert(f'["{prop_name}"]', frame=time)

        # ── Integer props ────────────────────────────────────────────────────
        for mi_key, default_val in int_defs.items():
            prop_name = "mi_" + mi_key.lower()
            raw = kf_values.get(mi_key, default_val)
            val = _to_int(raw)
            _write_prop_with_ui(obj, prop_name, val,
                                desc_dict.get(mi_key, f"Mine-Imator: {mi_key}"),
                                initialised=props_initialised)
            obj.keyframe_insert(f'["{prop_name}"]', frame=time)

        # ── Float props ──────────────────────────────────────────────────────
        for mi_key, default_val in float_defs.items():
            prop_name = "mi_" + mi_key.lower()
            raw = kf_values.get(mi_key, default_val)
            val = _to_float(raw)
            _write_prop_with_ui(obj, prop_name, val,
                                desc_dict.get(mi_key, f"Mine-Imator: {mi_key}"),
                                initialised=props_initialised)
            obj.keyframe_insert(f'["{prop_name}"]', frame=time)

        # ── Color props ──────────────────────────────────────────────────────
        for mi_key, default_rgb in color_defs.items():
            prop_name = "mi_" + mi_key.lower()
            raw = kf_values.get(mi_key)
            rgb = _resolve_color(raw, default_rgb)
            _write_prop_with_ui(obj, prop_name, rgb,
                                desc_dict.get(mi_key, f"Mine-Imator: {mi_key}"),
                                subtype='COLOR', initialised=props_initialised)
            for ch in range(3):
                obj.keyframe_insert(f'["{prop_name}"]', index=ch, frame=time)

        props_initialised = True


def apply_node_custom_props(obj, node, start_frame, fps_scale):
    """
    Convenience wrapper called by builder.py.

    Applies ALL custom props (common + type-specific) for the given node type,
    using the correct Blender property type for each MI value track.
    """
    bool_defs, int_defs, float_defs, color_defs, desc_dict = get_node_props(node.type)
    apply_mi_custom_props(
        obj, node, start_frame, fps_scale,
        bool_defs=bool_defs,
        int_defs=int_defs,
        float_defs=float_defs,
        color_defs=color_defs,
        desc_dict=desc_dict,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Static appearance flags writer
# ─────────────────────────────────────────────────────────────────────────────

# (MINode_attr, prop_name, prop_type, description)
# prop_type: 'bool' | 'int' | 'float' | 'str'
_STATIC_APPEARANCE_FLAGS = [
    ("backfaces",        "mi_backfaces",         "bool", "MI: Backface culling disabled"),
    ("shadows",          "mi_shadows",            "bool", "MI: Cast shadows"),
    ("ssao",             "mi_ssao",               "bool", "MI: Screen space ambient occlusion"),
    ("glow",             "mi_glow",               "bool", "MI: Emissive glow (Bloom) enabled"),
    ("glow_texture",     "mi_glow_texture",       "bool", "MI: Glow applies texture shape"),
    ("only_render_glow", "mi_only_render_glow",   "bool", "MI: Only render glow (invisible body)"),
    ("glint_mode",       "mi_glint_mode",         "int",  "MI: Enchantment glint mode (0=None,1=Static,2=Moving)"),
    ("fog",              "mi_fog",                "bool", "MI: Apply scene fog"),
    ("wind",             "mi_wind",               "bool", "MI: Apply wind effect"),
    ("blend_mode",       "mi_blend_mode",         "str",  "MI: Blend mode (normal/add/subtract/multiply/screen)"),
    ("alpha_mode",       "mi_alpha_mode",         "int",  "MI: Alpha mode (0=Default,1=Opaque,2=Blend,3=Hash)"),
]


def apply_node_static_props(obj, node):
    """
    Write static top-level TL appearance flags as non-animated custom properties.

    Uses proper Blender property types:
      bool attrs → Blender bool property (checkbox)
      int attrs  → Blender int property (spinner)
      str attrs  → Blender string property

    These are fixed per-object values (not keyframed) corresponding to the
    checkboxes and dropdowns in the MI timeline sidebar.
    """
    for attr, prop_name, prop_type, desc in _STATIC_APPEARANCE_FLAGS:
        val = getattr(node, attr, None)
        if val is None:
            continue

        if prop_type == "bool":
            obj[prop_name] = bool(val)
        elif prop_type == "int":
            obj[prop_name] = int(val)
        elif prop_type == "float":
            obj[prop_name] = float(val)
        else:  # 'str' or fallback
            obj[prop_name] = str(val)

        try:
            obj.id_properties_ui(prop_name).update(description=desc)
        except Exception:
            pass

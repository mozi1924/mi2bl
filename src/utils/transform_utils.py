import bpy
import math
from mathutils import Euler
from ..utils import core

MI_SCALE = 1.0 / 16.0
apply_mi_transition = core.apply_mi_transition
MIBaseImporter = core.MIBaseImporter

# ─── Node-type → custom-prop registry dispatch ────────────────────────────────
# Each entry maps an MI node type string to a (scalar_defs, color_defs) pair.
# Add new node types here as their MI-specific props are discovered.
# builder.py iterates this dict after building each object tree.
_MI_NODE_CUSTOM_PROP_REGISTRY = {}   # populated after the defs below are declared

def _apply_default_transform(obj, node, disable_scale=False):
    """Apply the default_values from MI as the object's rest transform."""
    dv = node.default_values

    # Position:  MI UI X → BL X,  MI UI Y (Up) → BL Z,  MI UI Z (Depth) → BL -Y
    px = dv.get("POS_X", 0.0) * MI_SCALE
    py = dv.get("POS_Y", 0.0) * MI_SCALE  # UI Y (Up) → BL Z
    pz = dv.get("POS_Z", 0.0) * MI_SCALE  # UI Z (Depth) → BL -Y
    obj.location = (px, -pz, py)

    # Rotation
    rx = math.radians(dv.get("ROT_X", 0.0))
    ry = math.radians(dv.get("ROT_Y", 0.0))  # UI Y (Yaw) → BL Z
    rz = math.radians(dv.get("ROT_Z", 0.0))  # UI Z (Roll) → BL -Y
    obj.rotation_mode = 'XYZ'
    # MI camera zero-rot (Yaw=0) faces south (-Y in BL); Blender camera zero-rot faces -Z.
    obj.rotation_euler = Euler((rx, -rz, ry), 'XYZ')

    # Scale
    sx = dv.get("SCA_X", 1.0)
    sy = dv.get("SCA_Y", 1.0)  # UI Y → BL Z
    sz = dv.get("SCA_Z", 1.0)  # UI Z → BL Y
    obj.scale = (sx, sz, sy)


def _hex_to_rgb(hex_val):
    if isinstance(hex_val, str):
        hex_str = hex_val.lstrip('#')
        if not hex_str:
            return (1.0, 1.0, 1.0)
        try:
            hex_int = int(hex_str, 16)
        except ValueError:
            return (1.0, 1.0, 1.0)
        r = ((hex_int >> 16) & 0xFF) / 255.0
        g = ((hex_int >> 8) & 0xFF) / 255.0
        b = (hex_int & 0xFF) / 255.0
        return (r, g, b)
    elif isinstance(hex_val, (int, float)):
        hex_int = int(hex_val)
        r = ((hex_int >> 16) & 0xFF) / 255.0
        g = ((hex_int >> 8) & 0xFF) / 255.0
        b = (hex_int & 0xFF) / 255.0
        return (r, g, b)
    return (1.0, 1.0, 1.0)

def _apply_light_properties(light_obj, node, start_frame, fps_scale):
    """Apply light properties and keyframes based on Mine-imator logic.
    
    MI 灯光属性默认值（来自 tl_value_default.gml）：
      LIGHT_COLOR         = c_white (#FFFFFF)
      LIGHT_STRENGTH      = 1.0
      LIGHT_SPECULAR_STRENGTH = 1.0
      LIGHT_SIZE          = 2.0   (注意：不是 0！)
      LIGHT_RANGE         = 250.0
      LIGHT_FADE_SIZE     = 0.5
      LIGHT_SPOT_RADIUS   = 50.0  (注意：不是 45！)
      LIGHT_SPOT_SHARPNESS = 0.5
    """
    l_data = light_obj.data
    dv = node.default_values

    # ── 辅助函数 ──────────────────────────────────────────────────────────────

    def get_energy(strength, l_range):
        """
        MI → Blender 能量换算。
        MI 使用非物理衰减，Blender Cycles/Eevee 默认使用平方反比衰减。
        为了让默认灯（strength=1, range=250 MI单位 ≈ 15.6 m）在 Blender 中
        产生肉眼合理的亮度，使用以下公式：
          energy = strength * (range_m ^ 2) * 4π
        这与平方反比衰减在截断距离边界产生约 1 lux 照度时的 Watts 等价。
        系数 *1.0 是可调倍率，根据渲染引擎/场景按需缩放。
        """
        range_m = l_range * MI_SCALE          # MI units → Blender meters
        # 平方律等效：在截断距离的边缘, E = P/(4π r²) ≈ 1 lux
        # → P = 4π r² * strength_factor
        energy = strength * (range_m ** 2) * 4.0 * math.pi
        # 可调缩放系数（1.0 = 保持物理值，可由用户乘以倍率调亮/调暗）
        return max(0.0, energy)

    def get_radius(size_val):
        """MI LIGHT_SIZE（MI单位）→ Blender shadow_soft_size（米）"""
        return max(0.0, size_val * MI_SCALE)

    def get_spot_size(radius_val):
        """
        MI LIGHT_SPOT_RADIUS 是光锥**半角**（度数）。
        Blender spot_size 是光锥**全角**（弧度）。
        → spot_size = radians(radius_val * 2)
        并且 Blender 要求 spot_size ∈ (0, π]。
        """
        return max(0.0001, min(math.pi, math.radians(radius_val * 2.0)))

    def get_spot_blend(sharpness_val):
        """
        MI LIGHT_SPOT_SHARPNESS: 1.0=锐利边缘, 0.0=柔和边缘。
        Blender spot_blend:      0.0=锐利边缘, 1.0=柔和边缘。
        → blend = 1.0 - clamp(sharpness, 0, 1)
        """
        return 1.0 - max(0.0, min(1.0, sharpness_val))

    # ── use_custom_distance 只需设置一次，不可插关键帧 ──────────────────────
    l_data.use_custom_distance = True

    # ── 构建需要处理的帧集合 ──────────────────────────────────────────────────
    # frame_num=0 始终作为"初始静态帧"处理（使用 default_values 作为基础）。
    # 对于没有关键帧的灯光，只插入 frame 0 静态值。
    frames = set(node.keyframes.keys()) if node.keyframes else set()
    frames.add(0)

    # 构建一个"累积值"字典：每帧从 default_values 出发，叠加该帧关键帧数据。
    # 这样即使某一帧只存了 LIGHT_RANGE，其他属性（颜色/强度）也不会丢失。
    base_values = {
        "LIGHT_COLOR":           dv.get("LIGHT_COLOR",           16777215),  # c_white
        "LIGHT_STRENGTH":        dv.get("LIGHT_STRENGTH",        1.0),
        "LIGHT_SPECULAR_STRENGTH": dv.get("LIGHT_SPECULAR_STRENGTH", 1.0),
        "LIGHT_SIZE":            dv.get("LIGHT_SIZE",            2.0),       # GML 默认 2
        "LIGHT_RANGE":           dv.get("LIGHT_RANGE",           250.0),
        "LIGHT_FADE_SIZE":       dv.get("LIGHT_FADE_SIZE",       0.5),
        "LIGHT_SPOT_RADIUS":     dv.get("LIGHT_SPOT_RADIUS",     50.0),      # GML 默认 50
        "LIGHT_SPOT_SHARPNESS":  dv.get("LIGHT_SPOT_SHARPNESS",  0.5),
    }

    for frame_num in sorted(frames):
        time = start_frame + (frame_num * fps_scale)

        # 合并：base_values ← 该帧关键帧覆盖
        if frame_num == 0:
            # frame 0：default_values 已在 base_values 中，再叠加 kf[0]（若存在）
            cv = dict(base_values)
            if node.keyframes and 0 in node.keyframes:
                cv.update(node.keyframes[0])
        else:
            # 其他帧：从 base_values 继承所有字段，然后用该帧数据覆盖
            cv = dict(base_values)
            cv.update(node.keyframes.get(frame_num, {}))

        # ── 1. Energy（功率）──────────────────────────────────────────────────
        s = float(cv.get("LIGHT_STRENGTH", 1.0))
        r = float(cv.get("LIGHT_RANGE",    250.0))
        l_data.energy = get_energy(s, r)
        l_data.keyframe_insert("energy", frame=time)

        # ── 2. Cutoff Distance（物理截断距离）────────────────────────────────
        l_data.cutoff_distance = r * MI_SCALE
        l_data.keyframe_insert("cutoff_distance", frame=time)

        # ── 3. Color（颜色）──────────────────────────────────────────────────
        c_val = cv.get("LIGHT_COLOR", 16777215)
        l_data.color = _hex_to_rgb(c_val)
        l_data.keyframe_insert("color", frame=time)

        # ── 4. Shadow Soft Size（阴影半径）───────────────────────────────────
        sz = float(cv.get("LIGHT_SIZE", 2.0))
        l_data.shadow_soft_size = get_radius(sz)
        l_data.keyframe_insert("shadow_soft_size", frame=time)

        # ── 5. Specular Factor（高光因子）────────────────────────────────────
        spec = float(cv.get("LIGHT_SPECULAR_STRENGTH", 1.0))
        l_data.specular_factor = max(0.0, spec)
        l_data.keyframe_insert("specular_factor", frame=time)

        # ── 6. Spot 专属参数 ──────────────────────────────────────────────────
        if l_data.type == 'SPOT':
            spot_r = float(cv.get("LIGHT_SPOT_RADIUS",    50.0))
            spot_s = float(cv.get("LIGHT_SPOT_SHARPNESS",  0.5))
            l_data.spot_size  = get_spot_size(spot_r)
            l_data.spot_blend = get_spot_blend(spot_s)
            l_data.keyframe_insert("spot_size",  frame=time)
            l_data.keyframe_insert("spot_blend", frame=time)


def _apply_camera_shake(pivot_obj, node):
    """
    Apply camera shake as Noise modifiers on the pivot object's rotation F-curves.
    """
    dv = node.default_values
    if not dv.get("CAM_SHAKE", False):
        return
        
    # Shake strengths (MI uses degrees)
    # Mapping: MI X -> Pivot X, MI Z -> Pivot Y (-rz), MI Y -> Pivot Z (ry)
    sh_strengths = [
        dv.get("CAM_SHAKE_STRENGTH_X", 1.0),
        dv.get("CAM_SHAKE_STRENGTH_Z", 1.0), 
        dv.get("CAM_SHAKE_STRENGTH_Y", 1.0)
    ]
    
    # Shake speeds
    sh_speeds = [
        dv.get("CAM_SHAKE_SPEED_X", 1.0),
        dv.get("CAM_SHAKE_SPEED_Z", 1.0),
        dv.get("CAM_SHAKE_SPEED_Y", 1.0)
    ]

    if not pivot_obj.animation_data or not pivot_obj.animation_data.action:
        # Create a dummy action if none exists so we can add modifiers
        pivot_obj.animation_data_create()
        pivot_obj.animation_data.action = bpy.data.actions.new(name=pivot_obj.name + "_Shake")

    action = pivot_obj.animation_data.action
    for i in range(3):
        strength = sh_strengths[i]
        if strength <= 0: continue
        
        # Ensure fcurve exists
        fcurve = action.fcurves.find("rotation_euler", index=i)
        if not fcurve:
            fcurve = action.fcurves.new("rotation_euler", index=i)
            # Find existing rotation or use current
            fcurve.keyframe_points.insert(0, pivot_obj.rotation_euler[i])

        # Add Noise modifier
        mod = fcurve.modifiers.new('NOISE')
        mod.amplitude = math.radians(strength)
        
        # Scaling Frequency: Higher MI speed = smaller Blender scale.
        # Default speed 1.0 -> Scale 5.0 seems reasonable.
        speed = max(0.01, sh_speeds[i])
        mod.scale = 5.0 / speed 
        
        # Phase offset to avoid synchronized shaking
        mod.phase = i * 100.0


# ─── Unified MI Custom Properties System ──────────────────────────────────────
#
# Any MI node type can declare a "prop registry" — a pair of dicts:
#   scalar_defs : {mi_key: default_float}
#   color_defs  : {mi_key: (r, g, b)}
#
# These are stored as Blender custom properties on the target object with the
# naming convention  "mi_<mi_key_lowercase>"  so they remain unique, searchable,
# and driver-addressable across the whole file.
#
# All numeric props are ``float`` (keyframe-able).
# Color props are ``list[float]`` [R, G, B] (IDPropertyArray, index-addressable).
#
# A shared description registry maps any mi_key → tooltip string.
# ──────────────────────────────────────────────────────────────────────────────

# ── Per-type scalar prop registries ───────────────────────────────────────────

# Camera: MI-only properties that have no direct Blender equivalent
_MI_CAMERA_SCALAR_PROPS = {
    # Effect toggles (MI-only on/off switches)
    "CAM_BLOOM":                   0.0,   # bool → float
    "CAM_VIGNETTE":                0.0,
    "CAM_CA":                      0.0,
    # DoF (MI-specific)
    "CAM_DOF_RANGE":               200.0,
    "CAM_DOF_FADE_SIZE":           100.0,
    "CAM_DOF_BIAS":                0.0,
    "CAM_DOF_THRESHOLD":           0.0,
    "CAM_DOF_GAIN":                0.0,
    # Chromatic Aberration / Fringe
    "CAM_DOF_FRINGE_RED":          1.0,
    "CAM_DOF_FRINGE_GREEN":        1.0,
    "CAM_DOF_FRINGE_BLUE":         1.0,
    "CAM_DOF_FRINGE_ANGLE_RED":    90.0,
    "CAM_DOF_FRINGE_ANGLE_GREEN": -135.0,
    "CAM_DOF_FRINGE_ANGLE_BLUE":  -45.0,
    # Bloom
    "CAM_BLOOM_THRESHOLD":         0.85,
    "CAM_BLOOM_INTENSITY":         0.4,
    "CAM_BLOOM_RADIUS":            1.0,
    "CAM_BLOOM_RATIO":             0.0,
    # Lens Dirt
    "CAM_LENS_DIRT_BLOOM":         1.0,   # bool → float
    "CAM_LENS_DIRT_GLOW":          1.0,
    "CAM_LENS_DIRT_RADIUS":        0.5,
    "CAM_LENS_DIRT_INTENSITY":     0.8,
    "CAM_LENS_DIRT_POWER":         1.5,
    # Color Correction
    "CAM_COLOR_CORRECTION":        0.0,   # bool → float
    "CAM_CONTRAST":                0.0,
    "CAM_BRIGHTNESS":              0.0,
    "CAM_SATURATION":              1.0,
    "CAM_VIBRANCE":                0.0,
    # Film Grain
    "CAM_GRAIN_STRENGTH":          0.10,
    "CAM_GRAIN_SATURATION":        0.10,
    "CAM_GRAIN_SIZE":              1.0,
    # Vignette
    "CAM_VIGNETTE_RADIUS":         1.0,
    "CAM_VIGNETTE_SOFTNESS":       0.5,
    "CAM_VIGNETTE_STRENGTH":       1.0,
    # Chromatic Aberration (CA)
    "CAM_CA_BLUR_AMOUNT":          0.05,
    "CAM_CA_RED_OFFSET":           0.12,
    "CAM_CA_GREEN_OFFSET":         0.08,
    "CAM_CA_BLUE_OFFSET":          0.04,
    # Distortion
    "CAM_DISTORT_ZOOM_AMOUNT":     1.0,
    "CAM_DISTORT_AMOUNT":          0.05,
    # Misc
    "CAM_SIZE_KEEP_ASPECT_RATIO":  1.0,   # bool → float
    "CAM_SHAKE_MODE":              1.0,
}

# Camera: color properties (stored as [R, G, B] float arrays)
_MI_CAMERA_COLOR_PROPS = {
    "CAM_BLOOM_BLEND":    (1.0, 1.0, 1.0),  # c_white → [1,1,1]
    "CAM_VIGNETTE_COLOR": (0.0, 0.0, 0.0),  # c_black → [0,0,0]
    "CAM_COLOR_BURN":     (1.0, 1.0, 1.0),  # c_white → [1,1,1]
}

# ── Shared description registry (all node types) ───────────────────────────────
# New entries for other node types should be added here, not in separate dicts.
_MI_PROP_DESCRIPTIONS = {
    # Camera props
    "CAM_BLOOM":                   "MI: Bloom effect enabled (0.0=off, 1.0=on)",
    "CAM_VIGNETTE":                "MI: Vignette effect enabled (0.0=off, 1.0=on)",
    "CAM_CA":                      "MI: Chromatic aberration effect enabled (0.0=off, 1.0=on)",
    "CAM_DOF_RANGE":               "MI: DoF in-focus range (MI units)",
    "CAM_DOF_FADE_SIZE":           "MI: DoF fade/transition size (MI units)",
    "CAM_DOF_BIAS":                "MI: DoF bias offset",
    "CAM_DOF_THRESHOLD":           "MI: DoF blur threshold",
    "CAM_DOF_GAIN":                "MI: DoF blur gain",
    "CAM_DOF_FRINGE_RED":          "MI: Fringe (CA) strength – Red channel",
    "CAM_DOF_FRINGE_GREEN":        "MI: Fringe (CA) strength – Green channel",
    "CAM_DOF_FRINGE_BLUE":         "MI: Fringe (CA) strength – Blue channel",
    "CAM_DOF_FRINGE_ANGLE_RED":    "MI: Fringe angle (deg) – Red channel",
    "CAM_DOF_FRINGE_ANGLE_GREEN":  "MI: Fringe angle (deg) – Green channel",
    "CAM_DOF_FRINGE_ANGLE_BLUE":   "MI: Fringe angle (deg) – Blue channel",
    "CAM_BLOOM_THRESHOLD":         "MI: Bloom luminance threshold",
    "CAM_BLOOM_INTENSITY":         "MI: Bloom intensity multiplier",
    "CAM_BLOOM_RADIUS":            "MI: Bloom blur radius",
    "CAM_BLOOM_RATIO":             "MI: Bloom aspect ratio",
    "CAM_BLOOM_BLEND":             "MI: Bloom blend color [R, G, B]",
    "CAM_LENS_DIRT_BLOOM":         "MI: Lens dirt applied to bloom (0.0=off, 1.0=on)",
    "CAM_LENS_DIRT_GLOW":          "MI: Lens dirt applied to glow (0.0=off, 1.0=on)",
    "CAM_LENS_DIRT_RADIUS":        "MI: Lens dirt radius",
    "CAM_LENS_DIRT_INTENSITY":     "MI: Lens dirt intensity",
    "CAM_LENS_DIRT_POWER":         "MI: Lens dirt power exponent",
    "CAM_COLOR_CORRECTION":        "MI: Color correction enabled (0.0=off, 1.0=on)",
    "CAM_CONTRAST":                "MI: Color correction – Contrast",
    "CAM_BRIGHTNESS":              "MI: Color correction – Brightness",
    "CAM_SATURATION":              "MI: Color correction – Saturation",
    "CAM_VIBRANCE":                "MI: Color correction – Vibrance",
    "CAM_COLOR_BURN":              "MI: Color correction – Burn color [R, G, B]",
    "CAM_GRAIN_STRENGTH":          "MI: Film grain strength",
    "CAM_GRAIN_SATURATION":        "MI: Film grain saturation",
    "CAM_GRAIN_SIZE":              "MI: Film grain size",
    "CAM_VIGNETTE_RADIUS":         "MI: Vignette radius",
    "CAM_VIGNETTE_SOFTNESS":       "MI: Vignette softness",
    "CAM_VIGNETTE_STRENGTH":       "MI: Vignette strength",
    "CAM_VIGNETTE_COLOR":          "MI: Vignette color [R, G, B]",
    "CAM_CA_BLUR_AMOUNT":          "MI: Chromatic aberration blur amount",
    "CAM_CA_RED_OFFSET":           "MI: Chromatic aberration – Red channel offset",
    "CAM_CA_GREEN_OFFSET":         "MI: Chromatic aberration – Green channel offset",
    "CAM_CA_BLUE_OFFSET":          "MI: Chromatic aberration – Blue channel offset",
    "CAM_DISTORT_ZOOM_AMOUNT":     "MI: Lens distortion zoom amount",
    "CAM_DISTORT_AMOUNT":          "MI: Lens distortion amount",
    "CAM_SIZE_KEEP_ASPECT_RATIO":  "MI: Keep aspect ratio (0.0=off, 1.0=on)",
    "CAM_SHAKE_MODE":              "MI: Camera shake mode (0.0=off, 1.0=on)",
    # ── Future node type props can be added here: ──────────────────────────
    # e.g. "CUBE_OPACITY": "MI: Cube opacity (0.0=transparent, 1.0=opaque)",
}


def _coerce_to_float(value):
    """Coerce a value from MI data to float for custom property storage.
    bool → 0.0/1.0, int/float → float, other → 0.0.
    """
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _apply_mi_custom_props(obj, node, start_frame, fps_scale,
                           scalar_defs, color_defs=None):
    """Store MI node properties as keyframe-able custom properties on any
    Blender object.  This is the **unified** entry-point for all node types —
    cameras, lights, cubes, folders, etc. — so that creators can access and
    drive MI-specific data from within Blender.

    Custom property naming convention:  ``mi_<mi_key_lowercase>``
    e.g.  ``CAM_BLOOM``  →  ``mi_cam_bloom``

    Scalar properties are stored as ``float`` (keyframe-able).
    Color properties are stored as ``list[float]`` ``[R, G, B]``
    (Blender IDPropertyArray; index-addressable in drivers).

    Parameters
    ----------
    obj : bpy.types.Object
        Target Blender object to receive the custom properties.
    node : MINode
        The parsed MI timeline node carrying ``default_values`` and
        ``keyframes``.
    start_frame : float
        Blender frame number that corresponds to MI frame 0.
    fps_scale : float
        MI-to-Blender frame-rate ratio (blender_fps / mi_tempo).
    scalar_defs : dict[str, float]
        Mapping of MI property keys → their MI default float values.
        All entries will be written as ``float`` custom properties.
    color_defs : dict[str, tuple[float, float, float]] | None
        Mapping of MI property keys → default (R, G, B) tuples.
        These are hex-encoded in MI data and written as [R, G, B] arrays.
        Pass ``None`` or ``{}`` when the node type has no color props.
    """
    if color_defs is None:
        color_defs = {}

    dv = node.default_values

    # ── 1. Build base-value dicts from default_values (MI file) ───────────
    base_scalar = {
        mi_key: _coerce_to_float(dv.get(mi_key, default_val))
        for mi_key, default_val in scalar_defs.items()
    }

    base_color = {}
    for mi_key, default_rgb in color_defs.items():
        raw = dv.get(mi_key, None)
        base_color[mi_key] = list(_hex_to_rgb(raw)) if raw is not None \
            else list(default_rgb)

    # ── 2. Determine the set of frames to process ─────────────────────────
    frames = set(node.keyframes.keys()) if node.keyframes else set()
    frames.add(0)

    props_initialised = False  # Create Blender UI metadata only on first frame

    for frame_num in sorted(frames):
        time = start_frame + (frame_num * fps_scale)

        # Current-frame override values (keyframe data for this frame)
        cv = dict(node.keyframes.get(frame_num, {})) if node.keyframes else {}

        # ── 2a. Scalar properties ──────────────────────────────────────────
        for mi_key, default_val in scalar_defs.items():
            prop_name = "mi_" + mi_key.lower()
            value = _coerce_to_float(cv[mi_key]) if mi_key in cv \
                else base_scalar[mi_key]

            obj[prop_name] = value

            if not props_initialised:
                desc = _MI_PROP_DESCRIPTIONS.get(mi_key, f"Mine-Imator: {mi_key}")
                try:
                    obj.id_properties_ui(prop_name).update(description=desc)
                except Exception:
                    pass

            obj.keyframe_insert(f'["{prop_name}"]', frame=time)

        # ── 2b. Color properties (float array [R,G,B]) ────────────────────
        for mi_key, default_rgb in color_defs.items():
            prop_name = "mi_" + mi_key.lower()
            rgb = list(_hex_to_rgb(cv[mi_key])) if mi_key in cv \
                else list(base_color[mi_key])

            obj[prop_name] = rgb

            if not props_initialised:
                desc = _MI_PROP_DESCRIPTIONS.get(mi_key, f"Mine-Imator: {mi_key}")
                try:
                    obj.id_properties_ui(prop_name).update(
                        description=desc, subtype='COLOR'
                    )
                except Exception:
                    pass

            # Keyframe each RGB channel independently
            for ch in range(3):
                obj.keyframe_insert(f'["{prop_name}"]', index=ch, frame=time)

        props_initialised = True


# ── Convenience alias kept for backwards-compatibility ────────────────────────
# Callers that previously imported _apply_camera_custom_props directly will
# still work; they simply receive _apply_mi_custom_props bound to the camera
# scalar/color defs.
def _apply_camera_custom_props(cam_lens_obj, node, start_frame, fps_scale):
    """Backwards-compatible wrapper — delegates to _apply_mi_custom_props."""
    _apply_mi_custom_props(
        cam_lens_obj, node, start_frame, fps_scale,
        scalar_defs=_MI_CAMERA_SCALAR_PROPS,
        color_defs=_MI_CAMERA_COLOR_PROPS,
    )


# ── Populate the node-type dispatch registry ──────────────────────────────────
# "camera" maps to the _Lens child object; the registry is consulted in
# builder._build_tree for the *child* object (cam_obj / light_obj) so the
# correct target object is already selected there.
# For future node types add entries here:
#   _MI_NODE_CUSTOM_PROP_REGISTRY["cube"]    = (_MI_CUBE_SCALAR_PROPS,   {})
#   _MI_NODE_CUSTOM_PROP_REGISTRY["surface"] = (_MI_SURFACE_SCALAR_PROPS, {})
#   etc.
_MI_NODE_CUSTOM_PROP_REGISTRY.update({
    "camera": (_MI_CAMERA_SCALAR_PROPS, _MI_CAMERA_COLOR_PROPS),
    # pointlight / spotlight: the MI light scalar props that do not map
    # to any native Blender light data-path are stored here so that they
    # remain accessible and drivable by animators.
    # (Currently empty — all MI light props are applied as native Blender
    #  properties.  Future MI-only light props can be added below.)
    "pointlight": ({}, {}),
    "spotlight":  ({}, {}),
    # Scene-graph containers / geometry nodes – no MI-only props yet.
    "folder":   ({}, {}),
    "cube":     ({}, {}),
    "surface":  ({}, {}),
    "block":    ({}, {}),
    "audio":    ({}, {}),
    "text":     ({}, {}),
    "char":     ({}, {}),
})


def _apply_camera_properties(cam_lens_obj, node, start_frame, fps_scale):
    """
    Apply camera-specific lens properties and keyframes.
    cam_lens_obj: The actual Blender Camera object (the "Lens" object).
    node: The MINode for the camera.
    """
    cam_data = cam_lens_obj.data
    dv = node.default_values
    
    # 1. Set sensor fit to Vertical to match MI's FOV behavior
    cam_data.sensor_fit = 'VERTICAL'

    # 2. Keyframe Processing
    frames = set(node.keyframes.keys()) if node.keyframes else set()
    frames.add(0)

    for frame_num in sorted(frames):
        time = start_frame + (frame_num * fps_scale)
        if frame_num == 0:
            # Use default_values, then override with keyframe 0 if it exists
            current_values = dict(dv)
            if node.keyframes and 0 in node.keyframes:
                current_values.update(node.keyframes[0])
        else:
            current_values = node.keyframes.get(frame_num, {})

        # --- Lens Properties ---
        
        # FOV (Mine-imator uses vertical FOV)
        if "CAM_FOV" in current_values or frame_num == 0:
            fov = current_values.get("CAM_FOV", 45.0)
            cam_data.angle = math.radians(fov)
            cam_data.keyframe_insert("lens", frame=time)

        # Depth of Field Toggle
        if "CAM_DOF" in current_values or frame_num == 0:
            cam_data.dof.use_dof = bool(current_values.get("CAM_DOF", False))
            cam_data.dof.keyframe_insert("use_dof", frame=time)

        # Focus Distance
        if any(k in current_values for k in ["CAM_DOF_DEPTH", "CAM_ROTATE_DISTANCE"]) or frame_num == 0:
            # If CAM_DOF_DEPTH is 0 or missing, it often follows rotate distance
            depth = current_values.get("CAM_DOF_DEPTH", 0.0)
            if depth <= 0:
                depth = current_values.get("CAM_ROTATE_DISTANCE", 100.0)
            cam_data.dof.focus_distance = depth * MI_SCALE
            cam_data.dof.keyframe_insert("focus_distance", frame=time)

        # Blur Size -> F-stop mapping (Heuristic)
        # MI blur_size (0.0 to 1.0+). Lower f-stop = more blur.
        if "CAM_DOF_BLUR_SIZE" in current_values or frame_num == 0:
            blur = current_values.get("CAM_DOF_BLUR_SIZE", 0.01)
            fstop = 128.0
            if blur > 0:
                # Heuristic: 0.01 -> ~20 f-stop, 0.1 -> ~2.0 f-stop, 1.0 -> ~0.2 f-stop
                fstop = 1.0 / (blur * 5.0)
            cam_data.dof.aperture_fstop = max(0.1, min(128.0, fstop))
            cam_data.dof.keyframe_insert("aperture_fstop", frame=time)

        # Aperture Blades & Rotation
        if "CAM_BLADE_AMOUNT" in current_values or frame_num == 0:
            cam_data.dof.aperture_blades = int(current_values.get("CAM_BLADE_AMOUNT", 0))
            cam_data.dof.keyframe_insert("aperture_blades", frame=time)
        
        if "CAM_BLADE_ANGLE" in current_values or frame_num == 0:
            rot = math.radians(current_values.get("CAM_BLADE_ANGLE", 0.0))
            cam_data.dof.aperture_rotation = rot
            cam_data.dof.keyframe_insert("aperture_rotation", frame=time)
            
        if "CAM_DOF_BLUR_RATIO" in current_values or frame_num == 0:
            ratio = current_values.get("CAM_DOF_BLUR_RATIO", 1.0)
            cam_data.dof.aperture_ratio = ratio
            cam_data.dof.keyframe_insert("aperture_ratio", frame=time)

        # --- Orbit / Rotate Distance ---
        # This affects the location of the lens object relative to its parent (the pivot)
        if "CAM_ROTATE_DISTANCE" in current_values or frame_num == 0:
            dist = current_values.get("CAM_ROTATE_DISTANCE", 0.0)
            # Blender camera looks at -Z. So to be "away" from pivot, it moves in local +Z.
            cam_lens_obj.location[2] = dist * MI_SCALE
            cam_lens_obj.keyframe_insert("location", index=2, frame=time)

    # --- Post-Keyframe Pass (e.g. Shake) ---
    if cam_lens_obj.parent:
        _apply_camera_shake(cam_lens_obj.parent, node)

    # --- Scene-wide Settings (Apply based on frame 0/default) ---
    f0_values = dict(dv)
    if node.keyframes and 0 in node.keyframes:
        f0_values.update(node.keyframes[0])
    
    # Resolution
    if not f0_values.get("CAM_SIZE_USE_PROJECT", True):
        bpy.context.scene.render.resolution_x = int(f0_values.get("CAM_WIDTH", 1280))
        bpy.context.scene.render.resolution_y = int(f0_values.get("CAM_HEIGHT", 720))

    # Exposure
    if "CAM_EXPOSURE" in f0_values:
        val = f0_values.get("CAM_EXPOSURE", 1.0)
        bpy.context.scene.view_settings.exposure = math.log2(max(0.01, val))

    # Gamma (MI 2.2 is Neutral/Standard. Blender 1.0 is standard for AgX/Filmic)
    if "CAM_GAMMA" in f0_values:
        gamma = f0_values.get("CAM_GAMMA", 2.2)
        bpy.context.scene.view_settings.gamma = gamma / 2.2

    # --- Store MI-only properties as keyframe-able custom properties ---
    _apply_camera_custom_props(cam_lens_obj, node, start_frame, fps_scale)




def _apply_keyframes(obj, node, start_frame, fps_scale, disable_scale=False):
    """
    Apply keyframe animation data from the MINode onto the Blender object.
    Returns a list of (time, transition_info) tuples for interpolation pass.
    """
    kf_trans_list = []

    for frame_num in sorted(node.keyframes.keys()):
        values = node.keyframes[frame_num]
        time = start_frame + (frame_num * fps_scale)

        # Transition info for later interpolation
        trans_type = values.get("TRANSITION", "linear")
        t_info = {
            "type": trans_type,
            "ease_in": (values.get("EASE_IN_X", 1.0),
                        values.get("EASE_IN_Y", 0.0)),
            "ease_out": (values.get("EASE_OUT_X", 0.0),
                         values.get("EASE_OUT_Y", 1.0))
        }
        kf_trans_list.append((time, t_info))

        # --- Position ---
        has_pos = False
        loc = list(obj.location)
        if "POS_X" in values:
            loc[0] = values["POS_X"] * MI_SCALE
            has_pos = True
        if "POS_Z" in values:
            loc[1] = -values["POS_Z"] * MI_SCALE   # UI Z (Depth) → BL -Y
            has_pos = True
        if "POS_Y" in values:
            loc[2] = values["POS_Y"] * MI_SCALE     # UI Y (Up) → BL Z
            has_pos = True
        if has_pos:
            obj.location = tuple(loc)
            obj.keyframe_insert("location", frame=time)

        # --- Rotation ---
        has_rot = False
        rot = list(obj.rotation_euler) if obj.rotation_mode == 'XYZ' \
            else [0.0, 0.0, 0.0]
        if "ROT_X" in values:
            rot[0] = math.radians(values["ROT_X"])
            has_rot = True
        if "ROT_Z" in values:
            rot[1] = math.radians(-values["ROT_Z"])  # UI Z (Roll) → BL -Y
            has_rot = True
        if "ROT_Y" in values:
            rot[2] = math.radians(values["ROT_Y"])    # UI Y (Yaw) → BL Z
            has_rot = True
        if has_rot:
            obj.rotation_mode = 'XYZ'
            obj.rotation_euler = Euler(tuple(rot), 'XYZ')
            obj.keyframe_insert("rotation_euler", frame=time)

        # --- Scale ---
        has_scl = False
        scl = list(obj.scale)
        if "SCA_X" in values:
            scl[0] = values["SCA_X"]
            has_scl = True
        if "SCA_Z" in values:
            scl[1] = values["SCA_Z"]   # UI Z (Depth) → BL Y
            has_scl = True
        if "SCA_Y" in values:
            scl[2] = values["SCA_Y"]   # UI Y (Up) → BL Z
            has_scl = True
        if has_scl:
            obj.scale = tuple(scl)
            obj.keyframe_insert("scale", frame=time)

    return kf_trans_list


def _apply_interpolation_to_obj(obj, kf_trans_list):
    """Apply MI easing interpolation to the object's fcurves."""
    if not obj.animation_data or not obj.animation_data.action:
        return
    action = obj.animation_data.action
    for fcurve in action.fcurves:
        # Include camera and light properties for interpolation
        # cutoff_distance、color、shadow_soft_size、specular_factor 均为灯光可动画属性
        dp = fcurve.data_path
        is_mi_custom = dp.startswith('["mi_')  # MI custom properties
        if is_mi_custom or dp in (
            # 变换
            "location", "rotation_euler", "scale",
            # 摄像机镜头
            "lens",
            "dof.focus_distance", "dof.aperture_fstop",
            "dof.aperture_rotation", "dof.aperture_ratio",
            # 灯光通用
            "energy", "color", "shadow_soft_size", "specular_factor",
            "cutoff_distance",
            # 聚光灯专属
            "spot_size", "spot_blend",
        ):
            # Walk keyframe pairs
            for i in range(1, len(fcurve.keyframe_points)):
                kf0 = fcurve.keyframe_points[i - 1]
                kf1 = fcurve.keyframe_points[i]
                target_time = kf0.co.x

                best_t_info = None
                min_dist = 0.05
                for t, info in kf_trans_list:
                    dist = abs(t - target_time)
                    if dist < min_dist:
                        min_dist = dist
                        best_t_info = info

                if not best_t_info:
                    continue

                t_type = best_t_info["type"]
                if t_type == "instant":
                    kf0.interpolation = 'CONSTANT'
                elif t_type == "linear":
                    kf0.interpolation = 'LINEAR'
                elif t_type == "bezier":
                    MIBaseImporter.apply_bezier_handles(kf0, kf1, best_t_info)
                else:
                    apply_mi_transition(kf0, t_type, kf1)
            fcurve.update()

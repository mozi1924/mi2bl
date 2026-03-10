"""
scene/cameras.py — MI Camera native-property mapping and keyframing.

The camera-specific MI properties that CAN be mapped directly to native
Blender camera/DOF properties are handled here (FOV, DOF depth/blur, etc.).

Camera-specific properties that have no direct Blender equivalent are stored
as keyframe-able custom properties via props.apply_node_custom_props(), which
is called by builder.py after this function returns.

Static top-level flags (shake, etc.) are applied from node attributes set
in the parser.

This module no longer owns the _MI_CAMERA_SCALAR_PROPS / _MI_CAMERA_COLOR_PROPS
dicts — those now live in props.py which is the single source of truth.
"""

import bpy
import math
from ..constants import MI_SCALE
from .props import apply_node_custom_props, apply_node_static_props


def apply_camera_shake(pivot_obj, node):
    """Apply camera shake as Noise modifiers on the pivot object's rotation F-curves.

    Camera shake strength/speed come from keyframe data (already merged with
    MI_HARD_DEFAULTS by the parser).  We use the first keyframe or frame 0.
    `node.default_values` is the creation placement and is NOT used here.
    """
    # Get shake params from first keyframe (or hard defaults)
    first_kf = node.keyframes.get(0, {}) if node.keyframes else {}
    if not first_kf and node.keyframes:
        first_kf = next(iter(node.keyframes.values()))
    if not first_kf.get("CAM_SHAKE", False):
        return

    sh_strengths = [first_kf.get(f"CAM_SHAKE_STRENGTH_{c}", 1.0) for c in "XZY"]
    sh_speeds    = [first_kf.get(f"CAM_SHAKE_SPEED_{c}",    1.0) for c in "XZY"]

    if not pivot_obj.animation_data or not pivot_obj.animation_data.action:
        pivot_obj.animation_data_create()
        pivot_obj.animation_data.action = bpy.data.actions.new(
            name=pivot_obj.name + "_Shake"
        )

    action = pivot_obj.animation_data.action
    for i in range(3):
        strength = sh_strengths[i]
        if strength <= 0:
            continue
        fcurve = (
            action.fcurves.find("rotation_euler", index=i)
            or action.fcurves.new("rotation_euler", index=i)
        )
        if not fcurve.keyframe_points:
            fcurve.keyframe_points.insert(0, pivot_obj.rotation_euler[i])

        mod = fcurve.modifiers.new('NOISE')
        mod.amplitude = math.radians(strength)
        mod.scale     = 5.0 / max(0.01, sh_speeds[i])
        mod.phase     = i * 100.0


def apply_camera_properties(cam_lens_obj, node, start_frame, fps_scale):
    """
    Apply camera-specific native lens properties and keyframes.

    Properties written to native Blender camera data:
      - lens (angle)         ← CAM_FOV
      - dof.use_dof          ← CAM_DOF (bool)
      - dof.focus_distance   ← CAM_DOF_DEPTH / CAM_ROTATE_DISTANCE
      - dof.aperture_fstop   ← CAM_DOF_BLUR_SIZE
      - dof.aperture_blades  ← CAM_BLADE_AMOUNT
      - dof.aperture_ratio   ← CAM_DOF_BLUR_RATIO
      - dof.aperture_rotation← CAM_BLADE_ANGLE
      - (CAM_ROTATE_DISTANCE stored as custom prop only, not applied as Z offset)

    Everything else is handled by apply_node_custom_props (called by builder).
    """
    cam_data = cam_lens_obj.data
    cam_data.sensor_fit = 'VERTICAL'

    # Camera hard defaults (from tl_value_default.gml)
    _CAM_HARD_DEFAULTS = {
        "CAM_FOV": 45.0,
        "CAM_DOF": False,
        "CAM_DOF_DEPTH": 0.0,
        "CAM_ROTATE_DISTANCE": 100.0,
        "CAM_DOF_BLUR_SIZE": 0.01,
        "CAM_BLADE_AMOUNT": 0,
        "CAM_DOF_BLUR_RATIO": 1.0,
        "CAM_BLADE_ANGLE": 0.0,
        "CAM_SIZE_USE_PROJECT": True,
        "CAM_WIDTH": 1280,
        "CAM_HEIGHT": 720,
        "CAM_EXPOSURE": 1.0,
        "CAM_GAMMA": 2.2,
    }

    # Always include frame 0; then every authored keyframe
    frames = set(node.keyframes.keys()) if node.keyframes else set()
    frames.add(0)

    for frame_num in sorted(frames):
        time = start_frame + (frame_num * fps_scale)
        # node.keyframes contains MI_HARD_DEFAULTS + per-frame overrides (from parser).
        # node.default_values = creation placement — NOT used here.
        kf = node.keyframes.get(frame_num, {})
        # Merge: cam hard defaults → per-frame keyframe values
        cv = dict(_CAM_HARD_DEFAULTS)
        cv.update(kf)

        # ── FOV ─────────────────────────────────────────────────────────────
        fov = cv.get("CAM_FOV", 45.0)
        cam_data.angle = math.radians(float(fov))
        cam_data.keyframe_insert("lens", frame=time)

        # ── DOF toggle ──────────────────────────────────────────────────────
        cam_data.dof.use_dof = bool(cv.get("CAM_DOF", False))
        cam_data.dof.keyframe_insert("use_dof", frame=time)

        # ── Focus distance ───────────────────────────────────────────────────
        depth = float(cv.get("CAM_DOF_DEPTH", 0.0))
        if depth <= 0:
            depth = float(cv.get("CAM_ROTATE_DISTANCE", 100.0))
        cam_data.dof.focus_distance = depth * MI_SCALE
        cam_data.dof.keyframe_insert("focus_distance", frame=time)

        # ── Aperture f-stop (from blur size) ────────────────────────────────
        blur = float(cv.get("CAM_DOF_BLUR_SIZE", 0.01))
        fstop = 128.0 if blur <= 0 else 1.0 / (blur * 5.0)
        cam_data.dof.aperture_fstop = max(0.1, min(128.0, fstop))
        cam_data.dof.keyframe_insert("aperture_fstop", frame=time)

        # ── Aperture blades ──────────────────────────────────────────────────
        cam_data.dof.aperture_blades = int(cv.get("CAM_BLADE_AMOUNT", 0))
        cam_data.dof.keyframe_insert("aperture_blades", frame=time)

        # ── Aperture ratio ───────────────────────────────────────────────────
        cam_data.dof.aperture_ratio = float(cv.get("CAM_DOF_BLUR_RATIO", 1.0))
        cam_data.dof.keyframe_insert("aperture_ratio", frame=time)

        # ── Aperture rotation ────────────────────────────────────────────────
        cam_data.dof.aperture_rotation = math.radians(float(cv.get("CAM_BLADE_ANGLE", 0.0)))
        cam_data.dof.keyframe_insert("aperture_rotation", frame=time)

        # ── Lens Z offset: NOT applied.
        # CAM_ROTATE_DISTANCE in MI controls a camera-orbit pivot offset, but
        # Blender cameras approximate this differently. Applying this offset
        # shifts the physical lens away from the pivot and causes a mismatch
        # with the original MI framing. Store as custom prop only (via
        # apply_node_custom_props, called at the end of this function).

    # ── Camera shake (noise modifiers, not keyframes) ────────────────────────
    if cam_lens_obj.parent:
        apply_camera_shake(cam_lens_obj.parent, node)

    # ── Scene-level settings (only frame 0) ─────────────────────────────────
    # node.default_values = creation placement — NOT used here.
    # Use frame 0 keyframe data (which contains MI_HARD_DEFAULTS + per-frame overrides).
    cv0 = dict(_CAM_HARD_DEFAULTS)
    if node.keyframes:
        cv0.update(node.keyframes.get(0, {}))

    if not cv0.get("CAM_SIZE_USE_PROJECT", True):
        bpy.context.scene.render.resolution_x = int(cv0.get("CAM_WIDTH",  1280))
        bpy.context.scene.render.resolution_y = int(cv0.get("CAM_HEIGHT", 720))

    if "CAM_EXPOSURE" in cv0:
        bpy.context.scene.view_settings.exposure = math.log2(
            max(0.01, float(cv0["CAM_EXPOSURE"]))
        )
    if "CAM_GAMMA" in cv0:
        bpy.context.scene.view_settings.gamma = float(cv0["CAM_GAMMA"]) / 2.2

    # ── MI custom props (post-processing, etc.) ──────────────────────────────
    # Called here for the LENS object; builder.py does NOT call it again
    # because camera is excluded from _pivot_custom_prop_types.
    apply_node_custom_props(cam_lens_obj, node, start_frame, fps_scale)

    # ── Static appearance flags ──────────────────────────────────────────────
    apply_node_static_props(cam_lens_obj, node)

import bpy
import math
from ..constants import MI_SCALE
from .props import apply_node_custom_props

_MI_CAMERA_SCALAR_PROPS = {
    "CAM_BLOOM": 0.0, "CAM_VIGNETTE": 0.0, "CAM_CA": 0.0,
    "CAM_DOF_RANGE": 200.0, "CAM_DOF_FADE_SIZE": 100.0, "CAM_DOF_BIAS": 0.0,
    "CAM_DOF_THRESHOLD": 0.0, "CAM_DOF_GAIN": 0.0,
    "CAM_DOF_FRINGE_RED": 1.0, "CAM_DOF_FRINGE_GREEN": 1.0, "CAM_DOF_FRINGE_BLUE": 1.0,
    "CAM_DOF_FRINGE_ANGLE_RED": 90.0, "CAM_DOF_FRINGE_ANGLE_GREEN": -135.0, "CAM_DOF_FRINGE_ANGLE_BLUE": -45.0,
    "CAM_BLOOM_THRESHOLD": 0.85, "CAM_BLOOM_INTENSITY": 0.4, "CAM_BLOOM_RADIUS": 1.0, "CAM_BLOOM_RATIO": 0.0,
    "CAM_LENS_DIRT_BLOOM": 1.0, "CAM_LENS_DIRT_GLOW": 1.0, "CAM_LENS_DIRT_RADIUS": 0.5,
    "CAM_LENS_DIRT_INTENSITY": 0.8, "CAM_LENS_DIRT_POWER": 1.5,
    "CAM_COLOR_CORRECTION": 0.0, "CAM_CONTRAST": 0.0, "CAM_BRIGHTNESS": 0.0, "CAM_SATURATION": 1.0, "CAM_VIBRANCE": 0.0,
    "CAM_GRAIN_STRENGTH": 0.10, "CAM_GRAIN_SATURATION": 0.10, "CAM_GRAIN_SIZE": 1.0,
    "CAM_VIGNETTE_RADIUS": 1.0, "CAM_VIGNETTE_SOFTNESS": 0.5, "CAM_VIGNETTE_STRENGTH": 1.0,
    "CAM_CA_BLUR_AMOUNT": 0.05, "CAM_CA_RED_OFFSET": 0.12, "CAM_CA_GREEN_OFFSET": 0.08, "CAM_CA_BLUE_OFFSET": 0.04,
    "CAM_DISTORT_ZOOM_AMOUNT": 1.0, "CAM_DISTORT_AMOUNT": 0.05,
    "CAM_SIZE_KEEP_ASPECT_RATIO": 1.0, "CAM_SHAKE_MODE": 1.0,
}

_MI_CAMERA_COLOR_PROPS = {
    "CAM_BLOOM_BLEND": (1.0, 1.0, 1.0),
    "CAM_VIGNETTE_COLOR": (0.0, 0.0, 0.0),
    "CAM_COLOR_BURN": (1.0, 1.0, 1.0),
}

_MI_CAMERA_DESCRIPTIONS = {
    "CAM_BLOOM": "MI: Bloom effect enabled",
    "CAM_VIGNETTE": "MI: Vignette effect enabled",
    "CAM_CA": "MI: Chromatic aberration effect enabled",
    "CAM_DOF_RANGE": "MI: DoF in-focus range",
    "CAM_DOF_FADE_SIZE": "MI: DoF fade size",
    "CAM_DOF_BIAS": "MI: DoF bias",
    "CAM_DOF_THRESHOLD": "MI: DoF threshold",
    "CAM_DOF_GAIN": "MI: DoF gain",
    "CAM_BLOOM_THRESHOLD": "MI: Bloom threshold",
    "CAM_BLOOM_INTENSITY": "MI: Bloom intensity",
    "CAM_BLOOM_RADIUS": "MI: Bloom radius",
    "CAM_BLOOM_BLEND": "MI: Bloom blend color",
    "CAM_COLOR_CORRECTION": "MI: Color correction enabled",
    "CAM_CONTRAST": "MI: Contrast",
    "CAM_BRIGHTNESS": "MI: Brightness",
    "CAM_SATURATION": "MI: Saturation",
    "CAM_VIBRANCE": "MI: Vibrance",
    "CAM_VIGNETTE_RADIUS": "MI: Vignette radius",
    "CAM_VIGNETTE_COLOR": "MI: Vignette color",
}

def get_camera_props():
    return _MI_CAMERA_SCALAR_PROPS, _MI_CAMERA_COLOR_PROPS, _MI_CAMERA_DESCRIPTIONS

def apply_camera_shake(pivot_obj, node):
    """Apply camera shake as Noise modifiers on the pivot object's rotation F-curves."""
    dv = node.default_values
    if not dv.get("CAM_SHAKE", False): return
        
    sh_strengths = [dv.get(f"CAM_SHAKE_STRENGTH_{c}", 1.0) for c in "XZY"]
    sh_speeds = [dv.get(f"CAM_SHAKE_SPEED_{c}", 1.0) for c in "XZY"]

    if not pivot_obj.animation_data or not pivot_obj.animation_data.action:
        pivot_obj.animation_data_create()
        pivot_obj.animation_data.action = bpy.data.actions.new(name=pivot_obj.name + "_Shake")

    action = pivot_obj.animation_data.action
    for i in range(3):
        strength = sh_strengths[i]
        if strength <= 0: continue
        fcurve = action.fcurves.find("rotation_euler", index=i) or action.fcurves.new("rotation_euler", index=i)
        if not fcurve.keyframe_points: fcurve.keyframe_points.insert(0, pivot_obj.rotation_euler[i])

        mod = fcurve.modifiers.new('NOISE')
        mod.amplitude = math.radians(strength)
        mod.scale = 5.0 / max(0.01, sh_speeds[i])
        mod.phase = i * 100.0

def apply_camera_properties(cam_lens_obj, node, start_frame, fps_scale):
    """Apply camera-specific lens properties and keyframes."""
    cam_data = cam_lens_obj.data
    dv = node.default_values
    cam_data.sensor_fit = 'VERTICAL'

    frames = set(node.keyframes.keys()) if node.keyframes else {0}
    frames.add(0)

    for frame_num in sorted(frames):
        time = start_frame + (frame_num * fps_scale)
        cv = dict(dv)
        if node.keyframes: cv.update(node.keyframes.get(frame_num, {}))

        if "CAM_FOV" in cv or frame_num == 0:
            cam_data.angle = math.radians(cv.get("CAM_FOV", 45.0))
            cam_data.keyframe_insert("lens", frame=time)

        if "CAM_DOF" in cv or frame_num == 0:
            cam_data.dof.use_dof = bool(cv.get("CAM_DOF", False))
            cam_data.dof.keyframe_insert("use_dof", frame=time)

        if any(k in cv for k in ["CAM_DOF_DEPTH", "CAM_ROTATE_DISTANCE"]) or frame_num == 0:
            depth = cv.get("CAM_DOF_DEPTH", 0.0)
            if depth <= 0: depth = cv.get("CAM_ROTATE_DISTANCE", 100.0)
            cam_data.dof.focus_distance = depth * MI_SCALE
            cam_data.dof.keyframe_insert("focus_distance", frame=time)

        if "CAM_DOF_BLUR_SIZE" in cv or frame_num == 0:
            blur = cv.get("CAM_DOF_BLUR_SIZE", 0.01)
            fstop = 128.0 if blur <= 0 else 1.0 / (blur * 5.0)
            cam_data.dof.aperture_fstop = max(0.1, min(128.0, fstop))
            cam_data.dof.keyframe_insert("aperture_fstop", frame=time)

        for prop, key in [("aperture_blades", "CAM_BLADE_AMOUNT"), ("aperture_ratio", "CAM_DOF_BLUR_RATIO")]:
            if key in cv or frame_num == 0:
                setattr(cam_data.dof, prop, cv.get(key, 0 if "AMOUNT" in key else 1.0))
                cam_data.dof.keyframe_insert(prop, frame=time)
        
        if "CAM_BLADE_ANGLE" in cv or frame_num == 0:
            cam_data.dof.aperture_rotation = math.radians(cv.get("CAM_BLADE_ANGLE", 0.0))
            cam_data.dof.keyframe_insert("aperture_rotation", frame=time)

        if "CAM_ROTATE_DISTANCE" in cv or frame_num == 0:
            cam_lens_obj.location[2] = cv.get("CAM_ROTATE_DISTANCE", 0.0) * MI_SCALE
            cam_lens_obj.keyframe_insert("location", index=2, frame=time)

    if cam_lens_obj.parent: apply_camera_shake(cam_lens_obj.parent, node)

    # Scene settings (frame 0)
    cv0 = dict(dv)
    if node.keyframes: cv0.update(node.keyframes.get(0, {}))
    if not cv0.get("CAM_SIZE_USE_PROJECT", True):
        bpy.context.scene.render.resolution_x = int(cv0.get("CAM_WIDTH", 1280))
        bpy.context.scene.render.resolution_y = int(cv0.get("CAM_HEIGHT", 720))
    if "CAM_EXPOSURE" in cv0:
        bpy.context.scene.view_settings.exposure = math.log2(max(0.01, cv0["CAM_EXPOSURE"]))
    if "CAM_GAMMA" in cv0:
        bpy.context.scene.view_settings.gamma = cv0["CAM_GAMMA"] / 2.2

    apply_node_custom_props(cam_lens_obj, node, start_frame, fps_scale)

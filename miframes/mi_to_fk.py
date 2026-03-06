import bpy
import re
from collections import defaultdict
from mathutils import Matrix

# ─── MI → FK Bone Mapping ───────────────────────────────────────────────────
# Maps MI bone names to their corresponding FK bone names.
# Since the parent-child hierarchies differ between MI and FK chains,
# we must bake world-space transforms (not just copy local transforms).

MI_TO_FK_MAP = {
    "MI_Root":          "Root",
    "MI_Head":          "Head root",
    "MI_arm.R":         "Shoulder.R",
    "MI_arm.upper.R":   "arm.fk.upper.R",
    "MI_arm.lower.R":   "arm.fk.lower.R",
    "MI_wrist.R":       "arm.wrist.ctrl.R",
    "MI_arm.L":         "Shoulder.L",
    "MI_arm.upper.L":   "arm.fk.upper.L",
    "MI_arm.lower.L":   "arm.fk.lower.L",
    "MI_wrist.L":       "arm.wrist.ctrl.L",
    "MI_Body Upper":    "Body Upper",
    "MI_Body Lower":    "Hip",
    "MI_leg.R":         "leg.root.R",
    "MI_leg.upper.R":   "leg.fk.upper.R",
    "MI_leg.lower.R":   "leg.fk.lower.R",
    "MI_ankle.R":       "ankle_crontrol.R",
    "MI_leg.L":         "leg.root.L",
    "MI_leg.upper.L":   "leg.fk.upper.L",
    "MI_leg.lower.L":   "leg.fk.lower.L",
    "MI_ankle.L":       "ankle_crontrol.L",
}

# IK/FK switch properties: set to 0.0 = FK mode
IK_FK_PROPS = [
    "arm-L-fk-ik",
    "arm-R-fk-ik",
    "leg-L-fk-ik",
    "leg-R-fk-ik",
]


def _get_bone_depth(pose_bone):
    """Get the hierarchy depth of a pose bone (0 = root)."""
    depth = 0
    b = pose_bone
    while b.parent:
        depth += 1
        b = b.parent
    return depth


def _get_keyed_frames(arm):
    """Collect all unique keyframe frame numbers from MI bones in the action."""
    if not arm.animation_data or not arm.animation_data.action:
        return []

    action = arm.animation_data.action
    mi_bone_names = set(MI_TO_FK_MAP.keys())
    frames = set()

    for fcurve in action.fcurves:
        m = re.match(r'pose\.bones\["([^"]+)"\]\.', fcurve.data_path)
        if m and m.group(1) in mi_bone_names:
            for kp in fcurve.keyframe_points:
                frames.add(int(kp.co.x))

    if not frames:
        return []
    
    # Bake EVERY frame to prevent motion loss on >180 degree spins
    min_frame = min(frames)
    max_frame = max(frames)
    
    return list(range(int(min_frame), int(max_frame) + 1))


def _get_mi_fcurves(action):
    """Get all fcurves that belong to MI bones."""
    mi_bone_names = set(MI_TO_FK_MAP.keys())
    results = []
    for fcurve in action.fcurves:
        m = re.match(r'pose\.bones\["([^"]+)"\]\.', fcurve.data_path)
        if m and m.group(1) in mi_bone_names:
            results.append(fcurve)
    return results


def bake_mi_to_fk(context):
    """
    Bake world-space transforms from MI bones onto FK bones,
    then clean up MI keyframes and switch all limbs to FK mode.
    
    The bake correctly handles differing parent-child hierarchies by:
    1. Sampling MI bone world (armature-space) matrices while MI mode is active
    2. Switching to FK mode
    3. Applying sampled matrices to FK bones using Blender's built-in
       bone.matrix setter, which handles parent chain & rest pose conversion
    4. Processing bones in parent-first order with depsgraph updates
       between hierarchy levels so child bones see correct parent transforms
    
    Returns (success: bool, message: str)
    """
    arm = context.active_object
    if not arm or arm.type != 'ARMATURE':
        return False, "No armature selected"

    if not arm.animation_data or not arm.animation_data.action:
        return False, "No animation data / action on the armature"

    pose_bones = arm.pose.bones
    action = arm.animation_data.action

    # --- 1. Validate that the bones exist ---
    valid_pairs = []
    for mi_name, fk_name in MI_TO_FK_MAP.items():
        mi_bone = pose_bones.get(mi_name)
        fk_bone = pose_bones.get(fk_name)
        if mi_bone and fk_bone:
            valid_pairs.append((mi_name, fk_name, mi_bone, fk_bone))

    if not valid_pairs:
        return False, "No matching MI/FK bone pairs found in the armature"

    # --- 2. Collect all keyframe times from MI bones ---
    frames = _get_keyed_frames(arm)
    if not frames:
        return False, "No keyframes found on MI bones"

    # --- 3. Group FK bones by hierarchy depth for correct parent-first processing ---
    depth_groups = defaultdict(list)
    for item in valid_pairs:
        fk_bone = item[3]
        depth = _get_bone_depth(fk_bone)
        depth_groups[depth].append(item)
    
    sorted_depths = sorted(depth_groups.keys())

    # --- 4. Sample MI bone world-space matrices WHILE MI MODE IS STILL ACTIVE ---
    # This is critical: MI bones may depend on mi_mapping_mode for their evaluated pose
    mi_world_samples = {}  # {frame: {mi_name: matrix}}

    scene = context.scene
    original_frame = scene.frame_current

    for frame in frames:
        scene.frame_set(frame)
        context.view_layer.update()
        mi_world_samples[frame] = {}
        for mi_name, fk_name, mi_bone, fk_bone in valid_pairs:
            # bone.matrix is the armature-space (object-space) pose matrix
            mi_world_samples[frame][mi_name] = mi_bone.matrix.copy()

    # --- 5. Switch to FK mode BEFORE baking ---
    if "prop.limbs" in pose_bones:
        limbs_bone = pose_bones["prop.limbs"]
        for prop_name in IK_FK_PROPS:
            if prop_name in limbs_bone:
                limbs_bone[prop_name] = 0.0

    # Turn off MI mapping mode
    if "logic" in pose_bones:
        logic_bone = pose_bones["logic"]
        if "mi_mapping_mode" in logic_bone:
            logic_bone["mi_mapping_mode"] = 0.0

    # Turn on head_inherit_rotation for accurate head transformation
    if "prop.head" in pose_bones:
        head_bone = pose_bones["prop.head"]
        if "head_inherit_rotation" in head_bone:
            head_bone["head_inherit_rotation"] = 1.0

    # Ensure all FK bones are operating in QUATERNION mode before setting their matrices.
    # If we set .matrix while they are in Euler mode, Blender only updates Euler channels,
    # and our quaternion keyframes later would just save the un-updated/default values!
    for mi_name, fk_name, mi_bone, fk_bone in valid_pairs:
        fk_bone.rotation_mode = 'QUATERNION'

    # Force depsgraph update so FK chain is now active
    context.view_layer.update()

    # --- 6. Bake world-space transforms onto FK bones ---
    # Process frame by frame. Within each frame, process bones in
    # parent-first order (by depth), updating the depsgraph between
    # depth levels so child bones see correct parent transforms.
    previous_quats = {}
    
    for frame in frames:
        scene.frame_set(frame)
        context.view_layer.update()

        # Apply MI world matrices to FK bones, depth level by depth level
        for depth in sorted_depths:
            for mi_name, fk_name, mi_bone, fk_bone in depth_groups[depth]:
                target_matrix = mi_world_samples[frame][mi_name]
                
                # Use Blender's built-in matrix setter.
                # This correctly computes matrix_basis by accounting for
                # the FK bone's parent chain and rest pose automatically.
                fk_bone.matrix = target_matrix
            
            # Update depsgraph after each depth level so that child bones
            # in the next level see the correct parent transforms
            context.view_layer.update()

        # Now keyframe all FK bones (order doesn't matter for keyframing)
        for mi_name, fk_name, mi_bone, fk_bone in valid_pairs:
            # Enforce quaternion continuity to prevent flips or lost spins (e.g., 360-degree jumps)
            current_quat = fk_bone.rotation_quaternion.copy()
            if fk_name in previous_quats:
                current_quat.make_compatible(previous_quats[fk_name])
                fk_bone.rotation_quaternion = current_quat
            previous_quats[fk_name] = current_quat.copy()

            fk_bone.keyframe_insert("rotation_quaternion", frame=frame)
            fk_bone.keyframe_insert("location", frame=frame)
            fk_bone.keyframe_insert("scale", frame=frame)

    # --- 7. Remove ALL keyframes from MI bones ---
    mi_fcurves = _get_mi_fcurves(action)
    for fc in mi_fcurves:
        action.fcurves.remove(fc)

    # --- 8. Reset MI bones to rest pose ---
    for mi_name, fk_name, mi_bone, fk_bone in valid_pairs:
        mi_bone.rotation_mode = 'QUATERNION'
        mi_bone.rotation_quaternion = (1, 0, 0, 0)
        mi_bone.location = (0, 0, 0)
        mi_bone.scale = (1, 1, 1)

    # --- 9. Keyframe the IK/FK switch and Head Inherit Rotation at the first frame ---
    if frames:
        first_frame = frames[0]
        if "prop.limbs" in pose_bones:
            limbs_bone = pose_bones["prop.limbs"]
            for prop_name in IK_FK_PROPS:
                if prop_name in limbs_bone:
                    limbs_bone[prop_name] = 0.0
                    limbs_bone.keyframe_insert(
                        data_path=f'["{prop_name}"]',
                        frame=first_frame
                    )
        
        if "prop.head" in pose_bones:
            head_bone = pose_bones["prop.head"]
            if "head_inherit_rotation" in head_bone:
                head_bone["head_inherit_rotation"] = 1.0
                head_bone.keyframe_insert(
                    data_path='["head_inherit_rotation"]',
                    frame=first_frame
                )

    # Restore original frame
    scene.frame_set(original_frame)
    context.view_layer.update()

    return True, f"Baked {len(frames)} frames across {len(valid_pairs)} bone pairs to FK"


class MI_OT_BakeToFK(bpy.types.Operator):
    """Bake MI bone animations to FK bones, clear MI keyframes, and switch to FK mode"""
    bl_idname = "mi.bake_to_fk"
    bl_label = "Bake MI → FK?"
    bl_description = (
        "Bake world-space transforms from MI bones to FK bones, "
        "remove MI keyframes, and switch all limbs to FK mode"
    )
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        success, msg = bake_mi_to_fk(context)
        if success:
            self.report({'INFO'}, msg)
            return {'FINISHED'}
        else:
            self.report({'ERROR'}, msg)
            return {'CANCELLED'}

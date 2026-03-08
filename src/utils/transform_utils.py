import bpy
import math
from mathutils import Euler
from ..utils import core

MI_SCALE = 1.0 / 16.0
apply_mi_transition = core.apply_mi_transition
MIBaseImporter = core.MIBaseImporter

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
    """Apply light properties and keyframes based on Mine-imator logic."""
    l_data = light_obj.data
    dv = node.default_values
    
    # 常量定义
    # MI_SCALE = 1.0 / 16.0 (Already defined globally)
    # 亮度修正系数：用于将 MI 的非物理强度转换为 Blender 的 Watts
    # 经验值：50.0 到 100.0 之间通常能获得较好的初始效果
    POWER_MULTIPLIER = 80.0 

    # 1. Colour (颜色) - MI 不支持色温，直接转换 Hex
    def get_rgb(val):
        return _hex_to_rgb(val)

    # 2. Radius (半径)
    def get_radius(size_val):
        return size_val * MI_SCALE

    # 3. Power (功率) - 结合强度和范围
    def get_energy(strength, l_range):
        """
        修正后的能量计算公式 (防止数值爆炸)
        逻辑：
        1. 基础功率：Strength * 100W (MI的默认强度1.0对应100W灯泡)
        2. 距离补偿：不再使用平方，改为线性补偿。每增加1米范围，增加约 5W - 10W 的功率。
        这样即使范围很大，数值也只会线性增长，不会指数爆炸。
        """
        dist_meters = l_range * MI_SCALE
        
        # 基础亮度 (Watts)
        base_power = strength * 50.0 
        
        # 距离增益 (防止远距离看不见，但也不要太亮)
        # 例如：Range 250 (15米) -> 增益 150W -> 总共 200W (合理)
        # 例如：Range 1000 (62米) -> 增益 620W -> 总共 670W (合理)
        range_boost = dist_meters * 10.0 * strength
        
        return (base_power + range_boost) * 10.0

    # 4. Spot Shape (聚光灯形状)
    def get_spot_size(radius_val):
        # MI 的 radius 通常指半角或特定比例，Blender 需要全角弧度
        # 假设 MI 数据是角度值 (Degrees)
        return math.radians(radius_val * 2.0)

    def get_spot_blend(sharpness_val):
        # MI Sharpness: 1.0 (锐利) -> 0.0 (柔和)
        # Blender Blend: 0.0 (锐利) -> 1.0 (柔和)
        return 1.0 - max(0.0, min(1.0, sharpness_val))

    # --- 关键帧与默认值处理逻辑 ---
    frames = set(node.keyframes.keys()) if node.keyframes else set()
    frames.add(0)

    for frame_num in sorted(frames):
        time = start_frame + (frame_num * fps_scale)
        # 获取当前帧数值
        if frame_num == 0:
            current_values = dict(dv)
            if node.keyframes and 0 in node.keyframes:
                current_values.update(node.keyframes[0])
        else:
            current_values = node.keyframes.get(frame_num, {})

        # 应用 Power & Range
        # 默认 Strength 为 1.0, 默认 Range 为 250
        s = float(current_values.get("LIGHT_STRENGTH", 1.0))
        r = float(current_values.get("LIGHT_RANGE", 250.0))
        
        # 1. 设置 Power (Energy)
        l_data.energy = get_energy(s, r)
        l_data.keyframe_insert("energy", frame=time)
        
        # 2. 设置物理截断距离 (Cutoff)
        # 这一点非常重要：让 Blender 真的在那个距离把光切断，而不是靠无限增加亮度来模拟
        l_data.use_custom_distance = True
        l_data.cutoff_distance = r * MI_SCALE
        l_data.keyframe_insert("cutoff_distance", frame=time)

        # 3. 设置 Color
        if "LIGHT_COLOR" in current_values or frame_num == 0:
            c_val = current_values.get("LIGHT_COLOR", "#FFFFFF")
            l_data.color = get_rgb(c_val)
            l_data.keyframe_insert("color", frame=time)

        # 应用 Radius
        if "LIGHT_SIZE" in current_values or frame_num == 0:
            sz = current_values.get("LIGHT_SIZE", 0.0)
            l_data.shadow_soft_size = get_radius(sz) # Eevee/Cycles通用半径
            l_data.keyframe_insert("shadow_soft_size", frame=time)

        # 应用聚光灯特有参数
        if l_data.type == 'SPOT':
            if any(k in current_values for k in ["LIGHT_SPOT_RADIUS", "LIGHT_SPOT_SHARPNESS"]) or frame_num == 0:
                spot_r = current_values.get("LIGHT_SPOT_RADIUS", 45.0)
                spot_s = current_values.get("LIGHT_SPOT_SHARPNESS", 0.5)
                
                l_data.spot_size = get_spot_size(spot_r)
                l_data.spot_blend = get_spot_blend(spot_s)
                l_data.keyframe_insert("spot_size", frame=time)
                l_data.keyframe_insert("spot_blend", frame=time)




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
        if fcurve.data_path in ("location", "rotation_euler", "scale"):
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

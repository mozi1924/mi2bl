# Mine-imator 与 Blender 相机系统对比及解析实现指南

本文档整理了 Mine-imator (简称为 MI) 中的相机级参数（基于 `e_value` 中的 `CAM_` 属性），并列出了在 Blender 中的等效参数或实现方案。在开发 `.miframes` 或 `.miobject` 文件解析器并导入到 Blender 时，可参考此映射表。

---

## 1. 基础相机参数 (General Camera Settings)

Mine-imator 的这部分参数主要控制相机的基础视角和光圈形态。

| Mine-imator 属性 | 说明 | Blender 对应属性 / 实现方式 |
| :--- | :--- | :--- |
| `CAM_FOV` | 相机视场角 (Field of View) | `bpy.context.object.data.angle` (视场角)<br>注意：MI 通常以垂直/水平 FOV 为单位，导入时需注意换算和 `sensor_fit`。 |
| `CAM_SIZE_USE_PROJECT` | 是否使用项目全局分辨率 | 由于 Blender 相机本身不携带分辨率参数，而是跟随 Scene (`bpy.context.scene.render.resolution_x / y`)，因此这通常对应动态设置场景分辨率。解析时若为 False，需要通过脚本动态改写当前 Scene 的渲染分辨率，或将该属性存储为自定义属性。 |
| `CAM_SIZE_KEEP_ASPECT_RATIO` | 强制保持宽高比 | 关联到相机的宽高比例，或者 Scene 渲染比例设置。 |
| `CAM_WIDTH` / `CAM_HEIGHT` | 自定义分辨率尺寸 | 对应 `bpy.context.scene.render.resolution_x` 和 `resolution_y`。 |
| `CAM_BLADE_AMOUNT` | 光圈叶片数量（影响景深光斑形状） | `bpy.context.object.data.dof.aperture_blades` |
| `CAM_BLADE_ANGLE` | 光圈叶片旋转角度 | `bpy.context.object.data.dof.aperture_rotation` |

---

## 2. 曝光与色彩管理 (Lighting & Exposure)

这些属性控制整个画面的曝光、伽马和色调映射。

| Mine-imator 属性 | 说明 | Blender 对应属性 / 实现方式 |
| :--- | :--- | :--- |
| `CAM_LIGHT_MANAGEMENT` | 是否启用光照色调管理 | Blender 中默认为开启 (Scene > Color Management)。如果关闭，相当于将 View Transform 设为 Standard/Raw。 |
| `CAM_TONEMAPPER` | 色调映射算法 | `bpy.context.scene.view_settings.view_transform` (如 Filmic, AgX, Standard 等)。 |
| `CAM_EXPOSURE` | 曝光值 | `bpy.context.scene.view_settings.exposure` |
| `CAM_GAMMA` | 伽马值 | `bpy.context.scene.view_settings.gamma` |

---

## 3. 景深系统 (Depth of Field)

Mine-imator 提供了一套非常定制化的景深和色散（Fringe）参数，Blender 中的对应关系部分是原生的，部分需要 compositor 介入。

| Mine-imator 属性 | 说明 | Blender 对应属性 / 实现方式 |
| :--- | :--- | :--- |
| `CAM_DOF` | 启用景深 | `bpy.context.object.data.dof.use_dof = True` |
| `CAM_DOF_DEPTH` | 焦距距离 | `bpy.context.object.data.dof.focus_distance` |
| `CAM_DOF_RANGE`<br>`CAM_DOF_FADE_SIZE` | 景深影响范围/褪色 | Blender 用 `aperture_fstop` 控制景深范围。如果是定制褪色，没有直接对应，可能需要将 F-stop 进行公式换算。 |
| `CAM_DOF_BLUR_SIZE` | 虚化模糊大小 | 同样映射到 `aperture_fstop`。 |
| `CAM_DOF_BLUR_RATIO` | 模糊长宽比 (Anamorphic) | `bpy.context.object.data.dof.aperture_ratio` |
| `CAM_DOF_BIAS`, `CAM_DOF_THRESHOLD` | 基于亮度的光斑阈值 | 对应 Eevee/Cycles 景深散景的高亮溢出，但不够精准，在物理渲染器中通过高光材质和 F-stop 自动实现。 |
| `CAM_DOF_FRINGE*` (各类) | 景深色散 / 假轴向色差 | Blender 原生 DoF 不太容易实现定制通道的角度色散，可用后期节点 (Compositor -> Lens Distortion)。 |

---

## 4. 镜头晃动与摄影机动画 (Camera Shake)

Mine-imator 内置了相机晃动的功能。在 Blender 中，由于这是时序动作，需要转换为 F-Curve 上的 Noise Modifier（噪声修改器）。

| Mine-imator 属性 | 说明 | Blender 对应属性 / 实现方式 |
| :--- | :--- | :--- |
| `CAM_SHAKE` | 启用相机晃动 | 若开启，则需要为相机的 X/Y/Z 位移或旋转轨道的 F-Curve 添加 Noise 修改器。 |
| `CAM_SHAKE_MODE` | 晃动模式 | 控制 Noise 的 Phase (相位) 和 Blend (混合方式)。 |
| `CAM_SHAKE_STRENGTH_X/Y/Z` | 晃动幅度 (X/Y/Z) | `fcurve.modifiers["Noise"].amplitude` 参数针对不同的轴向。 |
| `CAM_SHAKE_SPEED_X/Y/Z` | 晃动频率/速度 (X/Y/Z) | `fcurve.modifiers["Noise"].scale` (缩放参数与频率成反比)。 |

*_注意：对于导入器来说，读取到 Shake 属性后，需要动态检测并创建 Noise Modifier！_*

---

## 5. 后期处理 - 泛光与特效 (Post-Processing - Bloom & Lens Dirt)

泛光在 Eevee 中有直接的支持，Cycles 中通常在 Compositor (合成节点) 实现。

| Mine-imator 属性 | 说明 | Blender 对应属性 / 实现方式 |
| :--- | :--- | :--- |
| `CAM_BLOOM` | 开启泛光 | Eevee: `bpy.context.scene.eevee.use_bloom = True` <br> Compositor: 插入 `Glare` (Fog Glow 模式) 节点。 |
| `CAM_BLOOM_THRESHOLD` | 泛光阈值 | Eevee: `eevee.bloom_threshold` <br> Compositor_Glare: [threshold](file:///Users/jaxlocke/Mine-imator/GmProject/scripts/action_tl_frame_cam_bloom_threshold) |
| `CAM_BLOOM_INTENSITY` | 泛光强度 | Eevee: `eevee.bloom_intensity` <br> Compositor_Glare: `mix`。 |
| `CAM_BLOOM_RADIUS` | 泛光半径 | Eevee: `eevee.bloom_radius` <br> Compositor_Glare: `size`。 |
| `CAM_BLOOM_RATIO`, `BLEND` | 泛光比例与混合模式 | 无完全等价原生参数，可由合成节点自行调整 Mix。 |
| `CAM_LENS_DIRT*` (各类) | 镜头污垢 | \- 必须通过 **合成节点 (Compositor)**。将污渍贴图叠加（Multiply）或者相加（Add）到泛光的输出遮罩上。 |

---

## 6. 后期处理 - 调色、暗角与色差 (Color Correction, Vignette, CA)

在 Blender 中，这部分几乎全部要通过 **Compositor (合成节点树)** 来构建。

| Mine-imator 属性 | 说明 | Blender Compositor 实现方式 |
| :--- | :--- | :--- |
| `CAM_COLOR_CORRECTION` | 开启色彩校正 | 开启节点树分支的开关。 |
| `CAM_CONTRAST`, `BRIGHTNESS` | 对比度, 亮度 | `Color Balance` 或 `Brightness/Contrast` 节点。 |
| `CAM_SATURATION` | 饱和度 | `Hue/Saturation/Value` 节点的 Saturation 参数。 |
| `CAM_VIBRANCE`, `COLOR_BURN` | 自然饱和度与色彩加深 | `Mix` 节点，使用 Color Burn 混合模式，Vibrance 可分离饱和度贴图制作。 |
| `CAM_GRAIN*` (各类) | 胶片颗粒噪点 | 使用噪波纹理叠加：`Texture` -> 噪波映射 + `Mix` (Overlay) 到图像上。强度对应叠加属性。 |
| `CAM_VIGNETTE*` (各类) | 暗角强度与半径 | `Ellipse Mask` 产生黑框 -> `Blur` 模糊边缘 -> `Mix` (Multiply) 到图像中，强度映射 `fac`。 |
| `CAM_CA` / `CA_RED_OFFSET` 等 | 色差 (Chromatic Aberration) | 使用 `Lens Distortion` 层分离节点并设置 Dispersion，也可以将 RGB 三通道分离 (`Separate Color`)，分别平移/缩放（偏移量对应 OFFSET），然后 `Combine Color` 组合！ |
| `CAM_DISTORT*` (各类) | 镜头畸变透视 | 使用 `Lens Distortion` 节点的 Distort 参数。 |

---

## 总结与开发建议

在开发针对 Blender 的 `.miframes` 解析器时：

1. **结构分离（驱动化）**：由于 MI 可以把这些数据做成关键帧，所以我们强烈建议将这部分 **后期渲染参数做成 Custom Properties 挂载在 Blender 相机物体上**。
2. **构建 Node Group（合成器支持）**：写一个 Python 自动生成 `Mine_Imator_PostProcess` 的 Compositor Node Group。将上述提到的 Color Correction、Vignette、CA 色差等通过 Custom Properties 驱动 (Drivers) 链接到 Node Group 的输入 Socket。
3. **相机噪声自动化**：编写处理逻辑，当侦测到 Camera_Shake 骨骼/通道存在时，遍历 F-curve 加入对应的修改器。

这个实现指南应可以覆盖目前 MI 最新的各类关于参数特性的解析需求了！

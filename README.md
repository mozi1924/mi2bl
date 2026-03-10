# Mi2Bl (Mine-Imator to Blender)

Mi2Bl is a high-fidelity importer designed to bring Mine-Imator projects, objects, and animations into Blender. It focuses on preserving the original animation data, parenting structures, and property mappings while utilizing Blender's native features where possible.

## Key Features

- **High-Fidelity Animation**: Full support for Mine-Imator's transition types (Linear, Instant, Bézier, etc.) mapped directly to Blender's F-curves.
- **Parenting Architecture**: Automatically rebuilds complex Mine-Imator parenting hierarchies using a pivot-based empty system to ensure rotations and scales match perfectly.
- **Native Parameter Mapping**: Maps Mine-Imator value tracks directly to native Blender properties (e.g., Camera FOV, Light Energy) for immediate use with standard Blender tools.
- **Custom Property Preservation**: All Mine-Imator specific properties (Alpha, Emissive, PBR weights) are stored as keyframe-able Custom Properties (prefixed with `mi_`) for use in custom Shaders and Node Groups.

## Native Property Mapping

The following Mine-Imator values are fully translated to **native Blender parameters** (not custom properties):

### General Transforms

| Mine-Imator Value         | Blender Target   | Description                               |
| :------------------------ | :--------------- | :---------------------------------------- |
| `POS_X`, `POS_Y`, `POS_Z` | `location`       | Position (with Y/Z swap and unit scaling) |
| `ROT_X`, `ROT_Y`, `ROT_Z` | `rotation_euler` | Euler Rotation (XYZ order)                |
| `SCA_X`, `SCA_Y`, `SCA_Z` | `scale`          | Object Scale                              |
| `PATH_OBJ`, `PATH_OFFSET` | `Follow Path`    | Follow Path constraint mapping            |

### Specialized Objects

| Mine-Imator Node | Blender Object  | Description                            |
| :--------------- | :-------------- | :------------------------------------- |
| `path`           | `CURVE` (NURBS) | Order 3 curve (quadratic B-spline)     |
| `pathpoint`      | Control Points  | Baked into spline data                 |
| `folder`         | `EMPTY` (Pivot) | Organizational hierarchy               |
| `camera`         | `CAMERA`        | Native lens data with pivot animation  |
| `pointlight`     | `LIGHT` (POINT) | Native light data with pivot animation |
| `spotlight`      | `LIGHT` (SPOT)  | Native light data with pivot animation |

### Camera Properties

| Mine-Imator Value            | Blender Target            | Description                               |
| :--------------------------- | :------------------------ | :---------------------------------------- |
| `CAM_FOV`                    | `lens` (Angle)            | Camera Field of View                      |
| `CAM_DOF` (Toggle)           | `dof.use_dof`             | Enable/Disable Depth of Field             |
| `CAM_DOF_DEPTH`              | `dof.focus_distance`      | Focal point distance                      |
| `CAM_ROTATE_DISTANCE`        | `dof.focus_distance`      | Fallback focal distance if depth is 0     |
| `CAM_DOF_BLUR_SIZE`          | `dof.aperture_fstop`      | Maps blur size to physically-based f-stop |
| `CAM_BLADE_AMOUNT`           | `dof.aperture_blades`     | Bokeh blade count                         |
| `CAM_DOF_BLUR_RATIO`         | `dof.aperture_ratio`      | Anamorphic bokeh squeeze                  |
| `CAM_BLADE_ANGLE`            | `dof.aperture_rotation`   | Rotation of the bokeh shape               |
| `CAM_WIDTH`, `CAM_HEIGHT`    | `render.resolution_x / y` | Scene render resolution                   |
| `CAM_EXPOSURE`               | `view_settings.exposure`  | Color management exposure                 |
| `CAM_GAMMA`                  | `view_settings.gamma`     | Color management gamma                    |
| `CAM_SHAKE` (Strength/Speed) | Noise Modifiers           | Applied to pivot rotation f-curves        |

### Light Properties

| Mine-Imator Value         | Blender Target     | Description                                   |
| :------------------------ | :----------------- | :-------------------------------------------- |
| `LIGHT_STRENGTH`          | `energy`           | Physically converted (Strength × Range² × 4π) |
| `LIGHT_RANGE`             | `cutoff_distance`  | Uses Blender's native Custom Distance cutoff  |
| `LIGHT_COLOR`             | `color`            | Hex integer converted to Linear RGB           |
| `LIGHT_SIZE`              | `shadow_soft_size` | Radius of the light source for soft shadows   |
| `LIGHT_SPECULAR_STRENGTH` | `specular_factor`  | Weight of the specular highlights             |
| `LIGHT_SPOT_RADIUS`       | `spot_size`        | Full-cone angle (Spotlight only)              |
| `LIGHT_SPOT_SHARPNESS`    | `spot_blend`       | Edge softness (Spotlight only)                |

## Custom Properties (`mi_`)

Properties that do not have a 1:1 native equivalent in Blender are stored as **Custom Properties** on the object. These include:

- **Material Tracks**: `mi_alpha`, `mi_emissive`, `mi_metallic`, `mi_roughness`, `mi_subsurface`.
- **Appearance Flags**: `mi_backfaces`, `mi_shadows`, `mi_glow`, `mi_wind`, `mi_blend_mode`.
- **Post-Processing**: `mi_cam_bloom`, `mi_cam_vignette`, `mi_cam_ca`, etc.

## Usage

1. Open the **N-Panel** in the 3D Viewport.
2. Navigate to the **Rig/2** tab.
3. Locate the **Object Animation (.mi\*)** panel.
4. Select a target object, set your **Start Frame**, and click **Load Anim (.mi\*)** to import your `.miobject` or `.miframes` file.

---

_Note: This project is part of the Rig2 Ecosystem._

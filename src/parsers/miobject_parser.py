"""
Parser for .miobject files.

Produces a tree of MINode objects that mirror the Mine-Imator timeline
hierarchy. Each node carries its type, default_values, keyframes, and
references to its children (ordered by parent_tree_index).

Supported timeline types for scene import:
  - "folder"  →  Blender Empty
  - "cube"    →  Blender MI-style Cube mesh
  - "surface" →  Blender MI-style Surface (upright plane) mesh
  - "block"   →  Blender MI-style Block mesh
  - "camera"  →  Blender Camera setup
  - "pointlight" / "spotlight" → Blender Light setup
  - "char" / "bodypart" / "scenery" / "item" / "particle_spawner" / "text" → Empty placeholders
  - "path"      →  Blender NURBS Curve (Order 3, matching MI quadratic B-spline)
  - "pathpoint" →  Control points baked into the parent NURBS curve; no standalone object

MI Default Value System — Three-Layer Design
--------------------------------------------
Mine-Imator uses a "double delta" approach to minimise file size.  Values are
only written to disk when they differ from the layer below:

  Layer 1 (lowest):  MI_HARD_DEFAULTS  — engine hard-coded fallbacks
                     (defined in tl_value_default.gml; e.g. POS=0, SCA=1)
  Layer 2 (middle):  timeline `default_values`  — where the user placed/posed
                     the object when creating it; saved relative to layer 1.
  Layer 3 (highest): per-keyframe data  — only the deltas from layer 2 are
                     written; missing keys mean "same as default_values".

Concrete example (Grass Block.miobject):
  default_values = {POS_X: 26.14924, POS_Y: 0.93506, POS_Z: 0.04474}
  keyframes[0]   = {}   ← empty! means frame 0 == default_values exactly
  keyframes[10]  = {POS_X: -67.50688, ...}  ← explicit delta from default

The parser therefore merges each keyframe as:
  MI_HARD_DEFAULTS  ←  default_values  ←  per-frame values
                    (lowest priority → highest priority)
"""

import json
import math

# ---- MI Hard Defaults Table (from tl_value_default.gml) --------------------
# These are the MI engine's fallback values when a key is absent from a keyframe.
# MI NEVER writes these into keyframes — importers MUST supply them.
# Colors are stored as int (c_white = 16777215, c_black = 0).

MI_HARD_DEFAULTS = {
    # Transform
    "POS_X": 0.0, "POS_Y": 0.0, "POS_Z": 0.0,
    "ROT_X": 0.0, "ROT_Y": 0.0, "ROT_Z": 0.0,
    "SCA_X": 1.0, "SCA_Y": 1.0, "SCA_Z": 1.0,

    # Visibility & Opacity
    "VISIBLE": True,
    "ALPHA": 1.0,

    # Material / PBR tracks
    "RGB_ADD": 0,           # MI stores as int color (c_black = 0)
    "RGB_MUL": 16777215,    # c_white
    "EMISSIVE": 0.0,        # labelled "GLOW" in older docs (brightness of emission)
    "METALLIC": 0.0,
    "ROUGHNESS": 1.0,
    "SUBSURFACE": 0.0,
    "SUBSURFACE_RADIUS_RED": 1.0,
    "SUBSURFACE_RADIUS_GREEN": 1.0,
    "SUBSURFACE_RADIUS_BLUE": 1.0,
    "SUBSURFACE_COLOR": 16777215,   # c_white

    # Glow / Bloom
    "GLOW_COLOR": 16777215,     # c_white
    # "GLOW" is a boolean toggle stored at TL level, not a keyframe value

    # Transition / Easing (meta keys present in every keyframe)
    "TRANSITION": "linear",
    "EASE_IN_X": 1.0,
    "EASE_IN_Y": 0.0,
    "EASE_OUT_X": 0.0,
    "EASE_OUT_Y": 1.0,

    # Bend (body-part specific, default 0)
    "BEND_ANGLE_X": 0.0,
    "BEND_ANGLE_Y": 0.0,
    "BEND_ANGLE_Z": 0.0,
}

# ---- MI Bug Fix (Y/Z swap) ------------------------------------------------

def fix_mi_yz_swap(values):
    """
    Mine-Imator has a known bug where UI Y (Up) and UI Z (Depth)
    are swapped in the saved JSON.  This restores the true UI values.

    Only applies to keys ending in _Y or _Z that are NOT easing params.
    Color keys (e.g. RGB_MUL, GLOW_COLOR) and boolean keys are unaffected.
    """
    fixed = {}
    for k, v in values.items():
        if k.endswith("_Y") and not k.startswith("EASE_"):
            fixed[k[:-2] + "_Z"] = v
        elif k.endswith("_Z") and not k.startswith("EASE_"):
            fixed[k[:-2] + "_Y"] = v
        else:
            fixed[k] = v
    return fixed


def _build_keyframe_baseline(default_values_raw):
    """
    Build the per-timeline effective baseline used as the fallback for every
    keyframe in that timeline.

    Merge order (lowest → highest priority):
      1. MI_HARD_DEFAULTS      — engine hard-coded fallbacks
      2. default_values (raw)  — timeline creation placement / pose

    The result is the value set that MI would use for any key absent from a
    keyframe dict.  It is computed BEFORE fix_mi_yz_swap is applied to the
    per-frame deltas, so we apply the swap here once rather than on every frame.
    """
    baseline = dict(MI_HARD_DEFAULTS)
    # Apply Y/Z swap to default_values before merging, so that the resulting
    # baseline is already in the corrected coordinate space.
    baseline.update(fix_mi_yz_swap(dict(default_values_raw)))
    return baseline


# ---- Data Classes ----------------------------------------------------------

SUPPORTED_TYPES = {
    "folder", "cube", "surface", "block",
    "char", "camera", "audio", "bodypart", "text",
    "scenery", "item", "particle_spawner", "spotlight", "pointlight",
    # Path system
    "path", "pathpoint",
}


class MINode:
    """A single timeline entry from a .miobject file."""

    __slots__ = (
        "id", "type", "name", "temp",
        "parent_id", "parent_tree_index",
        "default_values", "keyframes",
        "inherit",
        "children",
        "rot_point",
        "template_data",
        # Top-level static appearance flags (not in keyframes / default_values)
        "backfaces", "shadows", "ssao",
        "glow", "glow_texture", "only_render_glow",
        "glint_mode",
        "fog", "wind",
        "blend_mode", "alpha_mode",
        # Path-specific data (only set for type=="path")
        "path_data",
    )

    def __init__(self, tl_dict, templates_dict=None):
        if templates_dict is None:
            templates_dict = {}
        self.id = tl_dict.get("id", "")
        self.type = tl_dict.get("type", "")
        self.name = tl_dict.get("name", "")
        self.temp = tl_dict.get("temp", "null")  # template id
        self.template_data = templates_dict.get(self.temp, {})

        self.parent_id = tl_dict.get("parent", "root")
        self.parent_tree_index = tl_dict.get("parent_tree_index", 0)

        # ── default_values: raw scene placement (creation position) ──────────
        # Stored post-yz-swap for use by apply_default_values_transform (static
        # transform for objects without keyframes) and store_mi_placement
        # (informational custom props).
        raw_dv = dict(tl_dict.get("default_values", {}))
        self.default_values = fix_mi_yz_swap(raw_dv)

        # ── keyframe baseline (three-layer merge) ────────────────────────────
        # MI only writes the *delta* from default_values into each keyframe.
        # An empty keyframe dict ({}) means the frame is identical to
        # default_values, NOT to MI_HARD_DEFAULTS (all-zeros).
        #
        # Effective baseline = MI_HARD_DEFAULTS ← default_values
        # Per-keyframe data  = baseline ← authored per-frame delta
        #
        # _build_keyframe_baseline already applies fix_mi_yz_swap to the raw
        # default_values, so kf_baseline is fully in corrected coordinate space.
        kf_baseline = _build_keyframe_baseline(raw_dv)

        # ── keyframes ────────────────────────────────────────────────────────
        raw_kf = tl_dict.get("keyframes", {})
        self.keyframes = {}
        for frame_str, vals in raw_kf.items():
            # Correct the MI Y/Z coordinate swap bug on the per-frame delta
            swapped = fix_mi_yz_swap(dict(vals))
            # Merge: baseline (hard defaults + default_values) ← per-frame delta
            merged = dict(kf_baseline)   # bottom: engine fallbacks + timeline placement
            merged.update(swapped)        # top:    authored per-frame overrides
            self.keyframes[int(frame_str)] = merged

        # ── inherit flags ────────────────────────────────────────────────────
        self.inherit = tl_dict.get("inherit", {})

        # ── rot_point ────────────────────────────────────────────────────────
        # MI always saves this array regardless of rot_point_custom.
        # Default [0, -8, 0] = bottom-center of a 16-unit shape.
        self.rot_point = list(tl_dict.get("rot_point", [0.0, -8.0, 0.0]))

        # ── Top-level static appearance properties ───────────────────────────
        # These are NOT in keyframes — they are fixed per-object settings.
        self.backfaces = tl_dict.get("backfaces", False)
        self.shadows = tl_dict.get("shadows", True)
        self.ssao = tl_dict.get("ssao", True)
        self.glow = tl_dict.get("glow", False)
        self.glow_texture = tl_dict.get("glow_texture", False)
        self.only_render_glow = tl_dict.get("only_render_glow", False)
        self.glint_mode = tl_dict.get("glint_mode", 0)
        self.fog = tl_dict.get("fog", True)
        self.wind = tl_dict.get("wind", False)
        self.blend_mode = tl_dict.get("blend_mode", "normal")
        self.alpha_mode = tl_dict.get("alpha_mode", 0)

        # ── Path-specific settings (only for type=="path") ───────────────────
        # Raw "path" sub-object from JSON: smooth, closed, detail, etc.
        raw_path = tl_dict.get("path", None)
        if raw_path is not None:
            self.path_data = {
                "smooth":  raw_path.get("smooth",  True),
                "closed":  raw_path.get("closed",  False),
                "detail":  raw_path.get("detail",  6),
                "shape_generate": raw_path.get("shape_generate", False),
                "shape_radius":   raw_path.get("shape_radius",   8),
            }
        else:
            self.path_data = None

        # Filled in during tree building
        self.children = []

    @property
    def display_name(self):
        """Return a human-readable name for the node."""
        if self.name:
            return self.name
        # Capitalise type as fallback
        return self.type.capitalize()

    def __repr__(self):
        return f"MINode(id={self.id!r}, type={self.type!r}, name={self.display_name!r}, children={len(self.children)})"


# ---- Tree Builder ---------------------------------------------------------

def parse_miobject(filepath_or_data):
    """
    Parse a .miobject file (path or already-loaded dict) and return
    a list of root MINode trees.

    Parameters
    ----------
    filepath_or_data : str | dict
        Either a file path to a .miobject JSON file, or the already-parsed
        dict from json.load().

    Returns
    -------
    roots : list[MINode]
        Top-level nodes whose parent is "root".
    all_nodes : dict[str, MINode]
        Flat lookup of id → MINode for every *supported* timeline entry.
    meta : dict
        File-level metadata (format, created_in, tempo, …).
    """
    if isinstance(filepath_or_data, str):
        with open(filepath_or_data, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        data = filepath_or_data

    meta = {
        "format": data.get("format", 34),
        "created_in": data.get("created_in", ""),
    }

    raw_templates = data.get("templates", [])
    templates_dict = {t.get("id"): t for t in raw_templates}

    timelines = data.get("timelines", [])

    # Build flat node dict (only supported types)
    all_nodes = {}
    for tl in timelines:
        tl_type = tl.get("type", "")
        if tl_type not in SUPPORTED_TYPES:
            continue
        node = MINode(tl, templates_dict=templates_dict)
        all_nodes[node.id] = node

    # Link children to parents (sort by parent_tree_index)
    roots = []
    for node in all_nodes.values():
        pid = node.parent_id
        if pid == "root" or pid not in all_nodes:
            roots.append(node)
        else:
            all_nodes[pid].children.append(node)

    # Sort children by parent_tree_index
    for node in all_nodes.values():
        node.children.sort(key=lambda n: n.parent_tree_index)

    roots.sort(key=lambda n: n.parent_tree_index)

    return roots, all_nodes, meta

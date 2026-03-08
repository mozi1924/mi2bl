"""
Parser for .miobject files.

Produces a tree of MINode objects that mirror the Mine-Imator timeline
hierarchy. Each node carries its type, default_values, keyframes, and
references to its children (ordered by parent_tree_index).

Supported timeline types for scene import:
  - "folder"  →  Blender Empty
  - "cube"    →  Blender MI-style Cube mesh
  - "surface" →  Blender MI-style Surface (upright plane) mesh

The parser intentionally ignores types that are not yet supported
(e.g. "char", "camera", "audio", "bodypart", etc.).
"""

import json
import math

# ---- MI Bug Fix (Y/Z swap) ------------------------------------------------

def fix_mi_yz_swap(values):
    """
    Mine-Imator has a known bug where UI Y (Up) and UI Z (Depth)
    are swapped in the saved JSON.  This restores the true UI values.
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


def _fill_defaults(values):
    """Fill missing POS/ROT/SCA keys with MI defaults (POS=0, ROT=0, SCA=1)."""
    for k in ("POS_X", "POS_Y", "POS_Z", "ROT_X", "ROT_Y", "ROT_Z"):
        values.setdefault(k, 0.0)
    for k in ("SCA_X", "SCA_Y", "SCA_Z"):
        values.setdefault(k, 1.0)
    values.setdefault("TRANSITION", "linear")
    return values


# ---- Data Classes ----------------------------------------------------------

SUPPORTED_TYPES = {"folder", "cube", "surface", "block", "char", "camera", "audio", "bodypart", "text", "scenery", "item", "particle_spawner", "spotlight", "pointlight"}


class MINode:
    """A single timeline entry from a .miobject file."""

    __slots__ = (
        "id", "type", "name", "temp",
        "parent_id", "parent_tree_index",
        "default_values", "keyframes",
        "inherit",
        "children",
        "rot_point",
        "template_data"
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

        # default_values = scene placement offset (initial position)
        raw_dv = dict(tl_dict.get("default_values", {}))
        self.default_values = fix_mi_yz_swap(raw_dv)

        # keyframes = {frame_str: {prop: value, ...}}
        raw_kf = tl_dict.get("keyframes", {})
        self.keyframes = {}
        for frame_str, vals in raw_kf.items():
            fixed = _fill_defaults(dict(vals))
            self.keyframes[int(frame_str)] = fix_mi_yz_swap(fixed)

        # inherit flags (position, rotation, scale, etc.)
        self.inherit = tl_dict.get("inherit", {})

        # rot_point: offset from geometric center in MI local space.
        # MI always saves this array regardless of rot_point_custom.
        # Default [0, -8, 0] = bottom-center of a 16-unit shape.
        self.rot_point = list(tl_dict.get("rot_point", [0.0, -8.0, 0.0]))

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

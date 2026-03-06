"""
Bridge module that re-exports the existing miframes package so that
the new src package can reach it with clean imports.
"""
import importlib
import sys
import os

# Ensure the parent directory (mi2bl root) is on sys.path so that
# ``import miframes`` works even when Blender loads the addon from
# a symlinked path.
_parent = os.path.dirname(os.path.dirname(__file__))
if _parent not in sys.path:
    sys.path.insert(0, _parent)

from miframes import configs  # noqa: F401, E402
from miframes import importer  # noqa: F401, E402
from miframes import object_importer  # noqa: F401, E402
from miframes import object_panel  # noqa: F401, E402

bl_info = {
    "name": "MI2BL - Mine-Imator to Blender",
    "author": "mi2bl",
    "version": (0, 1, 0),
    "blender": (3, 6, 0),
    "location": "File > Import > Mine-Imator Object (.miobject)",
    "description": "Import Mine-Imator .miobject scenes (folders, cubes, surfaces) into Blender",
    "category": "Import-Export",
}

from . import src


def register():
    src.register()


def unregister():
    src.unregister()

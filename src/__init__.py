from . import mesh_gen
from . import miobject_parser
from . import scene_importer
from .miframes_bridge import configs, importer, object_importer, object_panel


def register():
    scene_importer.register()
    object_panel.register()


def unregister():
    scene_importer.unregister()
    object_panel.unregister()

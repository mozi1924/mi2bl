from . import mesh_gen
from . import miobject_parser
from . import scene_importer
from . import object_importer
from . import object_panel
from . import core


def register():
    scene_importer.register()
    object_importer.register()
    object_panel.register()


def unregister():
    scene_importer.unregister()
    object_importer.unregister()
    object_panel.unregister()

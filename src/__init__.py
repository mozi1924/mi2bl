from .importers import scene_importer, object_importer
from .ui import object_panel

def register():
    scene_importer.register()
    object_importer.register()
    object_panel.register()

def unregister():
    scene_importer.unregister()
    object_importer.unregister()
    object_panel.unregister()

"""GUI layer: view providers, commands and task panels. Only imported when
FreeCAD runs with its GUI."""

import os

import rom_geometry


ICON_DIRECTORY = os.path.join(os.path.dirname(rom_geometry.__file__), "resources", "icons")


def icon_path(icon_name):
    return os.path.join(ICON_DIRECTORY, icon_name)

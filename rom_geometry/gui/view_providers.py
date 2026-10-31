"""View providers: icons, the coloured semi-transparent region preview, and
double-click-to-edit for regions."""

import FreeCAD as App
import FreeCADGui as Gui

from rom_geometry.gui import icon_path
from rom_geometry.objects.object_helpers import object_type


REGION_PREVIEW_TRANSPARENCY = 55


def set_shape_color(view_object, color_rgb):
    """FreeCAD 1.x stores colour in ShapeAppearance (a material); older
    versions use ShapeColor."""
    if hasattr(view_object, "ShapeAppearance"):
        material = view_object.ShapeAppearance[0]
        material.DiffuseColor = tuple(color_rgb[:3])
        view_object.ShapeAppearance = (material,)
    else:
        view_object.ShapeColor = tuple(color_rgb[:3])


class ROMBaseViewProvider:
    icon_name = "rom_region.svg"

    def __init__(self, view_object):
        view_object.Proxy = self

    def attach(self, view_object):
        self.Object = view_object.Object

    def getIcon(self):
        return icon_path(self.icon_name)

    def updateData(self, obj, property_name):
        pass

    def onChanged(self, view_object, property_name):
        pass

    def dumps(self):
        return None

    def loads(self, state):
        return None


class ROMModelViewProvider(ROMBaseViewProvider):
    icon_name = "rom_model.svg"


class ROMRegionViewProvider(ROMBaseViewProvider):
    """Region = compound of its cells, drawn in the region colour,
    semi-transparent, with the cell boundaries visible as edges."""
    icon_name = "rom_region.svg"

    def attach(self, view_object):
        self.Object = view_object.Object
        view_object.Transparency = REGION_PREVIEW_TRANSPARENCY
        set_shape_color(view_object, view_object.Object.Color)

    def updateData(self, obj, property_name):
        if property_name == "Color" and obj.ViewObject is not None:
            set_shape_color(obj.ViewObject, obj.Color)

    def doubleClicked(self, view_object):
        view_object.Document.setEdit(view_object.Object.Name, 0)
        return True

    def setEdit(self, view_object, mode):
        if mode != 0:
            return False
        from rom_geometry.gui.region_task_panel import RegionTaskPanel
        region_object = view_object.Object
        region_object.Document.openTransaction(f"Edit region {region_object.Label}")
        Gui.Control.showDialog(RegionTaskPanel(region_object))
        return True

    def unsetEdit(self, view_object, mode):
        Gui.Control.closeDialog()
        return True


class ROMCavityViewProvider(ROMBaseViewProvider):
    icon_name = "rom_cavity.svg"

    def attach(self, view_object):
        self.Object = view_object.Object
        view_object.Transparency = 70
        set_shape_color(view_object, (0.75, 0.9, 1.0))


class ROMFastenerPartViewProvider(ROMBaseViewProvider):
    icon_name = "rom_fastener.svg"


VIEW_PROVIDER_BY_TYPE = {
    "ROMModel": ROMModelViewProvider,
    "ROMRegion": ROMRegionViewProvider,
    "ROMCavity": ROMCavityViewProvider,
    "ROMFastenerPart": ROMFastenerPartViewProvider,
}


def attach_view_provider(document_object):
    if not App.GuiUp or document_object.ViewObject is None:
        return
    view_provider_class = VIEW_PROVIDER_BY_TYPE.get(object_type(document_object))
    if view_provider_class is not None:
        view_provider_class(document_object.ViewObject)

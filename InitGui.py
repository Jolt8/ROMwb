import FreeCADGui as Gui


class ROMGeometryWorkbench(Gui.Workbench):
    MenuText = "ROM Geometry"
    ToolTip = "Define lumped regions on CAD solids and export cells, interfaces and conduction distances for FVMFramework"

    def __init__(self):
        import os
        import rom_geometry
        self.__class__.Icon = os.path.join(os.path.dirname(rom_geometry.__file__), "resources", "icons", "rom_geometry_workbench.svg")

    def Initialize(self):
        from rom_geometry.gui import commands
        self.appendToolbar("ROM Geometry", commands.TOOLBAR_COMMANDS)
        self.appendMenu("ROM Geometry", commands.TOOLBAR_COMMANDS)

    def Activated(self):
        pass

    def Deactivated(self):
        pass

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(ROMGeometryWorkbench())

"""Toolbar / menu commands of the ROMGeometry workbench.

Where objects go: the ROM model normally lives in its own document that
links into the assembly (the assembly file is never modified). Commands use
the ROMModel of the active document; if the active document has none (e.g.
you just clicked in the assembly's 3D view) and exactly one ROMModel is open
anywhere, that one is used.
"""

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

from rom_geometry.core.export_json import ExportRefusedError
from rom_geometry.gui import icon_path
from rom_geometry.gui.view_providers import attach_view_provider
from rom_geometry.objects.object_helpers import object_type
from rom_geometry.objects.rom_model import create_rom_model, engine_axis_of_model, export_rom_model, find_rom_models, validate_rom_model
from rom_geometry.objects.rom_region import create_rom_region
from rom_geometry.objects.source_features import create_cavity, create_fastener_parts


def find_target_model(create_if_missing):
    active_document = App.ActiveDocument
    if active_document is not None:
        active_document_models = find_rom_models(active_document)
        if len(active_document_models) >= 1:
            return active_document_models[0]

    all_open_models = []
    for open_document in App.listDocuments().values():
        all_open_models.extend(find_rom_models(open_document))
    if len(all_open_models) == 1:
        return all_open_models[0]
    if len(all_open_models) > 1:
        QtGui.QMessageBox.warning(Gui.getMainWindow(), "ROM Geometry", "Several ROM models are open. Activate the document of the one you want.")
        return None

    if not create_if_missing or active_document is None:
        return None

    model_object = create_rom_model(active_document)
    attach_view_provider(model_object)
    return model_object


def show_text_report(window_title, report_text):
    report_dialog = QtGui.QDialog(Gui.getMainWindow())
    report_dialog.setWindowTitle(window_title)
    report_dialog.resize(900, 500)
    dialog_layout = QtGui.QVBoxLayout(report_dialog)
    report_view = QtGui.QPlainTextEdit(report_text)
    report_view.setReadOnly(True)
    report_view.setLineWrapMode(QtGui.QPlainTextEdit.NoWrap)
    dialog_layout.addWidget(report_view)
    close_button = QtGui.QPushButton("Close")
    close_button.clicked.connect(report_dialog.accept)
    dialog_layout.addWidget(close_button)
    report_dialog.exec_()


def selected_shape_objects():
    """Selected objects in any open document that have a shape."""
    return [selected_object for selected_object in Gui.Selection.getSelection("*") if hasattr(selected_object, "Shape")]


class CreateROMModelCommand:
    def GetResources(self):
        return {"Pixmap": icon_path("rom_model.svg"), "MenuText": "Create ROM Model", "ToolTip": "Create a ROM model (region group) in the active document. Use a separate, saved document that links to the assembly."}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        doc = App.ActiveDocument
        doc.openTransaction("Create ROM model")
        model_object = create_rom_model(doc)
        attach_view_provider(model_object)
        doc.commitTransaction()
        doc.recompute()
        if not doc.FileName:
            App.Console.PrintWarning("ROM Geometry: save this document before binding z-limits to another document's spreadsheet (cross-document expressions need a saved file).\n")


class AddRegionCommand:
    def GetResources(self):
        return {"Pixmap": icon_path("rom_region.svg"), "MenuText": "Add Region", "ToolTip": "Add a region from the selected solid(s): fused, cut to a z (and r) range, split into axial cells."}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        model_object = find_target_model(create_if_missing=True)
        if model_object is None:
            return
        doc = model_object.Document
        source_objects = [source_object for source_object in selected_shape_objects() if object_type(source_object) != "ROMRegion"]

        existing_region_count = len([child for child in model_object.Group if object_type(child) == "ROMRegion"])
        if len(source_objects) == 1:
            default_region_name = source_objects[0].Label
        else:
            default_region_name = f"region_{existing_region_count + 1}"

        doc.openTransaction("Add ROM region")
        region_object = create_rom_region(model_object, default_region_name, source_objects)
        attach_view_provider(region_object)
        doc.commitTransaction()
        doc.recompute()

        Gui.Selection.clearSelection()
        Gui.getDocument(doc.Name).setEdit(region_object.Name, 0)


class MakeCavityCommand:
    def GetResources(self):
        return {"Pixmap": icon_path("rom_cavity.svg"), "MenuText": "Make Cavity Solid", "ToolTip": "Fluid volume: an axial cylinder around the selected solids, minus those solids. Set Enclosure to use your own enclosing solid instead."}

    def IsActive(self):
        return len(selected_shape_objects()) > 0

    def Activated(self):
        subtracted_objects = selected_shape_objects()
        model_object = find_target_model(create_if_missing=False)
        if model_object is not None:
            doc = model_object.Document
            engine_axis = engine_axis_of_model(model_object)
        else:
            doc = App.ActiveDocument
            engine_axis = None

        doc.openTransaction("Make cavity solid")
        cavity_object = create_cavity(doc, subtracted_objects, label="cavity", engine_axis=engine_axis)
        if model_object is not None and model_object.Axis is not None:
            cavity_object.Axis = model_object.Axis
        attach_view_provider(cavity_object)
        doc.commitTransaction()
        doc.recompute()
        App.Console.PrintMessage(f"ROM Geometry: {cavity_object.Label}: {cavity_object.Status}\n")


class SplitFastenerCommand:
    def GetResources(self):
        return {"Pixmap": icon_path("rom_fastener.svg"), "MenuText": "Split Fastener Between Bodies", "ToolTip": "Select the fastener, then body A, then body B. Makes two halves, cut on the bodies' contact plane, to add to each body's region."}

    def IsActive(self):
        return len(selected_shape_objects()) == 3

    def Activated(self):
        fastener_object, body_a_object, body_b_object = selected_shape_objects()
        model_object = find_target_model(create_if_missing=False)
        doc = model_object.Document if model_object is not None else App.ActiveDocument

        doc.openTransaction("Split fastener")
        fastener_part_a, fastener_part_b = create_fastener_parts(doc, fastener_object, body_a_object, body_b_object)
        attach_view_provider(fastener_part_a)
        attach_view_provider(fastener_part_b)
        doc.commitTransaction()
        doc.recompute()
        for fastener_part in (fastener_part_a, fastener_part_b):
            App.Console.PrintMessage(f"ROM Geometry: {fastener_part.Label}: {fastener_part.Status}\n")


class ValidateCommand:
    def GetResources(self):
        return {"Pixmap": icon_path("rom_validate.svg"), "MenuText": "Validate", "ToolTip": "Build all cells and interfaces and run every check."}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        model_object = find_target_model(create_if_missing=False)
        if model_object is None:
            QtGui.QMessageBox.information(Gui.getMainWindow(), "ROM Geometry", "No ROM model found.")
            return
        QtGui.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            build, report = validate_rom_model(model_object)
        finally:
            QtGui.QApplication.restoreOverrideCursor()
        summary_text = f"{len(build.cells)} cells, {len(build.facetsets)} face sets\n\n"
        show_text_report(f"Validate {model_object.Label}", summary_text + report.as_text())


class ExportCommand:
    def GetResources(self):
        return {"Pixmap": icon_path("rom_export.svg"), "MenuText": "Export JSON", "ToolTip": "Validate, then write the ROM geometry JSON for FVMFramework. Refused if there are errors."}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        model_object = find_target_model(create_if_missing=False)
        if model_object is None:
            QtGui.QMessageBox.information(Gui.getMainWindow(), "ROM Geometry", "No ROM model found.")
            return

        if not model_object.ExportPath:
            chosen_path, chosen_filter = QtGui.QFileDialog.getSaveFileName(Gui.getMainWindow(), "Export ROM geometry", "rom_geometry.json", "JSON (*.json)")
            if not chosen_path:
                return
            model_object.ExportPath = chosen_path

        try:
            build, report, export = export_rom_model(model_object)
        except ExportRefusedError as refusal:
            show_text_report("Export refused", "Nothing was written.\n\n" + refusal.report.as_text())
            return

        show_text_report("Export written", f"Wrote {model_object.ExportPath}\n{len(export['cells'])} cells, {len(export['facetsets'])} face sets\n\n" + report.as_text())


TOOLBAR_COMMANDS = [
    "ROMGeometry_CreateModel",
    "ROMGeometry_AddRegion",
    "ROMGeometry_MakeCavity",
    "ROMGeometry_SplitFastener",
    "ROMGeometry_Validate",
    "ROMGeometry_Export",
]

Gui.addCommand("ROMGeometry_CreateModel", CreateROMModelCommand())
Gui.addCommand("ROMGeometry_AddRegion", AddRegionCommand())
Gui.addCommand("ROMGeometry_MakeCavity", MakeCavityCommand())
Gui.addCommand("ROMGeometry_SplitFastener", SplitFastenerCommand())
Gui.addCommand("ROMGeometry_Validate", ValidateCommand())
Gui.addCommand("ROMGeometry_Export", ExportCommand())

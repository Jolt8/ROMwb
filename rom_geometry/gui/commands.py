"""Toolbar / menu commands of the ROMGeometry workbench.

Where objects go: the ROM model normally lives in its own document that
links into the assembly (the assembly file is never modified). Commands use
the ROMModel of the active document; if the active document has none (e.g.
you just clicked in the assembly's 3D view) and exactly one ROMModel is open
anywhere, that one is used.
"""

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtGui

from rom_geometry.core.export_json import ExportRefusedError
from rom_geometry.gui import icon_path
from rom_geometry.gui.progress import BuildProgressDialog
from rom_geometry.gui.view_providers import attach_view_provider
from rom_geometry.objects.object_helpers import object_type
from rom_geometry.objects.rom_model import create_rom_model, export_rom_model, find_rom_models, validate_rom_model
from rom_geometry.objects.rom_boundary_patch import create_boundary_patch
from rom_geometry.objects.rom_region import create_rom_region
from rom_geometry.objects.source_features import create_fastener_parts


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
        return {"Pixmap": icon_path("rom_region.svg"), "MenuText": "Add Region", "ToolTip": "Add a region from the selected solid(s): fused, cut to a z (and r) range, split into axial cells. Tick 'Inverse' in the panel for a fluid region (the empty space around the selected solids)."}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        model_object = find_target_model(create_if_missing=True)
        if model_object is None:
            return
        doc = model_object.Document
        source_objects = [source_object for source_object in selected_shape_objects() if object_type(source_object) != "ROMRegion"]

        doc.openTransaction("Add ROM region")
        region_object = create_rom_region(model_object, None, source_objects)
        attach_view_provider(region_object)
        doc.commitTransaction()
        doc.recompute()

        Gui.Selection.clearSelection()
        Gui.getDocument(doc.Name).setEdit(region_object.Name, 0)


class SplitFastenerCommand:
    def GetResources(self):
        return {"Pixmap": icon_path("rom_fastener.svg"), "MenuText": "Split Fastener Between Bodies", "ToolTip": "Select the fastener, then body A, then body B. Makes two halves in the ROM model, cut on the bodies' contact plane, to add to each body's region."}

    def IsActive(self):
        return len(selected_shape_objects()) == 3

    def Activated(self):
        fastener_object, body_a_object, body_b_object = selected_shape_objects()
        model_object = find_target_model(create_if_missing=True)
        if model_object is None:
            return
        doc = model_object.Document

        doc.openTransaction("Split fastener")
        fastener_part_a, fastener_part_b = create_fastener_parts(doc, fastener_object, body_a_object, body_b_object, model_object)
        attach_view_provider(fastener_part_a)
        attach_view_provider(fastener_part_b)
        doc.commitTransaction()
        doc.recompute()
        for fastener_part in (fastener_part_a, fastener_part_b):
            App.Console.PrintMessage(f"ROM Geometry: {fastener_part.Label}: {fastener_part.Status}\n")


class AddBoundaryPatchCommand:
    def GetResources(self):
        return {"Pixmap": icon_path("rom_patch.svg"), "MenuText": "Add Boundary Patch", "ToolTip": "Name the selected CAD faces as a boundary patch (e.g. flat_plate_ambient_heat_exchange). Select the faces in the assembly's 3D view first."}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        from rom_geometry.gui.boundary_patch_task_panel import selected_faces

        picked_faces = selected_faces()
        model_object = find_target_model(create_if_missing=True)
        if model_object is None:
            return
        doc = model_object.Document

        doc.openTransaction("Add boundary patch")
        patch_object = create_boundary_patch(model_object, None, picked_faces)
        attach_view_provider(patch_object)
        doc.commitTransaction()
        doc.recompute()

        Gui.Selection.clearSelection()
        Gui.getDocument(doc.Name).setEdit(patch_object.Name, 0)


class ReviewInterfacesCommand:
    def GetResources(self):
        return {"Pixmap": icon_path("rom_review.svg"), "MenuText": "Review Interfaces", "ToolTip": "List every interface between regions (with area and an Ignore box), the near-contacts and the unassigned outer surfaces. Click a row to highlight it."}

    def IsActive(self):
        return App.ActiveDocument is not None

    def Activated(self):
        from rom_geometry.gui.interface_review_panel import InterfaceReviewPanel

        model_object = find_target_model(create_if_missing=False)
        if model_object is None:
            QtGui.QMessageBox.information(Gui.getMainWindow(), "ROM Geometry", "No ROM model found.")
            return
        with BuildProgressDialog("Building interfaces") as progress:
            review_panel = InterfaceReviewPanel(model_object, progress.update)
        Gui.Control.showDialog(review_panel)


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
        with BuildProgressDialog("Validating") as progress:
            build, report = validate_rom_model(model_object, progress.update)
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
            with BuildProgressDialog("Exporting") as progress:
                build, report, export = export_rom_model(model_object, progress_callback=progress.update)
        except ExportRefusedError as refusal:
            show_text_report("Export refused", "Nothing was written.\n\n" + refusal.report.as_text())
            return

        show_text_report("Export written", f"Wrote {model_object.ExportPath}\n{len(export['cells'])} cells, {len(export['facetsets'])} face sets\n\n" + report.as_text())


TOOLBAR_COMMANDS = [
    "ROMGeometry_CreateModel",
    "ROMGeometry_AddRegion",
    "ROMGeometry_SplitFastener",
    "ROMGeometry_AddBoundaryPatch",
    "ROMGeometry_ReviewInterfaces",
    "ROMGeometry_Validate",
    "ROMGeometry_Export",
]

Gui.addCommand("ROMGeometry_CreateModel", CreateROMModelCommand())
Gui.addCommand("ROMGeometry_AddRegion", AddRegionCommand())
Gui.addCommand("ROMGeometry_SplitFastener", SplitFastenerCommand())
Gui.addCommand("ROMGeometry_AddBoundaryPatch", AddBoundaryPatchCommand())
Gui.addCommand("ROMGeometry_ReviewInterfaces", ReviewInterfacesCommand())
Gui.addCommand("ROMGeometry_Validate", ValidateCommand())
Gui.addCommand("ROMGeometry_Export", ExportCommand())

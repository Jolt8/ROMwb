"""Task panel for a boundary patch: name, colour, and the picked faces.

Select faces in the 3D view (in the assembly, through its links), then
"Add selected faces". "Re-pick" replaces the highlighted face with the
selected one; use it when a CAD edit made a face impossible to re-match.
"""

import FreeCADGui as Gui
from PySide import QtGui

from rom_geometry.objects.rom_boundary_patch import add_faces_to_patch, entry_description, read_patch_entries, remove_patch_faces, replace_patch_face
from rom_geometry.objects.object_helpers import split_element_path


def selected_faces():
    """(top_object, sub_path) for every selected face, keeping the full link
    path (resolve=0) so faces under an assembly link are placed correctly."""
    picked_faces = []
    for selection_entry in Gui.Selection.getSelectionEx("*", 0):
        for sub_path in selection_entry.SubElementNames:
            object_path, element_name = split_element_path(sub_path)
            if element_name.startswith("Face"):
                picked_faces.append((selection_entry.Object, sub_path))
    return picked_faces


class BoundaryPatchTaskPanel:

    def __init__(self, patch_object):
        self.patch_object = patch_object
        self.form = QtGui.QWidget()
        self.form.setWindowTitle(f"ROM boundary patch: {patch_object.Label}")
        form_layout = QtGui.QFormLayout(self.form)

        self.patch_name_edit = QtGui.QLineEdit(patch_object.PatchName)
        self.patch_name_edit.setPlaceholderText("e.g. flat_plate_ambient_heat_exchange")
        form_layout.addRow("Patch name", self.patch_name_edit)

        self.color_button = QtGui.QPushButton()
        self.chosen_color = tuple(patch_object.Color[:3])
        self._show_color()
        self.color_button.clicked.connect(self._choose_color)
        form_layout.addRow("Preview colour", self.color_button)

        self.face_list = QtGui.QListWidget()
        self.face_list.setSelectionMode(QtGui.QAbstractItemView.ExtendedSelection)
        form_layout.addRow("Faces", self.face_list)

        add_button = QtGui.QPushButton("Add selected faces")
        remove_button = QtGui.QPushButton("Remove highlighted")
        repick_button = QtGui.QPushButton("Re-pick highlighted")
        repick_button.setToolTip("Replace the highlighted face with the face selected in the 3D view")
        add_button.clicked.connect(self._add_selected_faces)
        remove_button.clicked.connect(self._remove_highlighted_faces)
        repick_button.clicked.connect(self._repick_highlighted_face)
        button_row = QtGui.QHBoxLayout()
        button_row.addWidget(add_button)
        button_row.addWidget(remove_button)
        button_row.addWidget(repick_button)
        form_layout.addRow(button_row)

        self.status_label = QtGui.QLabel()
        self.status_label.setWordWrap(True)
        form_layout.addRow(self.status_label)

        self._refresh()

    def _refresh(self):
        self.face_list.clear()
        for patch_entry in read_patch_entries(self.patch_object):
            fingerprint = patch_entry["fingerprint"]
            self.face_list.addItem(f"{entry_description(patch_entry)}   ({fingerprint['surface_type']}, {fingerprint['area']:.4g} mm^2)")
        self.status_label.setText(self.patch_object.Status)

    def _recompute(self):
        self.patch_object.touch()
        self.patch_object.Document.recompute()
        self._refresh()

    def _show_color(self):
        red, green, blue = (int(255 * component) for component in self.chosen_color)
        self.color_button.setStyleSheet(f"background-color: rgb({red}, {green}, {blue});")

    def _choose_color(self):
        red, green, blue = (int(255 * component) for component in self.chosen_color)
        picked_color = QtGui.QColorDialog.getColor(QtGui.QColor(red, green, blue), self.form)
        if picked_color.isValid():
            self.chosen_color = (picked_color.redF(), picked_color.greenF(), picked_color.blueF())
            self._show_color()

    def _add_selected_faces(self):
        picked_faces = selected_faces()
        if len(picked_faces) == 0:
            QtGui.QMessageBox.information(self.form, "ROM patch", "Select one or more faces in the 3D view first.")
            return
        add_faces_to_patch(self.patch_object, picked_faces)
        Gui.Selection.clearSelection()
        self._recompute()

    def _remove_highlighted_faces(self):
        highlighted_rows = [self.face_list.row(item) for item in self.face_list.selectedItems()]
        if highlighted_rows:
            remove_patch_faces(self.patch_object, highlighted_rows)
            self._recompute()

    def _repick_highlighted_face(self):
        highlighted_rows = [self.face_list.row(item) for item in self.face_list.selectedItems()]
        picked_faces = selected_faces()
        if len(highlighted_rows) != 1 or len(picked_faces) != 1:
            QtGui.QMessageBox.information(self.form, "ROM patch", "Highlight exactly one face in the list and select exactly one face in the 3D view.")
            return
        top_object, sub_path = picked_faces[0]
        replace_patch_face(self.patch_object, highlighted_rows[0], top_object, sub_path)
        Gui.Selection.clearSelection()
        self._recompute()

    def getStandardButtons(self):
        return QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel

    def accept(self):
        patch_name = self.patch_name_edit.text().strip()
        if "__" in patch_name:
            QtGui.QMessageBox.warning(self.form, "ROM patch", "Patch names must not contain '__' (reserved for interface face sets).")
            return False
        self.patch_object.PatchName = patch_name
        if patch_name:
            self.patch_object.Label = patch_name
        self.patch_object.Color = self.chosen_color
        self.patch_object.Document.recompute()
        self.patch_object.Document.commitTransaction()
        Gui.getDocument(self.patch_object.Document.Name).resetEdit()
        Gui.Control.closeDialog()
        return True

    def reject(self):
        self.patch_object.Document.abortTransaction()
        self.patch_object.Document.recompute()
        Gui.getDocument(self.patch_object.Document.Name).resetEdit()
        Gui.Control.closeDialog()
        return True

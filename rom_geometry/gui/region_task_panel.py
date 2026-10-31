"""Task panel for creating / editing a region.

Each limit has a value box and an expression box. A non-empty expression
wins and is bound to the property, so it follows the spreadsheet, e.g.
    table_hybrid#Spreadsheet.pre_combustion_chamber_length
(cross-document expressions need the ROM document to have been saved once).
"""

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

from rom_geometry.objects.object_helpers import property_expression


LIMIT_PROPERTIES = (
    ("ZMin", "Z min"),
    ("ZMax", "Z max"),
    ("RMin", "R min (0 = none)"),
    ("RMax", "R max (0 = none)"),
)


class RegionTaskPanel:

    def __init__(self, region_object):
        self.region_object = region_object
        self.form = QtGui.QWidget()
        self.form.setWindowTitle(f"ROM region: {region_object.Label}")
        form_layout = QtGui.QFormLayout(self.form)

        self.region_name_edit = QtGui.QLineEdit(region_object.RegionName)
        self.material_edit = QtGui.QLineEdit(region_object.Material)
        self.material_edit.setPlaceholderText("e.g. aluminum_6061")
        form_layout.addRow("Region name", self.region_name_edit)
        form_layout.addRow("Material", self.material_edit)

        # Sources: the fused bodies / cavity / fastener halves.
        self.source_list = QtGui.QListWidget()
        self.source_objects = list(region_object.SourceShapes)
        self._refresh_source_list()
        add_selected_button = QtGui.QPushButton("Add selected")
        remove_button = QtGui.QPushButton("Remove highlighted")
        add_selected_button.clicked.connect(self._add_selected_sources)
        remove_button.clicked.connect(self._remove_highlighted_sources)
        source_buttons = QtGui.QHBoxLayout()
        source_buttons.addWidget(add_selected_button)
        source_buttons.addWidget(remove_button)
        source_box = QtGui.QVBoxLayout()
        source_box.addWidget(self.source_list)
        source_box.addLayout(source_buttons)
        form_layout.addRow("Sources (fused)", source_box)

        # Limits: value in mm, or an expression.
        self.limit_value_boxes = {}
        self.limit_expression_edits = {}
        for property_name, row_label in LIMIT_PROPERTIES:
            value_box = QtGui.QDoubleSpinBox()
            value_box.setDecimals(4)
            value_box.setSuffix(" mm")
            if property_name.startswith("Z"):
                value_box.setRange(-1.0e6, 1.0e6)
            else:
                value_box.setRange(0.0, 1.0e6)
            value_box.setValue(getattr(region_object, property_name).Value)

            expression_edit = QtGui.QLineEdit(property_expression(region_object, property_name))
            expression_edit.setPlaceholderText("or expression, e.g. table_hybrid#Spreadsheet.alias")
            value_box.setEnabled(expression_edit.text() == "")
            expression_edit.textChanged.connect(lambda text, box=value_box: box.setEnabled(text.strip() == ""))

            limit_row = QtGui.QHBoxLayout()
            limit_row.addWidget(value_box, 1)
            limit_row.addWidget(expression_edit, 2)
            form_layout.addRow(row_label, limit_row)
            self.limit_value_boxes[property_name] = value_box
            self.limit_expression_edits[property_name] = expression_edit

        self.cell_count_box = QtGui.QSpinBox()
        self.cell_count_box.setRange(1, 10000)
        self.cell_count_box.setValue(region_object.NAxialCells)
        form_layout.addRow("Axial cells", self.cell_count_box)

        self.color_button = QtGui.QPushButton()
        self.chosen_color = tuple(region_object.Color[:3])
        self._show_color()
        self.color_button.clicked.connect(self._choose_color)
        form_layout.addRow("Preview colour", self.color_button)

        preview_button = QtGui.QPushButton("Preview")
        preview_button.clicked.connect(self._preview)
        self.status_label = QtGui.QLabel(region_object.Status)
        self.status_label.setWordWrap(True)
        form_layout.addRow(preview_button, self.status_label)

    # --- helpers --------------------------------------------------------------

    def _refresh_source_list(self):
        self.source_list.clear()
        for source_object in self.source_objects:
            self.source_list.addItem(f"{source_object.Label}  ({source_object.Document.Name})")

    def _add_selected_sources(self):
        for selected_object in Gui.Selection.getSelection("*"):
            if selected_object is self.region_object or selected_object in self.source_objects:
                continue
            self.source_objects.append(selected_object)
        self._refresh_source_list()

    def _remove_highlighted_sources(self):
        highlighted_rows = sorted((self.source_list.row(item) for item in self.source_list.selectedItems()), reverse=True)
        for row in highlighted_rows:
            del self.source_objects[row]
        self._refresh_source_list()

    def _show_color(self):
        red, green, blue = (int(255 * component) for component in self.chosen_color)
        self.color_button.setStyleSheet(f"background-color: rgb({red}, {green}, {blue});")

    def _choose_color(self):
        red, green, blue = (int(255 * component) for component in self.chosen_color)
        picked_color = QtGui.QColorDialog.getColor(QtGui.QColor(red, green, blue), self.form)
        if picked_color.isValid():
            self.chosen_color = (picked_color.redF(), picked_color.greenF(), picked_color.blueF())
            self._show_color()

    def _apply_to_object(self):
        region_object = self.region_object
        region_name = self.region_name_edit.text().strip()
        region_object.RegionName = region_name
        if region_name:
            region_object.Label = region_name
        region_object.Material = self.material_edit.text().strip()
        region_object.SourceShapes = self.source_objects
        region_object.NAxialCells = self.cell_count_box.value()
        region_object.Color = self.chosen_color

        for property_name, row_label in LIMIT_PROPERTIES:
            expression_text = self.limit_expression_edits[property_name].text().strip()
            if expression_text:
                region_object.setExpression(property_name, expression_text)
            else:
                region_object.setExpression(property_name, None)
                setattr(region_object, property_name, self.limit_value_boxes[property_name].value())

        region_object.Document.recompute()

        # Show evaluated expression values back in the value boxes.
        for property_name, row_label in LIMIT_PROPERTIES:
            self.limit_value_boxes[property_name].setValue(getattr(region_object, property_name).Value)
        self.status_label.setText(region_object.Status)

    def _preview(self):
        try:
            self._apply_to_object()
        except Exception as apply_error:
            self.status_label.setText(f"error: {apply_error}")

    # --- task panel protocol ----------------------------------------------------

    def getStandardButtons(self):
        return QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel

    def accept(self):
        try:
            self._apply_to_object()
        except Exception as apply_error:
            QtGui.QMessageBox.warning(self.form, "ROM region", f"Could not apply the region settings:\n{apply_error}")
            return False
        self.region_object.Document.commitTransaction()
        Gui.getDocument(self.region_object.Document.Name).resetEdit()
        Gui.Control.closeDialog()
        return True

    def reject(self):
        self.region_object.Document.abortTransaction()
        self.region_object.Document.recompute()
        Gui.getDocument(self.region_object.Document.Name).resetEdit()
        Gui.Control.closeDialog()
        return True

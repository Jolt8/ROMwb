"""Task panel for creating / editing a region.

Tick "Inverse" for a fluid region: the region is then the empty space inside
the limits that the selected solids do not fill.

Each limit (Z min, Z max, R min, R max) can be set three ways:
  - a value typed in the box;
  - an expression: press "=" in the box (or click its f(x) icon) to open
    FreeCAD's expression editor, with autocomplete for objects, spreadsheet
    aliases and other documents (`table_hybrid#Spreadsheet.<alias>`);
  - a reference: select an LCS (e.g. an Assembly4 LCS under the assembly's
    link), a vertex, a planar face (z) or a coaxial cylindrical face (r) in
    the 3D view or tree, then click "Pick". The offset box adds to it.
A reference wins over a value or an expression.

Edits are applied to the object as you type and the region recomputes
after a short pause. Cancel undoes everything done in the panel.
"""

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui

from rom_geometry.objects.object_helpers import property_expression


# Max above min: the larger number sits on top, like the geometry.
LIMIT_ROWS = (
    ("ZMax", "Z max", True),
    ("ZMin", "Z min", True),
    ("RMax", "R max (0 = none)", False),
    ("RMin", "R min (0 = none)", False),
)

RECOMPUTE_DELAY_MILLISECONDS = 400


def make_quantity_box(document_object, property_name, allow_negative):
    """FreeCAD's own QuantitySpinBox bound to a property: shows units, has
    the f(x) button, and opens the expression editor (with autocomplete) on
    "=". Expressions entered there go straight onto the object."""
    quantity_box = Gui.UiLoader().createWidget("Gui::QuantitySpinBox")
    quantity_box.setProperty("unit", "mm")
    quantity_box.setProperty("minimum", -1.0e9 if allow_negative else 0.0)
    quantity_box.setProperty("maximum", 1.0e9)
    quantity_box.setProperty("rawValue", getattr(document_object, property_name).Value)
    expression_binding = Gui.ExpressionBinding(quantity_box)
    expression_binding.bind(document_object, property_name)
    return quantity_box, expression_binding


def reference_text(reference_value):
    if reference_value is None or reference_value[0] is None:
        return "(none)"
    top_object, sub_names = reference_value
    sub_name = sub_names[0] if sub_names else ""
    return f"{top_object.Label}.{sub_name}".rstrip(".")


class RegionTaskPanel:

    def __init__(self, region_object):
        self.region_object = region_object
        self.expression_bindings = []
        self.form = QtGui.QWidget()
        self.form.setWindowTitle(f"ROM region: {region_object.Label}")
        panel_layout = QtGui.QVBoxLayout(self.form)

        # Debounced recompute: typing applies values at once, but the
        # booleans only run once the user pauses.
        self.recompute_timer = QtCore.QTimer()
        self.recompute_timer.setSingleShot(True)
        self.recompute_timer.setInterval(RECOMPUTE_DELAY_MILLISECONDS)
        self.recompute_timer.timeout.connect(self._apply_and_recompute)

        # --- Region ---------------------------------------------------------
        region_group = QtGui.QGroupBox("Region")
        region_layout = QtGui.QFormLayout(region_group)
        self.region_name_edit = QtGui.QLineEdit(region_object.RegionName)
        self.material_edit = QtGui.QLineEdit(region_object.Material)
        self.material_edit.setPlaceholderText("e.g. aluminum_6061")
        self.cell_count_box = QtGui.QSpinBox()
        self.cell_count_box.setRange(1, 10000)
        self.cell_count_box.setValue(region_object.NAxialCells)
        self.color_button = QtGui.QPushButton()
        self.chosen_color = tuple(region_object.Color[:3])
        self._show_color()
        region_layout.addRow("Region name", self.region_name_edit)
        region_layout.addRow("Material", self.material_edit)
        region_layout.addRow("Axial cells", self.cell_count_box)
        region_layout.addRow("Preview colour", self.color_button)

        # Inverse = fluid region: the empty space the sources leave inside the limits.
        self.inverse_checkbox = QtGui.QCheckBox("Inverse: the empty space around the sources (fluid regions)")
        self.inverse_checkbox.setToolTip("Region = everything inside the limits that the sources do NOT fill. Without R max, the outer limit is the sources' largest radius.")
        self.inverse_checkbox.setChecked(region_object.Inverse)
        self.keep_largest_checkbox = QtGui.QCheckBox("Keep only the largest empty space (drop seal grooves, small gaps)")
        self.keep_largest_checkbox.setChecked(region_object.KeepOnlyLargestPiece)
        self.keep_largest_checkbox.setEnabled(region_object.Inverse)
        region_layout.addRow(self.inverse_checkbox)
        region_layout.addRow(self.keep_largest_checkbox)
        panel_layout.addWidget(region_group)

        self.region_name_edit.editingFinished.connect(self._schedule_recompute)
        self.material_edit.editingFinished.connect(self._schedule_recompute)
        self.cell_count_box.valueChanged.connect(self._schedule_recompute)
        self.color_button.clicked.connect(self._choose_color)
        self.inverse_checkbox.toggled.connect(self.keep_largest_checkbox.setEnabled)
        self.inverse_checkbox.toggled.connect(self._schedule_recompute)
        self.keep_largest_checkbox.toggled.connect(self._schedule_recompute)

        # --- Sources --------------------------------------------------------
        source_group = QtGui.QGroupBox("Sources (fused into one solid; with Inverse, the solids around the space)")
        source_layout = QtGui.QVBoxLayout(source_group)
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
        source_layout.addWidget(self.source_list)
        source_layout.addLayout(source_buttons)
        panel_layout.addWidget(source_group)

        # --- Limits ---------------------------------------------------------
        limits_group = QtGui.QGroupBox("Limits  (value, '=' for an expression, or Pick an LCS / face)")
        limits_layout = QtGui.QGridLayout(limits_group)
        for column_index, header_text in enumerate(("", "Value", "Reference", "", "", "Offset")):
            limits_layout.addWidget(QtGui.QLabel(f"<b>{header_text}</b>"), 0, column_index)

        self.value_boxes = {}
        self.offset_boxes = {}
        self.reference_labels = {}
        for row_index, (limit_name, row_label, allow_negative) in enumerate(LIMIT_ROWS, start=1):
            value_box, value_binding = make_quantity_box(region_object, limit_name, allow_negative)
            offset_box, offset_binding = make_quantity_box(region_object, f"{limit_name}Offset", True)
            self.expression_bindings.extend([value_binding, offset_binding])

            reference_label = QtGui.QLabel()
            reference_label.setWordWrap(True)
            pick_button = QtGui.QPushButton("Pick")
            pick_button.setToolTip("Use the selected LCS, vertex or face (select it in the 3D view or tree first)")
            clear_button = QtGui.QPushButton("✕")
            clear_button.setToolTip("Remove the reference")
            clear_button.setMaximumWidth(28)

            pick_button.clicked.connect(lambda checked=False, name=limit_name: self._pick_reference(name))
            clear_button.clicked.connect(lambda checked=False, name=limit_name: self._clear_reference(name))
            value_box.valueChanged.connect(self._schedule_recompute)
            offset_box.valueChanged.connect(self._schedule_recompute)

            limits_layout.addWidget(QtGui.QLabel(row_label), row_index, 0)
            limits_layout.addWidget(value_box, row_index, 1)
            limits_layout.addWidget(reference_label, row_index, 2)
            limits_layout.addWidget(pick_button, row_index, 3)
            limits_layout.addWidget(clear_button, row_index, 4)
            limits_layout.addWidget(offset_box, row_index, 5)
            limits_layout.setColumnStretch(2, 1)

            self.value_boxes[limit_name] = value_box
            self.offset_boxes[limit_name] = offset_box
            self.reference_labels[limit_name] = reference_label
        panel_layout.addWidget(limits_group)

        self.status_label = QtGui.QLabel(region_object.Status)
        self.status_label.setWordWrap(True)
        panel_layout.addWidget(self.status_label)

        self._refresh_limit_rows()

    # --- sources --------------------------------------------------------------

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
        self._schedule_recompute()

    def _remove_highlighted_sources(self):
        highlighted_rows = sorted((self.source_list.row(item) for item in self.source_list.selectedItems()), reverse=True)
        for row in highlighted_rows:
            del self.source_objects[row]
        self._refresh_source_list()
        self._schedule_recompute()

    # --- colour -----------------------------------------------------------------

    def _show_color(self):
        red, green, blue = (int(255 * component) for component in self.chosen_color)
        self.color_button.setStyleSheet(f"background-color: rgb({red}, {green}, {blue});")

    def _choose_color(self):
        red, green, blue = (int(255 * component) for component in self.chosen_color)
        picked_color = QtGui.QColorDialog.getColor(QtGui.QColor(red, green, blue), self.form)
        if picked_color.isValid():
            self.chosen_color = (picked_color.redF(), picked_color.greenF(), picked_color.blueF())
            self._show_color()
            self._schedule_recompute()

    # --- references -------------------------------------------------------------

    def _pick_reference(self, limit_name):
        selection = Gui.Selection.getSelectionEx("*", 0)
        if len(selection) == 0:
            QtGui.QMessageBox.information(self.form, "ROM region", "Select an LCS, vertex or face first (in the 3D view or the tree), then click Pick.")
            return

        # resolve=0 keeps the full link path (e.g. the assembly's link "Body"
        # + "injector_attachment_lcs."), which is what places an Assembly4
        # LCS correctly in the assembly.
        selected = selection[0]
        sub_name = selected.SubElementNames[0] if selected.SubElementNames else ""
        if selected.Object is self.region_object:
            return

        setattr(self.region_object, f"{limit_name}Reference", (selected.Object, [sub_name] if sub_name else []))
        self.region_object.setExpression(limit_name, None)
        Gui.Selection.clearSelection()
        self._apply_and_recompute()

    def _clear_reference(self, limit_name):
        setattr(self.region_object, f"{limit_name}Reference", None)
        self._apply_and_recompute()

    def _refresh_limit_rows(self):
        """Show what each limit currently is. A referenced limit's value box
        is read-only and shows the position the reference resolved to."""
        for limit_name, row_label, allow_negative in LIMIT_ROWS:
            reference_value = getattr(self.region_object, f"{limit_name}Reference")
            has_reference = reference_value is not None and reference_value[0] is not None
            value_box = self.value_boxes[limit_name]

            value_box.blockSignals(True)
            value_box.setProperty("rawValue", getattr(self.region_object, limit_name).Value)
            value_box.blockSignals(False)
            value_box.setEnabled(not has_reference)
            self.offset_boxes[limit_name].setEnabled(has_reference)
            self.reference_labels[limit_name].setText(reference_text(reference_value))

    # --- applying ---------------------------------------------------------------

    def _schedule_recompute(self, *ignored_signal_arguments):
        self.recompute_timer.start()

    def _apply_to_object(self):
        region_object = self.region_object
        region_name = self.region_name_edit.text().strip()
        if region_object.RegionName != region_name:
            region_object.RegionName = region_name
            if region_name:
                region_object.Label = region_name
        region_object.Material = self.material_edit.text().strip()
        if list(region_object.SourceShapes) != self.source_objects:
            region_object.SourceShapes = self.source_objects
        region_object.NAxialCells = self.cell_count_box.value()
        region_object.Color = self.chosen_color
        region_object.Inverse = self.inverse_checkbox.isChecked()
        region_object.KeepOnlyLargestPiece = self.keep_largest_checkbox.isChecked()

        # Values typed in the boxes. Expressions were already written onto the
        # object by the binding, and references are resolved on recompute,
        # so only plain values are copied here.
        for limit_name, row_label, allow_negative in LIMIT_ROWS:
            reference_value = getattr(region_object, f"{limit_name}Reference")
            has_reference = reference_value is not None and reference_value[0] is not None
            if not has_reference and not property_expression(region_object, limit_name):
                setattr(region_object, limit_name, self.value_boxes[limit_name].property("rawValue"))
            offset_name = f"{limit_name}Offset"
            if not property_expression(region_object, offset_name):
                setattr(region_object, offset_name, self.offset_boxes[limit_name].property("rawValue"))

    def _apply_and_recompute(self):
        self.recompute_timer.stop()
        try:
            self._apply_to_object()
            self.region_object.Document.recompute()
        except Exception as apply_error:
            self.status_label.setText(f"error: {apply_error}")
            return
        self._refresh_limit_rows()
        self.status_label.setText(self.region_object.Status)

    # --- task panel protocol ----------------------------------------------------

    def getStandardButtons(self):
        return QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel

    def accept(self):
        self._apply_and_recompute()
        if self.status_label.text().startswith("error:"):
            QtGui.QMessageBox.warning(self.form, "ROM region", self.status_label.text())
            return False
        self.region_object.Document.commitTransaction()
        Gui.getDocument(self.region_object.Document.Name).resetEdit()
        Gui.Control.closeDialog()
        return True

    def reject(self):
        self.recompute_timer.stop()
        self.region_object.Document.abortTransaction()
        self.region_object.Document.recompute()
        Gui.getDocument(self.region_object.Document.Name).resetEdit()
        Gui.Control.closeDialog()
        return True

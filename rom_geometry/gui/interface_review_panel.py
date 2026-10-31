"""Review Interfaces: every region-pair interface found by the generalFuse,
with its total area and an Ignore checkbox; the near-contacts (gaps below
NearContactTolerance) with their size; and each region's unassigned outer
surface. Clicking a row highlights it in the 3D view.

OK writes the ticked pairs to the model's IgnoredInterfaces. An ignored
interface is exported as a boundary face with no patch, so its area is still
accounted for but no heat crosses it.
"""

import FreeCADGui as Gui
from PySide import QtCore, QtGui

from rom_geometry.core.model_build import build_rom_geometry, summarize_for_review
from rom_geometry.gui.highlight import ShapeHighlighter
from rom_geometry.objects.rom_model import model_spec_from_rom_model


INTERFACE_COLOR = (1.0, 0.55, 0.0)
NEAR_CONTACT_COLOR = (0.9, 0.1, 0.1)
UNASSIGNED_COLOR = (0.6, 0.2, 0.9)

ROW_SHAPES_ROLE = QtCore.Qt.UserRole
ROW_COLOR_ROLE = QtCore.Qt.UserRole + 1
ROW_PAIR_ROLE = QtCore.Qt.UserRole + 2


def ignored_pair_key(region_a, region_b):
    return frozenset((region_a, region_b))


class InterfaceReviewPanel:

    def __init__(self, model_object, progress_callback=None):
        self.model_object = model_object
        self.highlighter = ShapeHighlighter()
        self.row_shapes = {}

        model_object.Document.recompute()
        build = build_rom_geometry(model_spec_from_rom_model(model_object), progress_callback)
        interface_summaries, near_contact_summaries, unassigned_summaries = summarize_for_review(build)

        self.form = QtGui.QWidget()
        self.form.setWindowTitle(f"Review interfaces: {model_object.Label}")
        panel_layout = QtGui.QVBoxLayout(self.form)
        panel_layout.addWidget(QtGui.QLabel("Click a row to highlight it. Tick Ignore to export that interface as an unpatched boundary (no heat crosses it)."))

        self.tree = QtGui.QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Item", "Area / gap", "Ignore"])
        panel_layout.addWidget(self.tree)

        # --- interfaces -------------------------------------------------------
        interfaces_root = QtGui.QTreeWidgetItem([f"Interfaces ({len(interface_summaries)})", "", ""])
        self.tree.addTopLevelItem(interfaces_root)
        self.interface_items = []
        for interface_summary in interface_summaries:
            interface_item = QtGui.QTreeWidgetItem([f"{interface_summary.region_a}  |  {interface_summary.region_b}", f"{interface_summary.area:.6g} mm^2", ""])
            interface_item.setFlags(interface_item.flags() | QtCore.Qt.ItemIsUserCheckable)
            interface_item.setCheckState(2, QtCore.Qt.Checked if interface_summary.ignored else QtCore.Qt.Unchecked)
            interface_item.setData(0, ROW_PAIR_ROLE, [interface_summary.region_a, interface_summary.region_b])
            self._remember_shapes(interface_item, interface_summary.faces, INTERFACE_COLOR)
            interfaces_root.addChild(interface_item)
            self.interface_items.append(interface_item)

        # --- near-contacts ----------------------------------------------------
        near_contacts_root = QtGui.QTreeWidgetItem([f"Near-contacts ({len(near_contact_summaries)})", "", ""])
        self.tree.addTopLevelItem(near_contacts_root)
        for near_contact in near_contact_summaries:
            near_contact_item = QtGui.QTreeWidgetItem([f"cell {near_contact['cell_a']} ({near_contact['region_a']})  |  cell {near_contact['cell_b']} ({near_contact['region_b']})", f"gap {near_contact['gap']:.4g} mm", ""])
            self._remember_shapes(near_contact_item, near_contact["solids"], NEAR_CONTACT_COLOR)
            near_contacts_root.addChild(near_contact_item)

        # --- unassigned outer surfaces ------------------------------------------
        unassigned_root = QtGui.QTreeWidgetItem([f"Unassigned outer surfaces ({len(unassigned_summaries)} regions)", "", ""])
        self.tree.addTopLevelItem(unassigned_root)
        for unassigned_summary in unassigned_summaries:
            unassigned_item = QtGui.QTreeWidgetItem([unassigned_summary.region, f"{unassigned_summary.area:.6g} mm^2", ""])
            self._remember_shapes(unassigned_item, unassigned_summary.faces, UNASSIGNED_COLOR)
            unassigned_root.addChild(unassigned_item)

        self.tree.expandAll()
        for column_index in range(3):
            self.tree.resizeColumnToContents(column_index)
        self.tree.itemSelectionChanged.connect(self._highlight_selected_row)

    def _remember_shapes(self, tree_item, shapes, color_rgb):
        row_key = id(tree_item)
        self.row_shapes[row_key] = (shapes, color_rgb)
        tree_item.setData(0, ROW_SHAPES_ROLE, row_key)

    def _highlight_selected_row(self):
        selected_items = self.tree.selectedItems()
        if len(selected_items) == 0:
            self.highlighter.clear()
            return
        row_key = selected_items[0].data(0, ROW_SHAPES_ROLE)
        if row_key not in self.row_shapes:
            self.highlighter.clear()
            return
        shapes, color_rgb = self.row_shapes[row_key]
        self.highlighter.show(shapes, color_rgb)

    def ticked_ignored_interfaces(self):
        """IgnoredInterfaces after this review: the ticked pairs, plus any
        existing entries that name pairs this review did not find (kept as
        they are, so nothing typed by hand is lost)."""
        reviewed_pairs = set()
        ticked_entries = []
        for interface_item in self.interface_items:
            region_a, region_b = interface_item.data(0, ROW_PAIR_ROLE)
            reviewed_pairs.add(ignored_pair_key(region_a, region_b))
            if interface_item.checkState(2) == QtCore.Qt.Checked:
                ticked_entries.append(f"{region_a}|{region_b}")

        kept_entries = []
        for existing_entry in self.model_object.IgnoredInterfaces:
            existing_names = [name.strip() for name in existing_entry.split("|")]
            if len(existing_names) == 2 and ignored_pair_key(*existing_names) in reviewed_pairs:
                continue
            kept_entries.append(existing_entry)
        return kept_entries + ticked_entries

    def getStandardButtons(self):
        return QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel

    def accept(self):
        self.highlighter.clear()
        new_ignored_interfaces = self.ticked_ignored_interfaces()
        if list(self.model_object.IgnoredInterfaces) != new_ignored_interfaces:
            doc = self.model_object.Document
            doc.openTransaction("Review interfaces")
            self.model_object.IgnoredInterfaces = new_ignored_interfaces
            doc.commitTransaction()
            doc.recompute()
        Gui.Control.closeDialog()
        return True

    def reject(self):
        self.highlighter.clear()
        Gui.Control.closeDialog()
        return True

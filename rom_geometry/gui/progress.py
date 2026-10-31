"""A progress dialog for the slow geometry steps (Validate, Export, Review
Interfaces), so FreeCAD never looks frozen while the booleans run."""

import FreeCADGui as Gui
from PySide import QtCore, QtGui


class BuildProgressDialog:
    """Use as a context manager; pass `.update` as the progress callback:

        with BuildProgressDialog("Validating") as progress:
            validate_rom_model(model_object, progress.update)
    """

    def __init__(self, title):
        self.title = title
        self.dialog = None

    def __enter__(self):
        self.dialog = QtGui.QProgressDialog(self.title, None, 0, 100, Gui.getMainWindow())
        self.dialog.setWindowTitle("ROM Geometry")
        self.dialog.setWindowModality(QtCore.Qt.WindowModal)
        self.dialog.setMinimumDuration(0)
        self.dialog.setValue(0)
        QtGui.QApplication.processEvents()
        return self

    def update(self, fraction, stage_text):
        self.dialog.setLabelText(f"{self.title}: {stage_text}")
        self.dialog.setValue(int(round(100 * fraction)))
        QtGui.QApplication.processEvents()

    def __exit__(self, exception_type, exception_value, traceback):
        self.dialog.close()
        self.dialog = None
        return False

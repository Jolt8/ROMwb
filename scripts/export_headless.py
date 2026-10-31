"""Export a document's ROMModel to JSON without the GUI.

    python scripts/export_headless.py model.FCStd out.json [--model ROMModelLabel]
                                      [--cells REGION=N ...] [--cells "*=N"]

--cells overrides NAxialCells per region (repeatable; "*" = every region,
applied first, so `--cells "*=1" --cells fuel_grain=10` works). The override
is used for this export only; the .FCStd file is not changed.

Re-launches itself under FreeCAD 1.1's bundled Python (set FREECAD_BIN to
use another install). Linked documents (the assembly, table_hybrid) are
opened automatically by FreeCAD. Exit code 0 = written, 1 = validation
errors (nothing written), 2 = usage error.
"""

import os
import sys

REPOSITORY_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPOSITORY_ROOT not in sys.path:
    sys.path.insert(0, REPOSITORY_ROOT)

from rom_geometry.freecad_bootstrap import ensure_running_under_freecad_python

ensure_running_under_freecad_python(__file__)

import FreeCAD as App

from rom_geometry.core.export_json import ExportRefusedError
from rom_geometry.objects.rom_model import export_rom_model, find_rom_models


def parse_cell_overrides(arguments):
    """All `--cells NAME=N` pairs -> {NAME: N}."""
    n_axial_cells_overrides = {}
    for argument_index, argument in enumerate(arguments):
        if argument != "--cells":
            continue
        override_text = arguments[argument_index + 1]
        region_name, separator, cell_count_text = override_text.rpartition("=")
        if not separator or not region_name:
            raise ValueError(f"--cells expects REGION=N, got '{override_text}'")
        n_axial_cells_overrides[region_name] = int(cell_count_text)
    return n_axial_cells_overrides


def main(arguments):
    if len(arguments) < 2:
        print(__doc__)
        return 2

    document_path = os.path.abspath(arguments[0])
    output_path = os.path.abspath(arguments[1])
    model_label = None
    if "--model" in arguments:
        model_label = arguments[arguments.index("--model") + 1]

    try:
        n_axial_cells_overrides = parse_cell_overrides(arguments)
    except ValueError as argument_error:
        print(argument_error)
        return 2

    doc = App.openDocument(document_path)
    rom_models = find_rom_models(doc)
    if model_label is not None:
        rom_models = [model_object for model_object in rom_models if model_object.Label == model_label]

    if len(rom_models) != 1:
        model_labels = ", ".join(model_object.Label for model_object in find_rom_models(doc)) or "none"
        print(f"expected exactly one ROMModel (found: {model_labels}); use --model LABEL")
        return 2

    try:
        build, report, export = export_rom_model(rom_models[0], output_path, n_axial_cells_overrides)
    except ExportRefusedError as refusal:
        print(refusal.report.as_text())
        print("export refused: nothing was written")
        return 1
    except ValueError as override_error:
        print(override_error)
        return 2

    print(report.as_text())
    if n_axial_cells_overrides:
        print("cell-count overrides used (not saved): " + ", ".join(f"{region_name}={n_axial_cells}" for region_name, n_axial_cells in n_axial_cells_overrides.items()))
    print(f"wrote {output_path}: {len(export['cells'])} cells, {len(export['facetsets'])} face sets")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

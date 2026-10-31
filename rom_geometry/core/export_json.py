"""Write the ROM geometry JSON (format_version 1) that FVMFramework loads.

Conventions (see docs/export_format.md):
  - every id is 1-based (Julia),
  - every value is SI: m, m^2, m^3, rad,
  - face sets hold only one half of each interface.
"""

import datetime
import json

import FreeCAD as App

from rom_geometry import ROM_GEOMETRY_VERSION
from rom_geometry.core.model_build import build_rom_geometry
from rom_geometry.core.validation import validate_rom_geometry


EXPORT_FORMAT_VERSION = 1

MILLIMETRE_TO_METRE = 1.0e-3
SQUARE_MILLIMETRE_TO_SQUARE_METRE = 1.0e-6
CUBIC_MILLIMETRE_TO_CUBIC_METRE = 1.0e-9

# Coordinates closer to zero than this (in m) are written as 0.0, so that
# round-off such as 3e-17 on an axis does not clutter the file.
COORDINATE_ROUND_OFF = 1.0e-12


class ExportRefusedError(Exception):
    """Raised when validation found errors. `report` holds the full report."""

    def __init__(self, report):
        super().__init__("export refused, validation found errors:\n" + report.as_text())
        self.report = report


def _point_in_metres(point_in_millimetres):
    point_in_metres = []
    for component in (point_in_millimetres.x, point_in_millimetres.y, point_in_millimetres.z):
        component_in_metres = component * MILLIMETRE_TO_METRE
        if abs(component_in_metres) < COORDINATE_ROUND_OFF:
            component_in_metres = 0.0
        point_in_metres.append(component_in_metres)
    return point_in_metres


def _unit_vector_list(unit_vector):
    if unit_vector is None:
        return None
    unit_vector_components = []
    for component in (unit_vector.x, unit_vector.y, unit_vector.z):
        if abs(component) < 1.0e-12:
            component = 0.0
        unit_vector_components.append(component)
    return unit_vector_components


def _length_or_null(length_in_millimetres):
    if length_in_millimetres is None or length_in_millimetres == 0:
        return None
    return length_in_millimetres * MILLIMETRE_TO_METRE


def build_export_dict(build, report):
    model_spec = build.model_spec
    freecad_version = ".".join(App.Version()[:3])

    export = {
        "format_version": EXPORT_FORMAT_VERSION,
        "generator": {"name": "ROMGeometry", "version": ROM_GEOMETRY_VERSION, "freecad_version": freecad_version},
        "source_document": model_spec.source_document,
        "exported_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "axis": {"origin": _point_in_metres(model_spec.engine_axis.origin), "direction": _unit_vector_list(model_spec.engine_axis.direction)},
        "radial_node_rule": model_spec.radial_node_rule,
    }

    export["parameters"] = {}
    for alias, parameter in build.parameters.items():
        export["parameters"][alias] = {"value": parameter["value"], "unit": parameter["unit"]}

    export["regions"] = []
    for region_geometry in build.region_geometries:
        region_spec = region_geometry.spec
        export["regions"].append({
            "name": region_spec.name,
            "material": region_spec.material,
            "source_shapes": list(region_spec.source_labels),
            "z_min": region_spec.z_min * MILLIMETRE_TO_METRE,
            "z_max": region_spec.z_max * MILLIMETRE_TO_METRE,
            "r_min": _length_or_null(region_spec.r_min),
            "r_max": _length_or_null(region_spec.r_max),
            "n_axial_cells": region_spec.n_axial_cells,
            "cell_ids": list(build.cellsets.get(region_spec.name, [])),
            "z_min_expression": region_spec.z_min_expression,
            "z_max_expression": region_spec.z_max_expression,
        })

    export["cells"] = []
    for cell in build.cells:
        exported_faces = []
        for cell_face in cell.faces:
            exported_face = {
                "local_idx": cell_face.local_idx,
                "kind": cell_face.kind,
                "neighbor_cell": cell_face.neighbor_cell,
                "neighbor_local_idx": cell_face.neighbor_local_idx,
            }
            if cell_face.kind == "boundary":
                exported_face["patch"] = cell_face.patch
            if cell_face.kind == "ignored_interface":
                exported_face["ignored_neighbor_cell"] = cell_face.ignored_neighbor_cell

            exported_face["area"] = cell_face.area * SQUARE_MILLIMETRE_TO_SQUARE_METRE
            exported_face["surface"] = cell_face.surface_type
            exported_face["radius"] = _length_or_null(cell_face.radius)
            exported_face["outward"] = cell_face.outward_radial_direction
            exported_face["normal"] = _unit_vector_list(cell_face.outward_normal)
            if cell_face.meridional_normal is not None:
                exported_face["meridional_normal"] = [cell_face.meridional_normal[0], cell_face.meridional_normal[1]]
            exported_face["centroid"] = _point_in_metres(cell_face.centroid)
            exported_face["half_cell_distance"] = cell_face.half_cell_distance * MILLIMETRE_TO_METRE
            if cell_face.kind == "interface":
                exported_face["neighbor_distance"] = cell_face.neighbor_distance * MILLIMETRE_TO_METRE
                exported_face["centroid_distance"] = cell_face.centroid_distance * MILLIMETRE_TO_METRE
            exported_face["distance_method"] = cell_face.distance_method
            exported_face["notes"] = "; ".join(cell_face.notes)
            exported_faces.append(exported_face)

        export["cells"].append({
            "id": cell.cell_id,
            "region": cell.region_name,
            "axial_index": cell.axial_index,
            "volume": cell.volume * CUBIC_MILLIMETRE_TO_CUBIC_METRE,
            "centroid": _point_in_metres(cell.centroid),
            "z_min": cell.z_min * MILLIMETRE_TO_METRE,
            "z_max": cell.z_max * MILLIMETRE_TO_METRE,
            "r_inner": cell.r_inner * MILLIMETRE_TO_METRE,
            "r_outer": cell.r_outer * MILLIMETRE_TO_METRE,
            "faces": exported_faces,
        })

    export["cellsets"] = {}
    for region_name, region_cell_ids in build.cellsets.items():
        export["cellsets"][region_name] = list(region_cell_ids)

    export["facetsets"] = {}
    for face_set_name, face_set_entries in build.facetsets.items():
        export["facetsets"][face_set_name] = [list(entry) for entry in face_set_entries]

    export["unassigned_boundary_faces"] = []
    for unassigned_face in build.unassigned_boundary_faces:
        export["unassigned_boundary_faces"].append({"cell": unassigned_face["cell"], "local_idx": unassigned_face["local_idx"], "area": unassigned_face["area"] * SQUARE_MILLIMETRE_TO_SQUARE_METRE, "centroid": _point_in_metres(unassigned_face["centroid"])})

    export["near_contacts"] = []
    for near_contact in build.near_contacts:
        export["near_contacts"].append({"region_a": near_contact["region_a"], "region_b": near_contact["region_b"], "cell_a": near_contact["cell_a"], "cell_b": near_contact["cell_b"], "gap": near_contact["gap"] * MILLIMETRE_TO_METRE})

    export["validation"] = {"errors": list(report.errors), "warnings": list(report.warnings)}
    return export


def export_rom_geometry(model_spec, output_path=None):
    """Build, validate and (if there are no errors) write the JSON.

    Returns (build, report, export_dict). Raises ExportRefusedError if
    validation found errors; nothing is written in that case. With
    output_path=None nothing is written (useful for tests).
    """
    build = build_rom_geometry(model_spec)
    report = validate_rom_geometry(build)

    if not report.is_valid:
        raise ExportRefusedError(report)

    export_dict = build_export_dict(build, report)

    if output_path is not None:
        with open(output_path, "w", encoding="utf-8") as output_file:
            json.dump(export_dict, output_file, indent=1)
            output_file.write("\n")

    return build, report, export_dict

"""Build the test fixture .FCStd from code (no hand-made binaries in git).

A small stack of coaxial solids imitating the engine, all on the global Z
axis, dimensions in mm and driven by the spreadsheet `fixture_parameters`:

    half section, axis on the left, r in mm:

               r=0          18   20      24
    z = 65     +-------------+----+-------+   end_cap: solid disk r 0..24
    z = 60     +-------------+----+-------+
               |   cavity    |liner|housing|  cavity  r  0..18 (fluid, made by subtraction)
               |   (fluid)   |    |       |  liner   r 18..20
    z =  0     +-------------+----+-------+  housing r 20..24
                    0.2 mm gap (deliberate near-contact)
    z = -0.2              ____+-----------+   nozzle: outer r 24, conical bore
                      ___/                |   from r = 18 at the top
    z = -10.2   r=10 +--------------------+   to r = 10 at the bottom

Usage under FreeCAD's python:
    python tests/build_fixture.py out_dir
"""

import os
import sys

REPOSITORY_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPOSITORY_ROOT not in sys.path:
    sys.path.insert(0, REPOSITORY_ROOT)

from rom_geometry.freecad_bootstrap import ensure_running_under_freecad_python

ensure_running_under_freecad_python(__file__)

import FreeCAD as App
import Part

from rom_geometry.core.axis_geometry import EngineAxis
from rom_geometry.core.boundary_patches import BoundaryPatchSpec
from rom_geometry.core.model_build import ROMModelSpec
from rom_geometry.core.regions import RegionSpec, make_cavity_solid


FIXTURE_DOCUMENT_NAME = "rom_fixture"
SHEET_NAME = "fixture_parameters"

FIXTURE_PARAMETERS = [
    ("housing_length", "60 mm"),
    ("housing_outer_radius", "24 mm"),
    ("housing_inner_radius", "20 mm"),
    ("liner_inner_radius", "18 mm"),
    ("end_cap_thickness", "5 mm"),
    ("nozzle_length", "10 mm"),
    ("nozzle_gap", "0.2 mm"),
    ("nozzle_throat_radius", "10 mm"),
    ("reference_half_angle", "30 deg"),
    ("n_liner_cells", "3"),
]


def _add_cylinder(doc, name, radius_alias, height_alias, base_z_expression=None):
    cylinder = doc.addObject("Part::Cylinder", name)
    cylinder.setExpression("Radius", f"{SHEET_NAME}.{radius_alias}")
    cylinder.setExpression("Height", f"{SHEET_NAME}.{height_alias}")
    if base_z_expression is not None:
        cylinder.setExpression(".Placement.Base.z", base_z_expression)
    return cylinder


def _add_cut(doc, name, base_object, tool_object):
    cut = doc.addObject("Part::Cut", name)
    cut.Base = base_object
    cut.Tool = tool_object
    return cut


def build_fixture_document(output_directory):
    """Create, recompute and save the fixture. Returns the saved path."""
    doc = App.newDocument(FIXTURE_DOCUMENT_NAME)

    sheet = doc.addObject("Spreadsheet::Sheet", SHEET_NAME)
    for row_number, (alias, value_text) in enumerate(FIXTURE_PARAMETERS, start=1):
        cell_address = f"B{row_number}"
        sheet.set(f"A{row_number}", alias)
        sheet.set(cell_address, value_text)
        sheet.setAlias(cell_address, alias)
    doc.recompute()

    # Housing and liner: annuli made from two cylinders each.
    housing_outer = _add_cylinder(doc, "housing_outer", "housing_outer_radius", "housing_length")
    housing_bore = _add_cylinder(doc, "housing_bore", "housing_inner_radius", "housing_length")
    housing = _add_cut(doc, "housing", housing_outer, housing_bore)

    liner_outer = _add_cylinder(doc, "liner_outer", "housing_inner_radius", "housing_length")
    liner_bore = _add_cylinder(doc, "liner_bore", "liner_inner_radius", "housing_length")
    liner = _add_cut(doc, "liner", liner_outer, liner_bore)

    # Solid end cap sitting on top of housing, liner and cavity.
    end_cap = _add_cylinder(doc, "end_cap", "housing_outer_radius", "end_cap_thickness", f"{SHEET_NAME}.housing_length")

    # Nozzle below a deliberate 0.2 mm gap, with a conical bore (the one
    # non-planar, non-cylindrical face in the fixture).
    nozzle_base_z = f"-({SHEET_NAME}.nozzle_gap + {SHEET_NAME}.nozzle_length)"
    nozzle_body = _add_cylinder(doc, "nozzle_body", "housing_outer_radius", "nozzle_length", nozzle_base_z)
    nozzle_cone = doc.addObject("Part::Cone", "nozzle_cone")
    nozzle_cone.setExpression("Radius1", f"{SHEET_NAME}.nozzle_throat_radius")
    nozzle_cone.setExpression("Radius2", f"{SHEET_NAME}.liner_inner_radius")
    nozzle_cone.setExpression("Height", f"{SHEET_NAME}.nozzle_length")
    nozzle_cone.setExpression(".Placement.Base.z", nozzle_base_z)
    nozzle = _add_cut(doc, "nozzle", nozzle_body, nozzle_cone)

    doc.recompute()

    # Fluid cavity: the empty space inside the housing, made with the same
    # helper the "Make Cavity Solid" command uses.
    engine_axis = EngineAxis()
    housing_length = sheet.housing_length.Value
    housing_outer_radius = sheet.housing_outer_radius.Value
    enclosing_cylinder = engine_axis.make_axial_cylinder(housing_outer_radius, 0.0, housing_length)
    cavity_shape = make_cavity_solid(enclosing_cylinder, [housing.Shape, liner.Shape], 0.01)
    cavity = doc.addObject("Part::Feature", "cavity")
    cavity.Shape = cavity_shape

    for helper_object in (housing_outer, housing_bore, liner_outer, liner_bore, nozzle_body, nozzle_cone):
        helper_object.Visibility = False

    doc.recompute()

    output_path = os.path.join(output_directory, "rom_fixture.FCStd")
    doc.saveAs(output_path)
    return output_path


def _find_face(shape, predicate, description):
    matching_faces = [face for face in shape.Faces if predicate(face)]
    if len(matching_faces) != 1:
        raise RuntimeError(f"fixture: expected exactly one {description}, found {len(matching_faces)}")
    return matching_faces[0]


def fixture_model_spec(doc, ignored_interfaces=(), extra_regions=()):
    """The region and patch definitions a user would make in the GUI, written
    in code. Region order: cavity, liner, housing, end_cap_center,
    end_cap_outer, nozzle (cell ids 1-3, 4-6, 7-8, 9, 10, 11)."""
    sheet = doc.getObject(SHEET_NAME)
    housing_length = sheet.housing_length.Value
    end_cap_thickness = sheet.end_cap_thickness.Value
    liner_inner_radius = sheet.liner_inner_radius.Value
    nozzle_gap = sheet.nozzle_gap.Value
    nozzle_length = sheet.nozzle_length.Value
    n_liner_cells = int(sheet.n_liner_cells)

    cavity_shape = Part.getShape(doc.getObject("cavity"))
    liner_shape = Part.getShape(doc.getObject("liner"))
    housing_shape = Part.getShape(doc.getObject("housing"))
    end_cap_shape = Part.getShape(doc.getObject("end_cap"))
    nozzle_shape = Part.getShape(doc.getObject("nozzle"))

    housing_length_expression = f"{SHEET_NAME}.housing_length"
    end_cap_top_expression = f"{SHEET_NAME}.housing_length + {SHEET_NAME}.end_cap_thickness"

    regions = [
        RegionSpec("cavity", "nitrous_oxide_gas", ["cavity"], [cavity_shape], 0.0, housing_length, n_axial_cells=n_liner_cells, z_min_expression="0 mm", z_max_expression=housing_length_expression),
        RegionSpec("liner", "phenolic", ["liner"], [liner_shape], 0.0, housing_length, n_axial_cells=n_liner_cells, z_min_expression="0 mm", z_max_expression=housing_length_expression),
        RegionSpec("housing", "aluminum_6061", ["housing"], [housing_shape], 0.0, housing_length, n_axial_cells=2, z_min_expression="0 mm", z_max_expression=housing_length_expression),
        RegionSpec("end_cap_center", "aluminum_6061", ["end_cap"], [end_cap_shape], housing_length, housing_length + end_cap_thickness, r_max=liner_inner_radius, z_min_expression=housing_length_expression, z_max_expression=end_cap_top_expression),
        RegionSpec("end_cap_outer", "aluminum_6061", ["end_cap"], [end_cap_shape], housing_length, housing_length + end_cap_thickness, r_min=liner_inner_radius, z_min_expression=housing_length_expression, z_max_expression=end_cap_top_expression),
        RegionSpec("nozzle", "graphite", ["nozzle"], [nozzle_shape], -(nozzle_gap + nozzle_length), -nozzle_gap),
    ]
    regions.extend(extra_regions)

    housing_outer_radius = sheet.housing_outer_radius.Value
    end_cap_top_z = housing_length + end_cap_thickness

    housing_ambient_face = _find_face(housing_shape, lambda face: isinstance(face.Surface, Part.Cylinder) and abs(face.Surface.Radius - housing_outer_radius) < 1e-9, "housing outer cylinder")
    end_cap_top_face = _find_face(end_cap_shape, lambda face: isinstance(face.Surface, Part.Plane) and abs(face.CenterOfMass.z - end_cap_top_z) < 1e-9, "end cap top face")
    nozzle_bore_face = _find_face(nozzle_shape, lambda face: isinstance(face.Surface, Part.Cone), "nozzle cone face")

    boundary_patches = [
        BoundaryPatchSpec("housing_ambient", [housing_ambient_face]),
        BoundaryPatchSpec("end_cap_top", [end_cap_top_face]),
        BoundaryPatchSpec("nozzle_bore", [nozzle_bore_face]),
    ]

    return ROMModelSpec(
        regions=regions,
        boundary_patches=boundary_patches,
        engine_axis=EngineAxis(),
        fuzzy_tolerance=0.01,
        near_contact_tolerance=0.5,
        ignored_interfaces=list(ignored_interfaces),
        radial_node_rule="geometric_mean",
        parameter_sheets=[sheet],
        source_document=doc.FileName.replace("\\", "/"),
    )


if __name__ == "__main__":
    fixture_output_directory = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    saved_path = build_fixture_document(fixture_output_directory)
    print(f"fixture written to {saved_path}")

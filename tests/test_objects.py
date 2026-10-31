"""Headless tests of the FeaturePython objects (Phase 2), run under FreeCAD's python:

    python tests/test_objects.py

The ROM model lives in its own document and links into the fixture
document, as it will with the real assembly (which must not be modified).
"""

import json
import math
import os
import subprocess
import sys
import tempfile
import unittest

TESTS_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
REPOSITORY_ROOT = os.path.dirname(TESTS_DIRECTORY)
for import_path in (REPOSITORY_ROOT, TESTS_DIRECTORY):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from rom_geometry.freecad_bootstrap import ensure_running_under_freecad_python

ensure_running_under_freecad_python(__file__)

import FreeCAD as App
import Part

import build_fixture
from rom_geometry.core.export_json import export_rom_geometry
from rom_geometry.objects.rom_model import create_rom_model, export_rom_model, find_rom_models, rom_regions_of_model
from rom_geometry.objects.rom_region import create_rom_region, region_spec_from_object
from rom_geometry.objects.source_features import create_fastener_parts


ROM_DOCUMENT_NAME = "rom_model_doc"
SHEET_REFERENCE = f"{build_fixture.FIXTURE_DOCUMENT_NAME}#{build_fixture.SHEET_NAME}"


def build_rom_document(fixture_doc, output_directory):
    """The same six regions as build_fixture.fixture_model_spec, but made as
    document objects in a separate, saved document."""
    rom_doc = App.newDocument(ROM_DOCUMENT_NAME)
    rom_doc.saveAs(os.path.join(output_directory, f"{ROM_DOCUMENT_NAME}.FCStd"))

    model_object = create_rom_model(rom_doc)
    model_object.ParameterSheets = [fixture_doc.getObject(build_fixture.SHEET_NAME)]

    cavity = fixture_doc.getObject("cavity")
    liner = fixture_doc.getObject("liner")
    housing = fixture_doc.getObject("housing")
    end_cap = fixture_doc.getObject("end_cap")
    nozzle = fixture_doc.getObject("nozzle")

    housing_length_expression = f"{SHEET_REFERENCE}.housing_length"
    end_cap_top_expression = f"{SHEET_REFERENCE}.housing_length + {SHEET_REFERENCE}.end_cap_thickness"

    region_definitions = [
        ("cavity", "nitrous_oxide_gas", cavity, 3),
        ("liner", "phenolic", liner, 3),
        ("housing", "aluminum_6061", housing, 2),
    ]
    for region_name, material, source_object, n_axial_cells in region_definitions:
        region_object = create_rom_region(model_object, region_name, [source_object], z_min=0.0)
        region_object.Material = material
        region_object.NAxialCells = n_axial_cells
        region_object.setExpression("ZMax", housing_length_expression)

    for region_name, radius_property in (("end_cap_center", "RMax"), ("end_cap_outer", "RMin")):
        region_object = create_rom_region(model_object, region_name, [end_cap])
        region_object.Material = "aluminum_6061"
        region_object.setExpression("ZMin", housing_length_expression)
        region_object.setExpression("ZMax", end_cap_top_expression)
        region_object.setExpression(radius_property, f"{SHEET_REFERENCE}.liner_inner_radius")

    nozzle_region = create_rom_region(model_object, "nozzle", [nozzle])
    nozzle_region.Material = "graphite"

    rom_doc.recompute()
    rom_doc.save()
    return rom_doc


class ROMObjectTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        fixture_path = build_fixture.build_fixture_document(cls.temporary_directory.name)
        App.closeDocument(build_fixture.FIXTURE_DOCUMENT_NAME)
        fixture_doc = App.openDocument(fixture_path)

        rom_doc = build_rom_document(fixture_doc, cls.temporary_directory.name)
        cls.rom_document_path = rom_doc.FileName

        # Close everything and reopen only the ROM document: FreeCAD must
        # reopen the fixture through the cross-document links.
        App.closeDocument(rom_doc.Name)
        App.closeDocument(fixture_doc.Name)
        cls.rom_doc = App.openDocument(cls.rom_document_path)
        cls.rom_doc.recompute()
        cls.fixture_doc = App.getDocument(build_fixture.FIXTURE_DOCUMENT_NAME)
        cls.model_object = find_rom_models(cls.rom_doc)[0]

    @classmethod
    def tearDownClass(cls):
        for document_name in list(App.listDocuments()):
            App.closeDocument(document_name)
        cls.temporary_directory.cleanup()

    def region(self, region_name):
        return [region_object for region_object in rom_regions_of_model(self.model_object) if region_object.RegionName == region_name][0]

    def assertRelativelyClose(self, actual, expected, relative_tolerance=1.0e-9):
        self.assertLessEqual(abs(actual - expected), relative_tolerance * abs(expected), f"{actual!r} != {expected!r}")

    def test_reopen_restores_regions_in_order(self):
        region_names = [region_object.RegionName for region_object in rom_regions_of_model(self.model_object)]
        self.assertEqual(region_names, ["cavity", "liner", "housing", "end_cap_center", "end_cap_outer", "nozzle"])
        self.assertEqual(type(self.region("liner").Proxy).__name__, "ROMRegion")
        self.assertIn(build_fixture.FIXTURE_DOCUMENT_NAME, App.listDocuments())

    def test_region_shapes_have_expected_volumes(self):
        self.assertEqual(len(self.region("liner").Shape.Solids), 3)
        self.assertRelativelyClose(self.region("liner").Shape.Volume, math.pi * (20.0 ** 2 - 18.0 ** 2) * 60.0)
        self.assertRelativelyClose(self.region("housing").Shape.Volume, math.pi * (24.0 ** 2 - 20.0 ** 2) * 60.0)
        self.assertRelativelyClose(self.region("end_cap_center").Shape.Volume, math.pi * 18.0 ** 2 * 5.0)
        self.assertTrue(self.region("liner").Status.startswith("3 cell(s)"))

    def test_cross_document_expressions_survive_reopen(self):
        liner_region = self.region("liner")
        self.assertIn(("ZMax", f"{SHEET_REFERENCE}.housing_length"), liner_region.ExpressionEngine)
        self.assertAlmostEqual(liner_region.ZMax.Value, 60.0, places=9)
        self.assertAlmostEqual(self.region("end_cap_outer").RMin.Value, 18.0, places=9)

    def test_spreadsheet_change_reaches_the_regions(self):
        fixture_sheet = self.fixture_doc.getObject(build_fixture.SHEET_NAME)
        try:
            fixture_sheet.set("B1", "70 mm")
            self.fixture_doc.recompute()
            self.rom_doc.recompute()
            self.assertAlmostEqual(self.region("liner").ZMax.Value, 70.0, places=9)
            self.assertRelativelyClose(self.region("liner").Shape.Volume, math.pi * (20.0 ** 2 - 18.0 ** 2) * 70.0)
        finally:
            fixture_sheet.set("B1", "60 mm")
            self.fixture_doc.recompute()
            self.rom_doc.recompute()

    def test_object_export_matches_core_export(self):
        output_path = os.path.join(self.temporary_directory.name, "from_objects.json")
        build, report, object_export = export_rom_model(self.model_object, output_path)
        self.assertEqual(report.errors, [])
        self.assertTrue(os.path.exists(output_path))

        core_model_spec = build_fixture.fixture_model_spec(self.fixture_doc)
        core_model_spec.boundary_patches = []
        core_build, core_report, core_export = export_rom_geometry(core_model_spec)

        self.assertEqual(object_export["facetsets"], core_export["facetsets"])
        self.assertEqual(object_export["parameters"], core_export["parameters"])
        for object_cell, core_cell in zip(object_export["cells"], core_export["cells"]):
            self.assertRelativelyClose(object_cell["volume"], core_cell["volume"])
            self.assertEqual(len(object_cell["faces"]), len(core_cell["faces"]))
            for object_face, core_face in zip(object_cell["faces"], core_cell["faces"]):
                self.assertEqual(object_face["neighbor_cell"], core_face["neighbor_cell"])
                self.assertRelativelyClose(object_face["area"], core_face["area"])
                self.assertRelativelyClose(object_face["half_cell_distance"], core_face["half_cell_distance"])

        liner_region_export = [region for region in object_export["regions"] if region["name"] == "liner"][0]
        self.assertEqual(liner_region_export["z_max_expression"], f"{SHEET_REFERENCE}.housing_length")
        self.assertEqual(liner_region_export["z_min_expression"], "0 mm")

    def test_headless_script_exports_the_saved_document(self):
        output_path = os.path.join(self.temporary_directory.name, "from_script.json")
        script_path = os.path.join(REPOSITORY_ROOT, "scripts", "export_headless.py")
        script_result = subprocess.run([sys.executable, script_path, self.rom_document_path, output_path], capture_output=True, text=True)
        self.assertEqual(script_result.returncode, 0, script_result.stdout + script_result.stderr)
        with open(output_path, encoding="utf-8") as exported_file:
            script_export = json.load(exported_file)
        self.assertEqual(len(script_export["cells"]), 11)
        self.assertEqual(script_export["validation"]["errors"], [])

    def test_headless_script_cell_overrides_are_not_saved(self):
        output_path = os.path.join(self.temporary_directory.name, "overridden.json")
        script_path = os.path.join(REPOSITORY_ROOT, "scripts", "export_headless.py")
        script_arguments = [sys.executable, script_path, self.rom_document_path, output_path, "--cells", "*=1", "--cells", "liner=5"]
        script_result = subprocess.run(script_arguments, capture_output=True, text=True)
        self.assertEqual(script_result.returncode, 0, script_result.stdout + script_result.stderr)
        with open(output_path, encoding="utf-8") as exported_file:
            script_export = json.load(exported_file)

        # cavity 1 + liner 5 + housing 1 + end caps 1 + 1 + nozzle 1
        self.assertEqual(len(script_export["cells"]), 10)
        self.assertEqual(len(script_export["cellsets"]["liner"]), 5)
        self.assertEqual(self.region("liner").NAxialCells, 3)

        bad_result = subprocess.run([sys.executable, script_path, self.rom_document_path, output_path, "--cells", "no_such_region=2"], capture_output=True, text=True)
        self.assertEqual(bad_result.returncode, 2)
        self.assertIn("no region named", bad_result.stdout)

    def test_inverse_region_from_housing_and_liner(self):
        surrounding_solids = [self.fixture_doc.getObject("housing"), self.fixture_doc.getObject("liner")]
        fluid_region = create_rom_region(self.model_object, "test_fluid", surrounding_solids, inverse=True)
        fluid_region.NAxialCells = 2
        self.rom_doc.recompute()
        try:
            self.assertAlmostEqual(fluid_region.ZMax.Value, 60.0, places=9)
            self.assertEqual(len(fluid_region.Shape.Solids), 2)
            self.assertRelativelyClose(fluid_region.Shape.Volume, math.pi * 18.0 ** 2 * 60.0)
        finally:
            self.rom_doc.removeObject(fluid_region.Name)
            self.rom_doc.recompute()

    def test_default_region_names(self):
        housing = self.fixture_doc.getObject("housing")
        first_region = create_rom_region(self.model_object, None, [housing])
        second_region = create_rom_region(self.model_object, None, [housing])
        try:
            self.assertEqual(first_region.RegionName, "housing_region")
            self.assertEqual(first_region.Label, "housing_region")
            self.assertEqual(second_region.RegionName, "housing_region_2")
        finally:
            self.rom_doc.removeObject(first_region.Name)
            self.rom_doc.removeObject(second_region.Name)
            self.rom_doc.recompute()


class SourceFeatureTests(unittest.TestCase):

    def setUp(self):
        self.doc = App.newDocument("source_feature_test")

    def tearDown(self):
        App.closeDocument(self.doc.Name)

    def test_fastener_parts_split_a_bolt_between_two_plates(self):
        plate_a = self.doc.addObject("Part::Feature", "plate_a")
        plate_a.Shape = Part.makeCylinder(30.0, 5.0).cut(Part.makeCylinder(2.5, 5.0, App.Vector(20, 0, 0)))
        plate_b = self.doc.addObject("Part::Feature", "plate_b")
        plate_b.Shape = Part.makeCylinder(30.0, 5.0, App.Vector(0, 0, 5)).cut(Part.makeCylinder(2.5, 5.0, App.Vector(20, 0, 5)))
        bolt = self.doc.addObject("Part::Feature", "bolt")
        bolt.Shape = Part.makeCylinder(2.0, 12.0, App.Vector(20, 0, -1))

        model_object = create_rom_model(self.doc)
        part_for_a, part_for_b = create_fastener_parts(self.doc, bolt, plate_a, plate_b, model_object)
        self.doc.recompute()

        # Everything the workbench makes lives inside the ROM model.
        self.assertIn(part_for_a, model_object.Group)
        self.assertIn(part_for_b, model_object.Group)

        self.assertAlmostEqual(part_for_a.Shape.Volume, math.pi * 4.0 * 6.0, places=6)
        self.assertAlmostEqual(part_for_b.Shape.Volume, math.pi * 4.0 * 6.0, places=6)
        self.assertLessEqual(part_for_a.Shape.BoundBox.ZMax, 5.0 + 1e-9)
        self.assertIn("contact plane", part_for_a.Status)

    def test_source_inside_a_placed_container_uses_its_global_position(self):
        container = self.doc.addObject("App::Part", "assembly")
        container.Placement.Base = App.Vector(0, 0, 100)
        cylinder = self.doc.addObject("Part::Cylinder", "cylinder")
        cylinder.Radius = 5.0
        cylinder.Height = 10.0
        container.addObject(cylinder)

        model_object = create_rom_model(self.doc)
        region_object = create_rom_region(model_object, "placed", [cylinder])
        self.doc.recompute()

        self.assertAlmostEqual(region_object.ZMin.Value, 100.0, places=9)
        self.assertAlmostEqual(region_object.ZMax.Value, 110.0, places=9)
        self.assertAlmostEqual(region_object.Shape.Volume, math.pi * 25.0 * 10.0, places=6)

    def test_model_axis_from_a_local_coordinate_system(self):
        # Engine axis along global X: an LCS rotated so its Z points along X.
        lcs = self.doc.addObject("Part::LocalCoordinateSystem", "engine_axis")
        lcs.Placement = App.Placement(App.Vector(0, 0, 0), App.Rotation(App.Vector(0, 0, 1), App.Vector(1, 0, 0)))
        cylinder = self.doc.addObject("Part::Feature", "x_cylinder")
        cylinder.Shape = Part.makeCylinder(5.0, 20.0, App.Vector(0, 0, 0), App.Vector(1, 0, 0))

        model_object = create_rom_model(self.doc)
        model_object.Axis = lcs
        region_object = create_rom_region(model_object, "along_x", [cylinder], z_min=0.0, z_max=20.0)
        region_object.NAxialCells = 2
        self.doc.recompute()

        cell_x_extents = sorted((solid.BoundBox.XMin, solid.BoundBox.XMax) for solid in region_object.Shape.Solids)
        self.assertEqual(len(cell_x_extents), 2)
        self.assertAlmostEqual(cell_x_extents[0][1], 10.0, places=9)
        self.assertAlmostEqual(region_object.Shape.Volume, math.pi * 25.0 * 20.0, places=6)


class LimitReferenceTests(unittest.TestCase):
    """Assembly4-style layout:
      part document:      Body with bottom_lcs (z = 2) and top_lcs (z = 18),
                          plus a 20 mm tall, r = 10 mm cylinder
      assembly document:  App::Part container at z = 50, holding links to the
                          Body and to the cylinder, each placed at z = 100
      ROM document:       the region, referencing through those links.
    So in the assembly the cylinder spans z = 150..170, bottom_lcs is at
    z = 152 and top_lcs at z = 168."""

    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        directory = cls.temporary_directory.name

        part_doc = App.newDocument("reference_part")
        part_doc.saveAs(os.path.join(directory, "reference_part.FCStd"))
        body = part_doc.addObject("PartDesign::Body", "Body")
        for lcs_name, lcs_z in (("bottom_lcs", 2.0), ("top_lcs", 18.0)):
            lcs = part_doc.addObject("PartDesign::CoordinateSystem", lcs_name)
            body.addObject(lcs)
            lcs.Placement.Base = App.Vector(0, 0, lcs_z)
        cylinder = part_doc.addObject("Part::Cylinder", "cylinder")
        cylinder.Radius = 10.0
        cylinder.Height = 20.0
        part_doc.recompute()
        part_doc.save()

        assembly_doc = App.newDocument("reference_assembly")
        assembly_doc.saveAs(os.path.join(directory, "reference_assembly.FCStd"))
        container = assembly_doc.addObject("App::Part", "Assembly")
        container.Placement.Base = App.Vector(0, 0, 50)
        body_link = assembly_doc.addObject("App::Link", "BodyLink")
        body_link.LinkedObject = body
        body_link.Placement.Base = App.Vector(0, 0, 100)
        cylinder_link = assembly_doc.addObject("App::Link", "CylinderLink")
        cylinder_link.LinkedObject = cylinder
        cylinder_link.Placement.Base = App.Vector(0, 0, 100)
        container.addObject(body_link)
        container.addObject(cylinder_link)
        assembly_doc.recompute()
        assembly_doc.save()

        cls.rom_doc = App.newDocument("reference_rom")
        cls.rom_doc.saveAs(os.path.join(directory, "reference_rom.FCStd"))
        cls.container = container
        cls.body_link = body_link
        cls.cylinder_link = cylinder_link
        cls.model_object = create_rom_model(cls.rom_doc)

    @classmethod
    def tearDownClass(cls):
        for document_name in list(App.listDocuments()):
            App.closeDocument(document_name)
        cls.temporary_directory.cleanup()

    def new_region(self, region_name):
        return create_rom_region(self.model_object, region_name, [self.cylinder_link], z_min=0.0, z_max=1.0)

    def test_lcs_references_through_assembly_links(self):
        region_object = self.new_region("between_lcs")
        # Picked via the container's tree path, and via the link itself.
        region_object.ZMinReference = (self.container, ["BodyLink.bottom_lcs."])
        region_object.ZMaxReference = (self.body_link, ["top_lcs."])
        region_object.ZMaxOffset = -1.0
        region_object.NAxialCells = 3
        self.rom_doc.recompute()

        self.assertAlmostEqual(region_object.ZMin.Value, 152.0, places=9)
        self.assertAlmostEqual(region_object.ZMax.Value, 167.0, places=9)
        self.assertAlmostEqual(region_object.Shape.Volume, math.pi * 100.0 * 15.0, places=6)
        self.assertEqual(len(region_object.Shape.Solids), 3)

        region_spec = region_spec_from_object(region_object)
        self.assertEqual(region_spec.z_min_expression, "reference reference_assembly#Assembly.BodyLink.bottom_lcs")
        self.assertEqual(region_spec.z_max_expression, "reference reference_assembly#BodyLink.top_lcs - 1 mm")

    def test_face_references_give_z_and_r(self):
        region_object = self.new_region("from_faces")
        cylinder_faces = self.cylinder_link.LinkedObject.Shape.Faces
        side_face_name = f"Face{[index for index, face in enumerate(cylinder_faces, start=1) if face.Surface.__class__.__name__ == 'Cylinder'][0]}"
        top_face_name = f"Face{[index for index, face in enumerate(cylinder_faces, start=1) if face.Surface.__class__.__name__ == 'Plane' and face.CenterOfMass.z > 10][0]}"

        region_object.ZMinReference = (self.body_link, ["bottom_lcs."])
        region_object.ZMaxReference = (self.cylinder_link, [top_face_name])
        region_object.RMaxReference = (self.cylinder_link, [side_face_name])
        region_object.RMaxOffset = -2.0
        self.rom_doc.recompute()

        self.assertAlmostEqual(region_object.ZMax.Value, 170.0, places=9)
        self.assertAlmostEqual(region_object.RMax.Value, 8.0, places=9)
        self.assertAlmostEqual(region_object.Shape.Volume, math.pi * 64.0 * 18.0, places=6)

        # A planar face has no radius: clear error, empty shape.
        region_object.RMaxReference = (self.cylinder_link, [top_face_name])
        self.rom_doc.recompute()
        self.assertIn("gives no radial position", region_object.Status)
        self.assertTrue(region_object.Shape.isNull())

    def test_reference_replaces_an_expression(self):
        region_object = self.new_region("reference_wins")
        region_object.setExpression("ZMin", "140 mm")
        self.rom_doc.recompute()
        self.assertAlmostEqual(region_object.ZMin.Value, 140.0, places=9)

        region_object.ZMinReference = (self.body_link, ["bottom_lcs."])
        region_object.ZMaxReference = (self.body_link, ["top_lcs."])
        self.rom_doc.recompute()
        self.assertAlmostEqual(region_object.ZMin.Value, 152.0, places=9)
        self.assertNotIn("ZMin", [bound_property for bound_property, expression in region_object.ExpressionEngine])

    def test_references_survive_save_and_reopen(self):
        region_object = self.new_region("reopened")
        region_object.ZMinReference = (self.container, ["BodyLink.bottom_lcs."])
        region_object.ZMaxReference = (self.body_link, ["top_lcs."])
        self.rom_doc.recompute()
        self.rom_doc.save()
        region_name = region_object.Name
        rom_path = self.rom_doc.FileName

        App.closeDocument(self.rom_doc.Name)
        reopened_doc = App.openDocument(rom_path)
        reopened_doc.recompute()
        reopened_region = reopened_doc.getObject(region_name)
        self.assertEqual(reopened_region.ZMinReference[1], ["BodyLink.bottom_lcs."])
        self.assertAlmostEqual(reopened_region.ZMax.Value, 168.0, places=9)

        type(self).rom_doc = reopened_doc
        type(self).model_object = find_rom_models(reopened_doc)[0]
        type(self).container = App.getDocument("reference_assembly").getObject("Assembly")
        type(self).body_link = App.getDocument("reference_assembly").getObject("BodyLink")
        type(self).cylinder_link = App.getDocument("reference_assembly").getObject("CylinderLink")

    def test_regions_saved_before_references_get_the_new_properties(self):
        region_object = self.new_region("old_region")
        for limit_name in ("ZMin", "ZMax", "RMin", "RMax"):
            region_object.removeProperty(f"{limit_name}Reference")
            region_object.removeProperty(f"{limit_name}Offset")
        for property_name in ("Inverse", "KeepOnlyLargestPiece", "ReferenceElementKinds"):
            region_object.removeProperty(property_name)
        region_object.Proxy.onDocumentRestored(region_object)
        self.assertIn("ZMinReference", region_object.PropertiesList)
        self.assertIn("RMaxOffset", region_object.PropertiesList)
        self.assertFalse(region_object.Inverse)
        self.assertTrue(region_object.KeepOnlyLargestPiece)

    def test_renumbered_reference_element_fails_loudly(self):
        # Pick the cylinder's side face for RMax, then edit the CAD so the
        # same element name points at a different kind of face. The region
        # must stop with a "re-pick" error instead of using the wrong face.
        part_doc = App.getDocument("reference_part")
        replacement_box = part_doc.addObject("Part::Box", "replacement_box")
        part_doc.recompute()

        region_object = self.new_region("guarded")
        region_object.ZMinReference = (self.body_link, ["bottom_lcs."])
        region_object.ZMaxReference = (self.body_link, ["top_lcs."])
        side_face_index = [index for index, face in enumerate(self.cylinder_link.LinkedObject.Shape.Faces, start=1) if face.Surface.__class__.__name__ == "Cylinder"][0]
        region_object.RMaxReference = (self.cylinder_link, [f"Face{side_face_index}"])
        self.rom_doc.recompute()
        self.assertEqual(dict(region_object.ReferenceElementKinds)["RMax"], "Face:Cylinder")
        self.assertFalse(region_object.Shape.isNull())

        original_cylinder = self.cylinder_link.LinkedObject
        try:
            self.cylinder_link.LinkedObject = replacement_box
            self.cylinder_link.Document.recompute()
            region_object.touch()
            self.rom_doc.recompute()
            self.assertIn("please re-pick", region_object.Status)
            self.assertTrue(region_object.Shape.isNull())
        finally:
            self.cylinder_link.LinkedObject = original_cylinder
            self.cylinder_link.Document.recompute()

        # Re-picking clears the old record and the region works again.
        region_object.RMaxReference = (self.cylinder_link, [f"Face{side_face_index}"])
        self.rom_doc.recompute()
        self.assertFalse(region_object.Shape.isNull())


if __name__ == "__main__":
    unittest.main(verbosity=2)

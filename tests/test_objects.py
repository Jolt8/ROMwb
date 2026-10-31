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
from rom_geometry.objects.rom_region import create_rom_region
from rom_geometry.objects.source_features import create_cavity, create_fastener_parts


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

    def test_cavity_feature_from_housing_and_liner(self):
        cavity_object = create_cavity(self.rom_doc, [self.fixture_doc.getObject("housing"), self.fixture_doc.getObject("liner")], label="test_cavity")
        self.rom_doc.recompute()
        self.assertAlmostEqual(cavity_object.CylinderRadius.Value, 24.0, places=6)
        self.assertRelativelyClose(cavity_object.Shape.Volume, math.pi * 18.0 ** 2 * 60.0)
        self.rom_doc.removeObject(cavity_object.Name)


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

        part_for_a, part_for_b = create_fastener_parts(self.doc, bolt, plate_a, plate_b)
        self.doc.recompute()

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


if __name__ == "__main__":
    unittest.main(verbosity=2)

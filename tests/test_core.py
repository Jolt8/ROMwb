"""Headless tests of the geometry core, run under FreeCAD's python:

    python tests/test_core.py                 (re-launches under FreeCAD 1.1)
    python tests/test_core.py --write-expected  (regenerate expected/fixture_export.json)

The fixture is rebuilt from code in a temporary folder every run.
"""

import json
import math
import os
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
from rom_geometry.core.axis_geometry import EngineAxis
from rom_geometry.core.boundary_patches import face_fingerprint, rematch_face
from rom_geometry.core.distances import cylindrical_half_cell_distance
from rom_geometry.core.export_json import ExportRefusedError, export_rom_geometry
from rom_geometry.core.model_build import ROMModelSpec
from rom_geometry.core.regions import RegionSpec, build_region_geometry
from rom_geometry.core.source_shapes import merge_source_shapes, split_shape_between_bodies


EXPECTED_EXPORT_PATH = os.path.join(TESTS_DIRECTORY, "expected", "fixture_export.json")
ANALYTIC_RELATIVE_TOLERANCE = 1.0e-9

# Fixture dimensions in metres (see build_fixture.py).
HOUSING_LENGTH = 0.060
HOUSING_OUTER_RADIUS = 0.024
HOUSING_INNER_RADIUS = 0.020
LINER_INNER_RADIUS = 0.018
END_CAP_THICKNESS = 0.005
NOZZLE_LENGTH = 0.010
NOZZLE_GAP = 0.0002
NOZZLE_THROAT_RADIUS = 0.010

# Cell ids follow the region order in fixture_model_spec.
CAVITY_CELLS = [1, 2, 3]
LINER_CELLS = [4, 5, 6]
HOUSING_CELLS = [7, 8]
END_CAP_CENTER_CELL = 9
END_CAP_OUTER_CELL = 10
NOZZLE_CELL = 11


def faces_to_neighbor(exported_cell, neighbor_cell_id):
    return [face for face in exported_cell["faces"] if face["kind"] == "interface" and face["neighbor_cell"] == neighbor_cell_id]


class FixtureExportTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        fixture_path = build_fixture.build_fixture_document(cls.temporary_directory.name)
        App.closeDocument(build_fixture.FIXTURE_DOCUMENT_NAME)

        # Reopen from disk, as a user would, to prove the saved file is enough.
        cls.doc = App.openDocument(fixture_path)
        cls.doc.recompute()

        model_spec = build_fixture.fixture_model_spec(cls.doc)
        cls.build, cls.report, cls.export = export_rom_geometry(model_spec)
        cls.cells_by_id = {cell["id"]: cell for cell in cls.export["cells"]}

    @classmethod
    def tearDownClass(cls):
        App.closeDocument(cls.doc.Name)
        cls.temporary_directory.cleanup()

    def assertRelativelyClose(self, actual, expected, relative_tolerance=ANALYTIC_RELATIVE_TOLERANCE):
        self.assertLessEqual(abs(actual - expected), relative_tolerance * abs(expected), f"{actual!r} != {expected!r} (relative tolerance {relative_tolerance})")

    # --- validation ---------------------------------------------------------

    def test_validation_passes_with_no_errors(self):
        self.assertEqual(self.report.errors, [], self.report.as_text())

    def test_cell_and_face_set_bookkeeping(self):
        self.assertEqual(self.export["format_version"], 1)
        self.assertEqual(len(self.export["cells"]), 11)
        self.assertEqual(self.export["cellsets"]["cavity"], CAVITY_CELLS)
        self.assertEqual(self.export["cellsets"]["housing"], HOUSING_CELLS)

        for exported_cell in self.export["cells"]:
            for face in exported_cell["faces"]:
                if face["kind"] != "interface":
                    continue
                partner_face = self.cells_by_id[face["neighbor_cell"]]["faces"][face["neighbor_local_idx"] - 1]
                self.assertEqual(partner_face["neighbor_cell"], exported_cell["id"])
                self.assertEqual(partner_face["neighbor_local_idx"], face["local_idx"])

    # --- analytic volumes and areas -----------------------------------------

    def test_cell_volumes_match_analytic(self):
        cavity_cell_volume = math.pi * LINER_INNER_RADIUS ** 2 * (HOUSING_LENGTH / 3)
        liner_cell_volume = math.pi * (HOUSING_INNER_RADIUS ** 2 - LINER_INNER_RADIUS ** 2) * (HOUSING_LENGTH / 3)
        housing_cell_volume = math.pi * (HOUSING_OUTER_RADIUS ** 2 - HOUSING_INNER_RADIUS ** 2) * (HOUSING_LENGTH / 2)
        end_cap_center_volume = math.pi * LINER_INNER_RADIUS ** 2 * END_CAP_THICKNESS
        end_cap_outer_volume = math.pi * (HOUSING_OUTER_RADIUS ** 2 - LINER_INNER_RADIUS ** 2) * END_CAP_THICKNESS

        # Nozzle = cylinder minus a conical frustum.
        nozzle_cylinder_volume = math.pi * HOUSING_OUTER_RADIUS ** 2 * NOZZLE_LENGTH
        nozzle_bore_volume = math.pi * NOZZLE_LENGTH / 3 * (NOZZLE_THROAT_RADIUS ** 2 + NOZZLE_THROAT_RADIUS * LINER_INNER_RADIUS + LINER_INNER_RADIUS ** 2)
        nozzle_volume = nozzle_cylinder_volume - nozzle_bore_volume

        for cell_id in CAVITY_CELLS:
            self.assertRelativelyClose(self.cells_by_id[cell_id]["volume"], cavity_cell_volume)
        for cell_id in LINER_CELLS:
            self.assertRelativelyClose(self.cells_by_id[cell_id]["volume"], liner_cell_volume)
        for cell_id in HOUSING_CELLS:
            self.assertRelativelyClose(self.cells_by_id[cell_id]["volume"], housing_cell_volume)
        self.assertRelativelyClose(self.cells_by_id[END_CAP_CENTER_CELL]["volume"], end_cap_center_volume)
        self.assertRelativelyClose(self.cells_by_id[END_CAP_OUTER_CELL]["volume"], end_cap_outer_volume)
        self.assertRelativelyClose(self.cells_by_id[NOZZLE_CELL]["volume"], nozzle_volume)

    def test_interface_areas_match_analytic(self):
        cavity_liner_area = 2 * math.pi * LINER_INNER_RADIUS * (HOUSING_LENGTH / 3)
        cavity_internal_area = math.pi * LINER_INNER_RADIUS ** 2
        housing_internal_area = math.pi * (HOUSING_OUTER_RADIUS ** 2 - HOUSING_INNER_RADIUS ** 2)
        liner_end_cap_area = math.pi * (HOUSING_INNER_RADIUS ** 2 - LINER_INNER_RADIUS ** 2)
        end_cap_split_area = 2 * math.pi * LINER_INNER_RADIUS * END_CAP_THICKNESS

        for cavity_cell_id, liner_cell_id in zip(CAVITY_CELLS, LINER_CELLS):
            cavity_to_liner_faces = faces_to_neighbor(self.cells_by_id[cavity_cell_id], liner_cell_id)
            self.assertEqual(len(cavity_to_liner_faces), 1)
            self.assertEqual(cavity_to_liner_faces[0]["surface"], "cylinder")
            self.assertEqual(cavity_to_liner_faces[0]["outward"], "+r")
            self.assertIsNone(cavity_to_liner_faces[0]["normal"])
            self.assertRelativelyClose(cavity_to_liner_faces[0]["area"], cavity_liner_area)

        cavity_internal_face = faces_to_neighbor(self.cells_by_id[1], 2)[0]
        self.assertRelativelyClose(cavity_internal_face["area"], cavity_internal_area)
        self.assertEqual(cavity_internal_face["normal"], [0.0, 0.0, 1.0])

        # The middle liner cell (z 20..40 mm) straddles the housing split at
        # z = 30 mm, so it has one 10 mm long face to each housing cell.
        housing_contact_length_per_cell = HOUSING_LENGTH / 6
        for housing_cell_id in HOUSING_CELLS:
            liner_to_housing_faces = faces_to_neighbor(self.cells_by_id[5], housing_cell_id)
            self.assertEqual(len(liner_to_housing_faces), 1)
            self.assertRelativelyClose(liner_to_housing_faces[0]["area"], 2 * math.pi * HOUSING_INNER_RADIUS * housing_contact_length_per_cell)

        self.assertRelativelyClose(faces_to_neighbor(self.cells_by_id[7], 8)[0]["area"], housing_internal_area)
        self.assertRelativelyClose(faces_to_neighbor(self.cells_by_id[6], END_CAP_OUTER_CELL)[0]["area"], liner_end_cap_area)
        self.assertRelativelyClose(faces_to_neighbor(self.cells_by_id[END_CAP_CENTER_CELL], END_CAP_OUTER_CELL)[0]["area"], end_cap_split_area)

    # --- half-cell distances ------------------------------------------------

    def test_cylindrical_half_cell_distance_matches_hand_formula(self):
        # Liner shell: r_c = sqrt(r_in * r_out); d_eq = r_f * |ln(r_f / r_c)|.
        liner_radial_node = math.sqrt(LINER_INNER_RADIUS * HOUSING_INNER_RADIUS)
        liner_inner_face_distance = LINER_INNER_RADIUS * abs(math.log(LINER_INNER_RADIUS / liner_radial_node))
        liner_outer_face_distance = HOUSING_INNER_RADIUS * abs(math.log(HOUSING_INNER_RADIUS / liner_radial_node))

        liner_cell = self.cells_by_id[4]
        liner_inner_face = faces_to_neighbor(liner_cell, 1)[0]
        liner_outer_face = faces_to_neighbor(liner_cell, 7)[0]
        self.assertEqual(liner_inner_face["distance_method"], "cylindrical_log")
        self.assertEqual(liner_inner_face["outward"], "-r")
        self.assertRelativelyClose(liner_inner_face["half_cell_distance"], liner_inner_face_distance)
        self.assertRelativelyClose(liner_outer_face["half_cell_distance"], liner_outer_face_distance)

        # Geometric mean => equal half-resistances: d / r_f is the same on both sides.
        self.assertRelativelyClose(liner_inner_face["half_cell_distance"] / LINER_INNER_RADIUS, liner_outer_face["half_cell_distance"] / HOUSING_INNER_RADIUS)

        # Solid cavity cell: r_c falls back to r_outer / 2, and says so.
        cavity_outer_face = faces_to_neighbor(self.cells_by_id[1], 4)[0]
        self.assertRelativelyClose(cavity_outer_face["half_cell_distance"], LINER_INNER_RADIUS * math.log(2.0))
        self.assertIn("r_outer / 2", cavity_outer_face["notes"])

        # Neighbour distance = sum of both half-cell distances.
        self.assertRelativelyClose(cavity_outer_face["neighbor_distance"], LINER_INNER_RADIUS * math.log(2.0) + liner_inner_face_distance)

        # The core function itself, in mm.
        self.assertRelativelyClose(cylindrical_half_cell_distance(20.0, math.sqrt(18.0 * 20.0)), 20.0 * math.log(20.0 / math.sqrt(360.0)))

    def test_planar_half_cell_distance(self):
        housing_internal_face = faces_to_neighbor(self.cells_by_id[7], 8)[0]
        self.assertEqual(housing_internal_face["distance_method"], "planar")
        self.assertRelativelyClose(housing_internal_face["half_cell_distance"], HOUSING_LENGTH / 4)
        self.assertRelativelyClose(housing_internal_face["neighbor_distance"], HOUSING_LENGTH / 2)
        self.assertRelativelyClose(housing_internal_face["centroid_distance"], HOUSING_LENGTH / 2)

    # --- near-contacts, cones, patches, parameters ---------------------------

    def test_gap_reported_as_near_contact(self):
        near_contact_pairs = {(near_contact["region_a"], near_contact["region_b"]) for near_contact in self.export["near_contacts"]}
        self.assertEqual(near_contact_pairs, {("cavity", "nozzle"), ("liner", "nozzle"), ("housing", "nozzle")})
        for near_contact in self.export["near_contacts"]:
            self.assertRelativelyClose(near_contact["gap"], NOZZLE_GAP, 1.0e-9)
        self.assertTrue(any("near-contact" in warning for warning in self.report.warnings))

    def test_cone_face_is_marked_approximate(self):
        cone_faces = [face for face in self.cells_by_id[NOZZLE_CELL]["faces"] if face["surface"] == "cone"]
        self.assertEqual(len(cone_faces), 1)
        self.assertEqual(cone_faces[0]["distance_method"], "approximate")
        self.assertEqual(cone_faces[0]["patch"], "nozzle_bore")
        self.assertIn("meridional_normal", cone_faces[0])
        self.assertTrue(any("approximate" in warning for warning in self.report.warnings))

        # The bore faces inward (towards the axis) and opens upward (+z) as
        # it widens from r = 10 to r = 18 mm.
        normal_radial_component, normal_axial_component = cone_faces[0]["meridional_normal"]
        self.assertLess(normal_radial_component, 0.0)
        self.assertGreater(normal_axial_component, 0.0)

    def test_face_set_direction(self):
        facetsets = self.export["facetsets"]

        # Region-pair set holds only the earlier region's half-faces.
        cavity_liner_cells = [cell_id for cell_id, local_idx in facetsets["cavity__liner"]]
        self.assertEqual(cavity_liner_cells, CAVITY_CELLS)
        self.assertNotIn("liner__cavity", facetsets)

        # Internal faces appear once, owned by the lower-z cell.
        self.assertEqual([cell_id for cell_id, local_idx in facetsets["liner__liner"]], [4, 5])

    def test_boundary_patch_assignment(self):
        facetsets = self.export["facetsets"]
        housing_ambient_area = 0.0
        for cell_id, local_idx in facetsets["housing_ambient"]:
            housing_ambient_area += self.cells_by_id[cell_id]["faces"][local_idx - 1]["area"]
        self.assertEqual([cell_id for cell_id, local_idx in facetsets["housing_ambient"]], HOUSING_CELLS)
        self.assertRelativelyClose(housing_ambient_area, 2 * math.pi * HOUSING_OUTER_RADIUS * HOUSING_LENGTH)

        # The end cap top is split radially between two regions.
        self.assertEqual([cell_id for cell_id, local_idx in facetsets["end_cap_top"]], [END_CAP_CENTER_CELL, END_CAP_OUTER_CELL])

        # The end cap's outer rim was not picked, so it is listed as unassigned.
        unassigned_on_end_cap_outer = [face for face in self.export["unassigned_boundary_faces"] if face["cell"] == END_CAP_OUTER_CELL]
        self.assertEqual(len(unassigned_on_end_cap_outer), 1)
        self.assertRelativelyClose(unassigned_on_end_cap_outer[0]["area"], 2 * math.pi * HOUSING_OUTER_RADIUS * END_CAP_THICKNESS)

    def test_spreadsheet_parameters_in_si(self):
        parameters = self.export["parameters"]
        self.assertEqual(parameters["housing_length"], {"value": HOUSING_LENGTH, "unit": "m"})
        self.assertRelativelyClose(parameters["reference_half_angle"]["value"], math.radians(30.0))
        self.assertEqual(parameters["reference_half_angle"]["unit"], "rad")
        self.assertEqual(parameters["n_liner_cells"], {"value": 3, "unit": ""})

        liner_region = [region for region in self.export["regions"] if region["name"] == "liner"][0]
        self.assertEqual(liner_region["z_max_expression"], "fixture_parameters.housing_length")
        self.assertRelativelyClose(liner_region["z_max"], HOUSING_LENGTH)

    def test_fingerprint_rematches_face_after_reordering(self):
        housing_faces = list(Part.getShape(self.doc.getObject("housing")).Faces)
        outer_face = [face for face in housing_faces if isinstance(face.Surface, Part.Cylinder) and abs(face.Surface.Radius - 24.0) < 1e-9][0]
        fingerprint = face_fingerprint(outer_face)

        reordered_faces = list(reversed(housing_faces))
        matched_index, match_status, match_message = rematch_face(fingerprint, reordered_faces, 0.01)
        self.assertEqual(match_status, "exact", match_message)
        self.assertTrue(reordered_faces[matched_index].isSame(outer_face))

    # --- regression against the committed expected export -------------------

    def test_matches_expected_export(self):
        if not os.path.exists(EXPECTED_EXPORT_PATH):
            self.skipTest("no expected export; run with --write-expected")
        with open(EXPECTED_EXPORT_PATH, encoding="utf-8") as expected_file:
            expected_export = json.load(expected_file)
        self._assert_json_close(_without_volatile_fields(self.export), _without_volatile_fields(expected_export), "$")

    def _assert_json_close(self, actual, expected, json_path):
        if isinstance(expected, dict):
            self.assertIsInstance(actual, dict, json_path)
            self.assertEqual(sorted(actual.keys()), sorted(expected.keys()), json_path)
            for key in expected:
                self._assert_json_close(actual[key], expected[key], f"{json_path}.{key}")
        elif isinstance(expected, list):
            self.assertIsInstance(actual, list, json_path)
            self.assertEqual(len(actual), len(expected), json_path)
            for item_index, (actual_item, expected_item) in enumerate(zip(actual, expected)):
                self._assert_json_close(actual_item, expected_item, f"{json_path}[{item_index}]")
        elif isinstance(expected, float) or isinstance(actual, float):
            self.assertLessEqual(abs(actual - expected), 1.0e-9 * abs(expected) + 1.0e-15, f"{json_path}: {actual!r} != {expected!r}")
        else:
            self.assertEqual(actual, expected, json_path)


def _without_volatile_fields(export):
    stable_export = dict(export)
    stable_export.pop("exported_at", None)
    stable_export.pop("source_document", None)
    stable_export["generator"] = {key: value for key, value in export["generator"].items() if key != "freecad_version"}
    return stable_export


class FailureCaseTests(unittest.TestCase):
    """Cases that must be refused or flagged."""

    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory()
        fixture_path = build_fixture.build_fixture_document(cls.temporary_directory.name)
        App.closeDocument(build_fixture.FIXTURE_DOCUMENT_NAME)
        cls.doc = App.openDocument(fixture_path)
        cls.doc.recompute()

    @classmethod
    def tearDownClass(cls):
        App.closeDocument(cls.doc.Name)
        cls.temporary_directory.cleanup()

    def test_overlapping_regions_refuse_export(self):
        # A press-fit ring r 19..21 mm, z 10..20 mm cuts into both the liner
        # and the housing.
        press_fit_ring = Part.makeCylinder(21.0, 10.0, App.Vector(0, 0, 10)).cut(Part.makeCylinder(19.0, 10.0, App.Vector(0, 0, 10)))
        press_fit_region = RegionSpec("press_fit_ring", "steel", ["press_fit_ring"], [press_fit_ring], 10.0, 20.0)
        model_spec = build_fixture.fixture_model_spec(self.doc, extra_regions=[press_fit_region])

        with self.assertRaises(ExportRefusedError) as refusal:
            export_rom_geometry(model_spec)

        overlap_errors = [error for error in refusal.exception.report.errors if "overlap by" in error]
        self.assertGreaterEqual(len(overlap_errors), 2)

        # Ring part inside the liner: r 19..20 mm over 10 mm, all in liner cell 4.
        expected_liner_overlap = math.pi * (20.0 ** 2 - 19.0 ** 2) * 10.0
        liner_overlap_error = [error for error in overlap_errors if "(liner)" in error][0]
        self.assertIn(f"{expected_liner_overlap:.6g} mm^3", liner_overlap_error)

    def test_ignored_interface_becomes_unpatched_boundary(self):
        model_spec = build_fixture.fixture_model_spec(self.doc, ignored_interfaces=["end_cap_outer|end_cap_center"])
        build, report, export = export_rom_geometry(model_spec)
        self.assertEqual(report.errors, [])

        end_cap_center_cell = [cell for cell in export["cells"] if cell["id"] == END_CAP_CENTER_CELL][0]
        ignored_faces = [face for face in end_cap_center_cell["faces"] if face["kind"] == "ignored_interface"]
        self.assertEqual(len(ignored_faces), 1)
        self.assertEqual(ignored_faces[0]["neighbor_cell"], 0)
        self.assertEqual(ignored_faces[0]["ignored_neighbor_cell"], END_CAP_OUTER_CELL)
        self.assertNotIn("end_cap_center__end_cap_outer", export["facetsets"])

    def test_plane_and_cylinder_faces_to_one_neighbour_stay_separate(self):
        # A plug sitting in a cup touches it on its flat bottom AND its
        # cylindrical side: two faces to the same neighbour, not one.
        cup = Part.makeCylinder(10.0, 10.0).cut(Part.makeCylinder(5.0, 5.0, App.Vector(0, 0, 5)))
        plug = Part.makeCylinder(5.0, 5.0, App.Vector(0, 0, 5))
        regions = [
            RegionSpec("cup", "aluminum_6061", ["cup"], [cup], 0.0, 10.0),
            RegionSpec("plug", "copper", ["plug"], [plug], 5.0, 10.0),
        ]
        build, report, export = export_rom_geometry(ROMModelSpec(regions=regions, engine_axis=EngineAxis()))
        self.assertEqual(report.errors, [])

        plug_cell = export["cells"][1]
        plug_to_cup_faces = faces_to_neighbor(plug_cell, 1)
        self.assertEqual(sorted(face["surface"] for face in plug_to_cup_faces), ["cylinder", "plane"])
        for face in plug_to_cup_faces:
            self.assertIn("kept separate", face["notes"])
        self.assertTrue(any("refused merge" in warning for warning in report.warnings))

    def test_empty_region_is_an_error(self):
        empty_region = RegionSpec("nothing_here", "air", ["housing"], [Part.getShape(self.doc.getObject("housing"))], 500.0, 600.0)
        model_spec = build_fixture.fixture_model_spec(self.doc, extra_regions=[empty_region])
        with self.assertRaises(ExportRefusedError) as refusal:
            export_rom_geometry(model_spec)
        self.assertTrue(any("nothing_here" in error and "empty" in error for error in refusal.exception.report.errors))

    def test_duplicate_region_name_is_an_error(self):
        duplicate_region = RegionSpec("housing", "aluminum_6061", ["end_cap"], [Part.getShape(self.doc.getObject("end_cap"))], 60.0, 65.0)
        model_spec = build_fixture.fixture_model_spec(self.doc)
        model_spec.regions = [region for region in model_spec.regions if not region.name.startswith("end_cap")] + [duplicate_region]
        with self.assertRaises(ExportRefusedError) as refusal:
            export_rom_geometry(model_spec)
        self.assertTrue(any("used more than once" in error for error in refusal.exception.report.errors))


class MergeAndSplitTests(unittest.TestCase):
    """Two stacked plates (A: z 0..5 mm, B: z 5..10 mm) bolted together. The
    bolt's head sits under A and its nut on top of B, and the shank
    (r = 2 mm) passes through a clearance hole (r = 2.5 mm) at x = 20 mm."""

    PLATE_RADIUS = 30.0
    HOLE_RADIUS = 2.5
    SHANK_RADIUS = 2.0
    HEAD_RADIUS = 4.0
    BOLT_X = 20.0

    def _plate_with_hole(self, z_bottom, thickness):
        plate = Part.makeCylinder(self.PLATE_RADIUS, thickness, App.Vector(0, 0, z_bottom))
        hole = Part.makeCylinder(self.HOLE_RADIUS, thickness, App.Vector(self.BOLT_X, 0, z_bottom))
        return plate.cut(hole)

    def _bolt(self):
        head = Part.makeCylinder(self.HEAD_RADIUS, 1.0, App.Vector(self.BOLT_X, 0, -1.0))
        shank = Part.makeCylinder(self.SHANK_RADIUS, 10.0, App.Vector(self.BOLT_X, 0, 0.0))
        nut = Part.makeCylinder(self.HEAD_RADIUS, 1.0, App.Vector(self.BOLT_X, 0, 10.0))
        return head.fuse([shank, nut]).removeSplitter()

    def test_bolt_is_split_on_the_contact_plane(self):
        plate_a = self._plate_with_hole(0.0, 5.0)
        plate_b = self._plate_with_hole(5.0, 5.0)
        shape_split = split_shape_between_bodies(self._bolt(), plate_a, plate_b, 0.01)

        self.assertAlmostEqual(shape_split.plane_point.z, 5.0, places=9)
        self.assertAlmostEqual(shape_split.plane_normal.z, 1.0, places=9)

        # Each half = head (or nut) + 5 mm of shank.
        expected_half_volume = math.pi * self.HEAD_RADIUS ** 2 * 1.0 + math.pi * self.SHANK_RADIUS ** 2 * 5.0
        self.assertLess(abs(shape_split.part_for_body_a.Volume - expected_half_volume), 1.0e-9 * expected_half_volume)
        self.assertLess(abs(shape_split.part_for_body_b.Volume - expected_half_volume), 1.0e-9 * expected_half_volume)
        self.assertLess(shape_split.part_for_body_a.BoundBox.ZMax, 5.0 + 1.0e-9)
        self.assertGreater(shape_split.part_for_body_b.BoundBox.ZMin, 5.0 - 1.0e-9)

    def test_split_halves_merged_into_regions_export_cleanly(self):
        plate_a = self._plate_with_hole(0.0, 5.0)
        plate_b = self._plate_with_hole(5.0, 5.0)
        shape_split = split_shape_between_bodies(self._bolt(), plate_a, plate_b, 0.01)

        regions = [
            RegionSpec("plate_a", "aluminum_6061", ["plate_a", "bolt (plate_a side)"], [plate_a, shape_split.part_for_body_a], -1.0, 5.0),
            RegionSpec("plate_b", "aluminum_6061", ["plate_b", "bolt (plate_b side)"], [plate_b, shape_split.part_for_body_b], 5.0, 11.0),
        ]
        build, report, export = export_rom_geometry(ROMModelSpec(regions=regions, engine_axis=EngineAxis()))
        self.assertEqual(report.errors, [])
        self.assertFalse(any("disconnected" in warning for warning in report.warnings), report.as_text())

        # The plates touch on an annulus around the hole and the bolt halves
        # on the shank cross-section: one planar interface face in total.
        plate_a_to_b_faces = faces_to_neighbor(export["cells"][0], 2)
        self.assertEqual(len(plate_a_to_b_faces), 1)
        expected_interface_area = (math.pi * self.PLATE_RADIUS ** 2 - math.pi * self.HOLE_RADIUS ** 2 + math.pi * self.SHANK_RADIUS ** 2) * 1.0e-6
        self.assertLess(abs(plate_a_to_b_faces[0]["area"] - expected_interface_area), 1.0e-9 * expected_interface_area)

    def test_split_needs_an_explicit_plane_when_bodies_do_not_touch(self):
        plate_a = self._plate_with_hole(0.0, 5.0)
        plate_b_with_gap = self._plate_with_hole(5.5, 4.5)
        with self.assertRaises(ValueError):
            split_shape_between_bodies(self._bolt(), plate_a, plate_b_with_gap, 0.01)

        shape_split = split_shape_between_bodies(self._bolt(), plate_a, plate_b_with_gap, 0.01, plane_point=App.Vector(0, 0, 5.25), plane_normal=App.Vector(0, 0, 1))
        self.assertAlmostEqual(shape_split.part_for_body_a.BoundBox.ZMax, 5.25, places=9)

    def test_disconnected_region_still_finds_its_interfaces(self):
        # Two separate pins on a plate form one region, so its cell is a
        # compound of two solids. Both pin bottoms must be found as one
        # merged planar interface to the plate.
        plate = Part.makeCylinder(self.PLATE_RADIUS, 5.0)
        pin_one = Part.makeCylinder(2.0, 3.0, App.Vector(10.0, 0, 5.0))
        pin_two = Part.makeCylinder(2.0, 3.0, App.Vector(-10.0, 0, 5.0))
        regions = [
            RegionSpec("plate", "aluminum_6061", ["plate"], [plate], 0.0, 5.0),
            RegionSpec("pins", "steel", ["pins"], [Part.makeCompound([pin_one, pin_two])], 5.0, 8.0),
        ]
        build, report, export = export_rom_geometry(ROMModelSpec(regions=regions, engine_axis=EngineAxis()))
        self.assertEqual(report.errors, [])
        self.assertTrue(any("disconnected" in warning for warning in report.warnings))

        pins_to_plate_faces = faces_to_neighbor(export["cells"][1], 1)
        self.assertEqual(len(pins_to_plate_faces), 1)
        expected_contact_area = 2 * math.pi * 2.0 ** 2 * 1.0e-6
        self.assertLess(abs(pins_to_plate_faces[0]["area"] - expected_contact_area), 1.0e-9 * expected_contact_area)

    def test_merged_press_fit_screw_is_not_an_overlap(self):
        # Screw r = 2.6 mm in a 2.5 mm hole: interfering CAD, but merged into
        # the plate's own region the overlap simply disappears in the fuse.
        plate_a = self._plate_with_hole(0.0, 5.0)
        press_fit_screw = Part.makeCylinder(2.6, 5.0, App.Vector(self.BOLT_X, 0, 0.0))
        merged_shape, n_disconnected_pieces = merge_source_shapes([plate_a, press_fit_screw], 0.01)
        self.assertEqual(n_disconnected_pieces, 1)

        regions = [RegionSpec("plate_a", "aluminum_6061", ["plate_a", "screw"], [plate_a, press_fit_screw], 0.0, 5.0)]
        build, report, export = export_rom_geometry(ROMModelSpec(regions=regions, engine_axis=EngineAxis()))
        self.assertEqual(report.errors, [])

        overlap_volume = math.pi * (2.6 ** 2 - self.HOLE_RADIUS ** 2) * 5.0
        expected_volume = (plate_a.Volume + press_fit_screw.Volume - overlap_volume) * 1.0e-9
        self.assertLess(abs(export["cells"][0]["volume"] - expected_volume), 1.0e-9 * expected_volume)


class InverseRegionTests(unittest.TestCase):
    """Inverse (fluid) regions: the empty space the sources leave inside the limits."""

    @classmethod
    def setUpClass(cls):
        # Liner r 18..20 inside housing r 20..24, both z 0..60 mm.
        cls.liner = Part.makeCylinder(20.0, 60.0).cut(Part.makeCylinder(18.0, 60.0))
        cls.housing = Part.makeCylinder(24.0, 60.0).cut(Part.makeCylinder(20.0, 60.0))

    def inverse_region(self, r_max=0.0, keep_largest_piece=True):
        return RegionSpec("fluid", "nitrous_oxide_gas", ["liner", "housing"], [self.liner, self.housing], 0.0, 60.0, r_max=r_max, n_axial_cells=3, invert=True, keep_largest_piece=keep_largest_piece)

    def test_inverse_region_is_the_bore(self):
        # Without RMax the outer limit is the sources' largest radius (24 mm),
        # so the empty space is exactly the r < 18 mm bore.
        build, report, export = export_rom_geometry(ROMModelSpec(regions=[self.inverse_region()], engine_axis=EngineAxis()))
        self.assertEqual(report.errors, [])
        bore_cell_volume = math.pi * 18.0 ** 2 * 20.0 * 1.0e-9
        for exported_cell in export["cells"]:
            self.assertLess(abs(exported_cell["volume"] - bore_cell_volume), 1.0e-9 * bore_cell_volume)
        self.assertTrue(export["regions"][0]["inverted"])
        self.assertFalse(any("source body" in warning for warning in report.warnings))

    def test_small_outer_void_is_dropped_or_kept(self):
        # RMax = 26 mm also takes in the r 24..26 mm ring outside the housing.
        bore_volume = math.pi * 18.0 ** 2 * 60.0
        outer_ring_volume = math.pi * (26.0 ** 2 - 24.0 ** 2) * 60.0

        kept_largest = build_region_geometry(self.inverse_region(r_max=26.0), EngineAxis(), 0.01)
        self.assertLess(abs(kept_largest.region_solid.Volume - bore_volume), 1.0e-9 * bore_volume)
        self.assertTrue(any("dropped 1 smaller void" in warning for warning in kept_largest.warnings))

        kept_all = build_region_geometry(self.inverse_region(r_max=26.0, keep_largest_piece=False), EngineAxis(), 0.01)
        self.assertLess(abs(kept_all.region_solid.Volume - (bore_volume + outer_ring_volume)), 1.0e-9 * bore_volume)
        self.assertTrue(any("disconnected" in warning for warning in kept_all.warnings))

    def test_fully_filled_inverse_region_is_an_error(self):
        solid_rod = Part.makeCylinder(24.0, 60.0)
        filled_region = RegionSpec("nothing", "air", ["rod"], [solid_rod], 0.0, 60.0, invert=True)
        region_geometry = build_region_geometry(filled_region, EngineAxis(), 0.01)
        self.assertTrue(any("sources fill all" in error for error in region_geometry.errors))


def write_expected_export():
    with tempfile.TemporaryDirectory() as temporary_directory:
        fixture_path = build_fixture.build_fixture_document(temporary_directory)
        App.closeDocument(build_fixture.FIXTURE_DOCUMENT_NAME)
        doc = App.openDocument(fixture_path)
        doc.recompute()
        model_spec = build_fixture.fixture_model_spec(doc)
        model_spec.source_document = "tests/rom_fixture.FCStd (built by tests/build_fixture.py)"
        os.makedirs(os.path.dirname(EXPECTED_EXPORT_PATH), exist_ok=True)
        export_rom_geometry(model_spec, EXPECTED_EXPORT_PATH)
        App.closeDocument(doc.Name)
    print(f"wrote {EXPECTED_EXPORT_PATH}")


if __name__ == "__main__":
    if "--write-expected" in sys.argv:
        write_expected_export()
        sys.exit(0)
    unittest.main(verbosity=2)

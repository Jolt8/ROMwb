"""Parametric helper solids that a region can use as sources:

  ROMCavity        fluid volume = enclosing solid (or an axial cylinder)
                   minus solid parts. Fluid regions (fluid_domain,
                   pre_chamber, ...) are empty space, not CAD bodies.
  ROMFastenerPart  one side of a fastener that crosses from one body into
                   another, cut on the bodies' contact plane.

Both recompute when the CAD they link to changes. Their Shape is in global
coordinates, so keep them at the top level of the document (not inside an
App::Part container).
"""

import FreeCAD as App
import Part

from rom_geometry.core.axis_geometry import EngineAxis
from rom_geometry.core.regions import make_cavity_solid
from rom_geometry.core.source_shapes import split_shape_between_bodies
from rom_geometry.objects.object_helpers import global_shape, report_to_console


class ROMCavity:
    Type = "ROMCavity"

    def __init__(self, obj):
        obj.Proxy = self
        obj.addProperty("App::PropertyXLink", "Enclosure", "Cavity", "Enclosing solid. Empty = an axial cylinder from CylinderRadius, ZMin, ZMax.")
        obj.addProperty("App::PropertyXLinkList", "SubtractedShapes", "Cavity", "Solid parts removed from the enclosure.")
        obj.addProperty("App::PropertyXLink", "Axis", "Cavity", "LCS for the cylinder enclosure. Empty = global Z.")
        obj.addProperty("App::PropertyLength", "CylinderRadius", "Cavity", "Radius of the cylinder enclosure.")
        obj.addProperty("App::PropertyDistance", "ZMin", "Cavity", "Lower end of the cylinder enclosure. Accepts expressions.")
        obj.addProperty("App::PropertyDistance", "ZMax", "Cavity", "Upper end of the cylinder enclosure. Accepts expressions.")
        obj.addProperty("App::PropertyBool", "KeepOnlyLargestSolid", "Cavity", "Drop small leftover voids (seal grooves, clearances) and keep the main cavity.")
        obj.addProperty("App::PropertyLength", "FuzzyTolerance", "Cavity", "Fuzzy tolerance for the cut.")
        obj.addProperty("App::PropertyString", "Status", "Cavity", "Result of the last recompute.")
        obj.KeepOnlyLargestSolid = True
        obj.FuzzyTolerance = 0.01
        obj.setEditorMode("Status", 1)

    def execute(self, obj):
        if obj.Enclosure is not None:
            enclosing_shape = global_shape(obj.Enclosure)
        else:
            if obj.Axis is not None:
                engine_axis = EngineAxis.from_placement(obj.Axis.getGlobalPlacement())
            else:
                engine_axis = EngineAxis()
            if obj.CylinderRadius.Value <= 0 or obj.ZMax.Value <= obj.ZMin.Value:
                obj.Status = "set Enclosure, or CylinderRadius > 0 and ZMax > ZMin"
                obj.Shape = Part.Shape()
                report_to_console(f"ROMCavity '{obj.Label}': {obj.Status}", True)
                return
            enclosing_shape = engine_axis.make_axial_cylinder(obj.CylinderRadius.Value, obj.ZMin.Value, obj.ZMax.Value)

        subtracted_shapes = [global_shape(subtracted_object) for subtracted_object in obj.SubtractedShapes]

        try:
            cavity_shape = make_cavity_solid(enclosing_shape, subtracted_shapes, obj.FuzzyTolerance.Value)
        except ValueError as cavity_error:
            obj.Status = str(cavity_error)
            obj.Shape = Part.Shape()
            report_to_console(f"ROMCavity '{obj.Label}': {obj.Status}", True)
            return

        cavity_solids = list(cavity_shape.Solids)
        if obj.KeepOnlyLargestSolid and len(cavity_solids) > 1:
            largest_solid = max(cavity_solids, key=lambda solid: solid.Volume)
            dropped_volume = cavity_shape.Volume - largest_solid.Volume
            obj.Shape = largest_solid
            obj.Status = f"{largest_solid.Volume:.6g} mm^3; dropped {len(cavity_solids) - 1} small void(s) totalling {dropped_volume:.6g} mm^3"
        else:
            obj.Shape = cavity_shape
            obj.Status = f"{cavity_shape.Volume:.6g} mm^3 in {len(cavity_solids)} solid(s)"

    def dumps(self):
        return None

    def loads(self, state):
        return None


class ROMFastenerPart:
    Type = "ROMFastenerPart"

    def __init__(self, obj):
        obj.Proxy = self
        obj.addProperty("App::PropertyXLink", "Fastener", "Fastener split", "Fastener (bolts, screws, pin, seal) crossing from BodyA into BodyB.")
        obj.addProperty("App::PropertyXLink", "BodyA", "Fastener split", "First body.")
        obj.addProperty("App::PropertyXLink", "BodyB", "Fastener split", "Second body.")
        obj.addProperty("App::PropertyEnumeration", "Side", "Fastener split", "Which body's side of the cut this object keeps.")
        obj.addProperty("App::PropertyBool", "UseCustomPlane", "Fastener split", "Cut on PlanePoint / PlaneNormal instead of the bodies' contact plane.")
        obj.addProperty("App::PropertyVector", "PlanePoint", "Fastener split", "Point on the custom cut plane (mm).")
        obj.addProperty("App::PropertyVector", "PlaneNormal", "Fastener split", "Normal of the custom cut plane.")
        obj.addProperty("App::PropertyLength", "FuzzyTolerance", "Fastener split", "Fuzzy tolerance for finding the contact.")
        obj.addProperty("App::PropertyString", "Status", "Fastener split", "Result of the last recompute.")
        obj.Side = ["BodyA", "BodyB"]
        obj.PlaneNormal = App.Vector(0, 0, 1)
        obj.FuzzyTolerance = 0.01
        obj.setEditorMode("Status", 1)

    def execute(self, obj):
        if obj.Fastener is None or obj.BodyA is None or obj.BodyB is None:
            obj.Status = "set Fastener, BodyA and BodyB"
            obj.Shape = Part.Shape()
            return

        if obj.UseCustomPlane:
            plane_point = obj.PlanePoint
            plane_normal = obj.PlaneNormal
        else:
            plane_point = None
            plane_normal = None

        try:
            shape_split = split_shape_between_bodies(global_shape(obj.Fastener), global_shape(obj.BodyA), global_shape(obj.BodyB), obj.FuzzyTolerance.Value, plane_point, plane_normal)
        except ValueError as split_error:
            obj.Status = str(split_error)
            obj.Shape = Part.Shape()
            report_to_console(f"ROMFastenerPart '{obj.Label}': {obj.Status}", True)
            return

        if obj.Side == "BodyA":
            kept_part = shape_split.part_for_body_a
        else:
            kept_part = shape_split.part_for_body_b

        obj.Shape = kept_part
        plane_text = f"cut plane through ({shape_split.plane_point.x:.4g}, {shape_split.plane_point.y:.4g}, {shape_split.plane_point.z:.4g}) mm, normal ({shape_split.plane_normal.x:.3g}, {shape_split.plane_normal.y:.3g}, {shape_split.plane_normal.z:.3g})"
        obj.Status = f"{kept_part.Volume:.6g} mm^3; {plane_text}; " + "; ".join(shape_split.notes)

    def dumps(self):
        return None

    def loads(self, state):
        return None


def create_cavity(doc, subtracted_objects, label="cavity", engine_axis=None):
    """Cavity with a cylinder enclosure sized to the subtracted parts:
    radius = their largest radius from the axis, z-range = their extent."""
    cavity_object = doc.addObject("Part::FeaturePython", "ROMCavity")
    ROMCavity(cavity_object)
    cavity_object.Label = label
    cavity_object.SubtractedShapes = list(subtracted_objects)

    if engine_axis is None:
        engine_axis = EngineAxis()

    largest_radius = 0.0
    axial_coordinates = []
    for subtracted_object in subtracted_objects:
        subtracted_shape = global_shape(subtracted_object)
        for vertex in subtracted_shape.Vertexes:
            largest_radius = max(largest_radius, engine_axis.radial_distance(vertex.Point))
        for edge in subtracted_shape.Edges:
            for edge_point in edge.discretize(32):
                largest_radius = max(largest_radius, engine_axis.radial_distance(edge_point))
        for corner_index in range(8):
            axial_coordinates.append(engine_axis.axial_coordinate(subtracted_shape.BoundBox.getPoint(corner_index)))

    if axial_coordinates:
        cavity_object.CylinderRadius = largest_radius
        cavity_object.ZMin = min(axial_coordinates)
        cavity_object.ZMax = max(axial_coordinates)
    return cavity_object


def create_fastener_parts(doc, fastener_object, body_a_object, body_b_object):
    """Two ROMFastenerPart objects, one per side. Returns (part_a, part_b)."""
    fastener_parts = []
    for side, body_object in (("BodyA", body_a_object), ("BodyB", body_b_object)):
        fastener_part = doc.addObject("Part::FeaturePython", "ROMFastenerPart")
        ROMFastenerPart(fastener_part)
        fastener_part.Label = f"{fastener_object.Label}__{body_object.Label}_side"
        fastener_part.Fastener = fastener_object
        fastener_part.BodyA = body_a_object
        fastener_part.BodyB = body_b_object
        fastener_part.Side = side
        fastener_parts.append(fastener_part)
    return fastener_parts[0], fastener_parts[1]

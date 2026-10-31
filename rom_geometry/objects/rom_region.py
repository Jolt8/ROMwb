"""ROMRegion: source solids cut to a z-range (optional r-range), split into
N equal axial cells. Its Shape is the compound of its cells, so the 3D view
shows the region with its cell boundaries.

ZMin / ZMax / RMin / RMax accept expressions, including spreadsheet aliases
in another document, e.g. `table_hybrid#Spreadsheet.pre_combustion_chamber_length`
(the document holding the region must have been saved once first).
"""

import FreeCAD as App
import Part

from rom_geometry.core.regions import RegionSpec, build_region_geometry
from rom_geometry.objects.object_helpers import global_shape, length_property_text, report_to_console
from rom_geometry.objects.rom_model import engine_axis_of_model, rom_model_of


DEFAULT_FUZZY_TOLERANCE = 0.01


class ROMRegion:
    Type = "ROMRegion"

    def __init__(self, obj):
        obj.Proxy = self

        obj.addProperty("App::PropertyString", "RegionName", "Region", "Name the Julia model uses for this region (cell set name). Must be unique.")
        obj.addProperty("App::PropertyString", "Material", "Region", "Free text, e.g. aluminum_6061.")
        obj.addProperty("App::PropertyXLinkList", "SourceShapes", "Region", "Bodies, links, solids, cavity solids or fastener halves. They are fused.")
        obj.addProperty("App::PropertyDistance", "ZMin", "Region", "Lower axial limit along the model axis. Accepts expressions.")
        obj.addProperty("App::PropertyDistance", "ZMax", "Region", "Upper axial limit along the model axis. Accepts expressions.")
        obj.addProperty("App::PropertyLength", "RMin", "Region", "Inner radial limit. 0 = unbounded.")
        obj.addProperty("App::PropertyLength", "RMax", "Region", "Outer radial limit. 0 = unbounded.")
        obj.addProperty("App::PropertyInteger", "NAxialCells", "Region", "Number of equal-length axial cells, numbered in increasing z.")
        obj.addProperty("App::PropertyColor", "Color", "Region", "Preview colour.")
        obj.addProperty("App::PropertyString", "Status", "Region", "Result of the last recompute.")

        obj.NAxialCells = 1
        obj.Color = (0.2, 0.6, 1.0)
        obj.setEditorMode("Status", 1)

    def execute(self, obj):
        region_spec = region_spec_from_object(obj)
        model_object = rom_model_of(obj)
        engine_axis = engine_axis_of_model(model_object)
        if model_object is not None:
            fuzzy_tolerance = model_object.FuzzyTolerance.Value
        else:
            fuzzy_tolerance = DEFAULT_FUZZY_TOLERANCE

        if model_object is None:
            report_to_console(f"ROMRegion '{obj.Label}' is not inside a ROMModel; using the global Z axis", True)

        region_geometry = build_region_geometry(region_spec, engine_axis, fuzzy_tolerance)

        if region_geometry.errors:
            obj.Shape = Part.Shape()
            obj.Status = "; ".join(region_geometry.errors)
            report_to_console(f"ROMRegion '{obj.Label}': {obj.Status}", True)
            return

        obj.Shape = Part.makeCompound(region_geometry.cell_solids)

        region_volume = region_geometry.region_solid.Volume
        status_text = f"{len(region_geometry.cell_solids)} cell(s), {region_volume:.6g} mm^3"
        if region_geometry.warnings:
            status_text += "; " + "; ".join(region_geometry.warnings)
            report_to_console(f"ROMRegion '{obj.Label}': {status_text}", True)
        obj.Status = status_text

    def dumps(self):
        return None

    def loads(self, state):
        return None


def region_spec_from_object(region_object):
    source_labels = []
    source_shapes = []
    for source_object in region_object.SourceShapes:
        source_labels.append(source_object.Label)
        source_shapes.append(global_shape(source_object))

    region_name = region_object.RegionName or region_object.Label

    return RegionSpec(
        name=region_name,
        material=region_object.Material,
        source_labels=source_labels,
        source_shapes=source_shapes,
        z_min=region_object.ZMin.Value,
        z_max=region_object.ZMax.Value,
        r_min=region_object.RMin.Value,
        r_max=region_object.RMax.Value,
        n_axial_cells=region_object.NAxialCells,
        z_min_expression=length_property_text(region_object, "ZMin"),
        z_max_expression=length_property_text(region_object, "ZMax"),
    )


def create_rom_region(model_object, region_name, source_objects, z_min=None, z_max=None):
    """Add a region to the end of the model's group. Without z-limits, the
    sources' extent along the model axis is used."""
    doc = model_object.Document
    region_object = doc.addObject("Part::FeaturePython", "ROMRegion")
    ROMRegion(region_object)
    region_object.Label = region_name
    region_object.RegionName = region_name
    region_object.SourceShapes = list(source_objects)

    # Default limits: the extent of the sources' bounding-box corners along
    # the axis (exact for the global Z axis, slightly generous for a tilted one).
    if (z_min is None or z_max is None) and len(source_objects) > 0:
        engine_axis = engine_axis_of_model(model_object)
        axial_coordinates = []
        for source_object in source_objects:
            source_bounding_box = global_shape(source_object).BoundBox
            for corner_index in range(8):
                axial_coordinates.append(engine_axis.axial_coordinate(source_bounding_box.getPoint(corner_index)))
        if z_min is None:
            z_min = min(axial_coordinates)
        if z_max is None:
            z_max = max(axial_coordinates)

    if z_min is not None:
        region_object.ZMin = z_min
    if z_max is not None:
        region_object.ZMax = z_max

    model_object.addObject(region_object)
    return region_object

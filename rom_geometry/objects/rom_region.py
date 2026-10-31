"""ROMRegion: source solids cut to a z-range (optional r-range), split into
N equal axial cells. Its Shape is the compound of its cells, so the 3D view
shows the region with its cell boundaries.

With Inverse = True the region is the empty space inside its limits that the
sources do not fill: a fluid region (fluid_domain, pre_chamber, ...) made by
picking the solids around it.

ZMin / ZMax / RMin / RMax can each come from:
  - a value,
  - an expression, including spreadsheet aliases in another document, e.g.
    `table_hybrid#Spreadsheet.pre_combustion_chamber_length` (the document
    holding the region must have been saved once first), or
  - a reference (ZMinReference, ...): an LCS (e.g. an Assembly4 LCS picked
    through the assembly's link), a vertex, a planar face (z), a coaxial
    cylindrical face (r) or a coaxial circular edge, plus an offset.
    A reference wins over a value or an expression.
"""

import FreeCAD as App
import Part

from rom_geometry.core.regions import RegionSpec, build_region_geometry
from rom_geometry.objects.object_helpers import global_shape, length_property_text, reference_element_kind, reference_position, report_to_console
from rom_geometry.objects.rom_model import engine_axis_of_model, rom_model_of


DEFAULT_FUZZY_TOLERANCE = 0.01

# (limit property, which coordinate of the reference it takes)
REFERENCED_LIMITS = (("ZMin", "axial"), ("ZMax", "axial"), ("RMin", "radial"), ("RMax", "radial"))


def add_missing_properties(obj):
    """Properties added after the first release. Kept in one function so
    documents saved by an older version get them when reopened."""
    for limit_name, coordinate_kind in REFERENCED_LIMITS:
        reference_property = f"{limit_name}Reference"
        offset_property = f"{limit_name}Offset"
        if reference_property not in obj.PropertiesList:
            obj.addProperty("App::PropertyXLinkSub", reference_property, "Region limits from references", f"LCS, vertex or face that sets {limit_name} ({coordinate_kind} position). Overrides the value / expression of {limit_name}.")
        if offset_property not in obj.PropertiesList:
            obj.addProperty("App::PropertyDistance", offset_property, "Region limits from references", f"Added to the {coordinate_kind} position of {reference_property}. Accepts expressions.")

    # Kind of element each reference pointed at when picked ("Edge:Circle",
    # "Face:Plane", ...). If a CAD edit renumbers the elements so that the
    # stored name (e.g. "Edge12") now points at a different kind of element,
    # the region stops with an error instead of using the wrong geometry.
    if "ReferenceElementKinds" not in obj.PropertiesList:
        obj.addProperty("App::PropertyMap", "ReferenceElementKinds", "Region limits from references", "Element kind of each reference when it was picked (internal check).")
        obj.setEditorMode("ReferenceElementKinds", 2)

    if "Inverse" not in obj.PropertiesList:
        obj.addProperty("App::PropertyBool", "Inverse", "Region", "Fluid / cavity region: the EMPTY space inside the limits that the sources do not fill. Without RMax, the outer limit is the sources' largest radius.")
    if "KeepOnlyLargestPiece" not in obj.PropertiesList:
        obj.addProperty("App::PropertyBool", "KeepOnlyLargestPiece", "Region", "Inverse regions only: keep the main empty space and drop small voids (seal grooves, gaps outside flanges).")
        obj.KeepOnlyLargestPiece = True


class ROMRegion:
    Type = "ROMRegion"

    def __init__(self, obj):
        obj.Proxy = self

        obj.addProperty("App::PropertyString", "RegionName", "Region", "Name the Julia model uses for this region (cell set name). Must be unique.")
        obj.addProperty("App::PropertyString", "Material", "Region", "Free text, e.g. aluminum_6061.")
        obj.addProperty("App::PropertyXLinkList", "SourceShapes", "Region", "Bodies, links, solids, cavity solids or fastener halves. They are fused.")
        obj.addProperty("App::PropertyDistance", "ZMin", "Region", "Lower axial limit along the model axis. Accepts expressions; set by ZMinReference if that is used.")
        obj.addProperty("App::PropertyDistance", "ZMax", "Region", "Upper axial limit along the model axis. Accepts expressions; set by ZMaxReference if that is used.")
        obj.addProperty("App::PropertyLength", "RMin", "Region", "Inner radial limit. 0 = unbounded. Set by RMinReference if that is used.")
        obj.addProperty("App::PropertyLength", "RMax", "Region", "Outer radial limit. 0 = unbounded. Set by RMaxReference if that is used.")
        obj.addProperty("App::PropertyInteger", "NAxialCells", "Region", "Number of equal-length axial cells, numbered in increasing z.")
        obj.addProperty("App::PropertyColor", "Color", "Region", "Preview colour.")
        obj.addProperty("App::PropertyString", "Status", "Region", "Result of the last recompute.")
        add_missing_properties(obj)

        obj.NAxialCells = 1
        obj.Color = (0.2, 0.6, 1.0)
        obj.setEditorMode("Status", 1)

    def onDocumentRestored(self, obj):
        add_missing_properties(obj)

    def onChanged(self, obj, property_name):
        # A newly picked (or cleared) reference starts a new element-kind record.
        if property_name.endswith("Reference") and "Restore" not in obj.State and "ReferenceElementKinds" in obj.PropertiesList:
            limit_name = property_name[:-len("Reference")]
            element_kinds = dict(obj.ReferenceElementKinds)
            if limit_name in element_kinds:
                del element_kinds[limit_name]
                obj.ReferenceElementKinds = element_kinds

    def execute(self, obj):
        model_object = rom_model_of(obj)
        engine_axis = engine_axis_of_model(model_object)
        if model_object is not None:
            fuzzy_tolerance = model_object.FuzzyTolerance.Value
        else:
            fuzzy_tolerance = DEFAULT_FUZZY_TOLERANCE

        if model_object is None:
            report_to_console(f"ROMRegion '{obj.Label}' is not inside a ROMModel; using the global Z axis", True)

        # Limits from references (LCS / faces): position + offset, written
        # into ZMin ... RMax so the property view shows the value in use.
        reference_errors = apply_limit_references(obj, engine_axis)
        if reference_errors:
            obj.Shape = Part.Shape()
            obj.Status = "; ".join(reference_errors)
            report_to_console(f"ROMRegion '{obj.Label}': {obj.Status}", True)
            return

        region_spec = region_spec_from_object(obj)
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


def apply_limit_references(region_object, engine_axis):
    """Set each referenced limit to (reference position + offset). Returns errors."""
    reference_errors = []
    element_kinds = dict(region_object.ReferenceElementKinds)
    element_kinds_changed = False

    for limit_name, coordinate_kind in REFERENCED_LIMITS:
        reference_value = getattr(region_object, f"{limit_name}Reference", None)
        if reference_value is None or reference_value[0] is None:
            continue

        top_object, sub_names = reference_value
        reference_label = f"{top_object.Label}.{sub_names[0] if sub_names else ''}".rstrip(".")

        # Same element name, different kind of element: the CAD was edited
        # and the name now points somewhere else. Moving is fine; changing
        # kind is not. Checked before anything else uses the element.
        try:
            current_element_kind = reference_element_kind(reference_value)
        except Exception as reference_error:
            reference_errors.append(f"{limit_name}Reference: {reference_error}")
            continue
        picked_element_kind = element_kinds.get(limit_name)
        if picked_element_kind is None:
            element_kinds[limit_name] = current_element_kind
            element_kinds_changed = True
        elif picked_element_kind != current_element_kind:
            reference_errors.append(f"{limit_name}Reference: {reference_label} was a {picked_element_kind} when picked but is now a {current_element_kind}; a CAD edit renumbered its elements, please re-pick it")
            continue

        try:
            axial_coordinate, radial_distance, reference_description = reference_position(reference_value, engine_axis)
        except Exception as reference_error:
            reference_errors.append(f"{limit_name}Reference: {reference_error}")
            continue

        if coordinate_kind == "axial":
            reference_coordinate = axial_coordinate
        else:
            reference_coordinate = radial_distance
        if reference_coordinate is None:
            reference_errors.append(f"{limit_name}Reference: {reference_description} gives no {coordinate_kind} position (pick an LCS, vertex, or a {'planar face' if coordinate_kind == 'axial' else 'coaxial cylindrical face'})")
            continue

        limit_offset = getattr(region_object, f"{limit_name}Offset").Value
        limit_value = reference_coordinate + limit_offset
        if coordinate_kind == "radial" and limit_value < 0:
            reference_errors.append(f"{limit_name}Reference: radius + offset is negative ({limit_value:.6g} mm)")
            continue

        # A reference wins: drop any expression that would overwrite it.
        for bound_property_name, expression in region_object.ExpressionEngine:
            if bound_property_name == limit_name:
                region_object.setExpression(limit_name, None)
        if abs(getattr(region_object, limit_name).Value - limit_value) > 1.0e-12:
            setattr(region_object, limit_name, limit_value)

    if element_kinds_changed:
        region_object.ReferenceElementKinds = element_kinds
    return reference_errors


def limit_definition_text(region_object, limit_name):
    """How a limit was defined, for the export: reference (+ offset),
    expression, or value in mm."""
    reference_value = getattr(region_object, f"{limit_name}Reference", None)
    if reference_value is not None and reference_value[0] is not None:
        top_object, sub_names = reference_value
        sub_name = sub_names[0] if sub_names else ""
        reference_text = f"reference {top_object.Document.Name}#{top_object.Label}.{sub_name}".rstrip(".")
        limit_offset = getattr(region_object, f"{limit_name}Offset").Value
        if limit_offset != 0:
            reference_text += f" {'+' if limit_offset > 0 else '-'} {abs(limit_offset):.12g} mm"
        return reference_text
    return length_property_text(region_object, limit_name)


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
        z_min_expression=limit_definition_text(region_object, "ZMin"),
        z_max_expression=limit_definition_text(region_object, "ZMax"),
        invert=region_object.Inverse,
        keep_largest_piece=region_object.KeepOnlyLargestPiece,
    )


def default_region_name(model_object, source_objects):
    """"<first source label>_region" (e.g. aluminum_housing_region), made
    unique within the model with _2, _3, ... The "_region" suffix keeps the
    name distinct from the part's own label, so FreeCAD doesn't append 001."""
    if len(source_objects) > 0:
        base_name = f"{source_objects[0].Label}_region"
    else:
        base_name = "region"

    existing_region_names = set()
    for child in model_object.Group:
        if hasattr(child, "RegionName"):
            existing_region_names.add(child.RegionName)

    region_name = base_name
    duplicate_number = 2
    while region_name in existing_region_names:
        region_name = f"{base_name}_{duplicate_number}"
        duplicate_number += 1
    return region_name


def create_rom_region(model_object, region_name, source_objects, z_min=None, z_max=None, inverse=False):
    """Add a region to the end of the model's group. Without z-limits, the
    sources' extent along the model axis is used. region_name=None gives
    the default "<part>_region" name."""
    doc = model_object.Document
    if region_name is None:
        region_name = default_region_name(model_object, source_objects)
    region_object = doc.addObject("Part::FeaturePython", "ROMRegion")
    ROMRegion(region_object)
    region_object.Label = region_name
    region_object.RegionName = region_name
    region_object.SourceShapes = list(source_objects)
    region_object.Inverse = inverse
    if inverse:
        region_object.Color = (0.55, 0.85, 1.0)

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

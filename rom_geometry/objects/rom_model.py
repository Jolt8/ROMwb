"""ROMModel: one per document, a group holding the regions in order.

The ORDER of the regions inside the group is meaningful: it sets cell
numbering and which region's half-faces an interface face set holds.
"""

import FreeCAD as App

from rom_geometry.core.axis_geometry import EngineAxis
from rom_geometry.core.distances import RADIAL_NODE_RULES
from rom_geometry.core.model_build import ROMModelSpec
from rom_geometry.objects.object_helpers import object_type


# Changing any of these changes every region's cells, so the regions are
# marked for recompute.
PROPERTIES_THAT_CHANGE_REGIONS = ("Axis", "FuzzyTolerance")


class ROMModel:
    Type = "ROMModel"

    def __init__(self, obj):
        obj.Proxy = self

        obj.addProperty("App::PropertyXLink", "Axis", "ROM Model", "Local coordinate system whose Z axis is the engine axis. Empty = global Z through the origin.")
        obj.addProperty("App::PropertyXLinkList", "ParameterSheets", "ROM Model", "Spreadsheets whose aliases are exported as parameters (e.g. table_hybrid).")
        obj.addProperty("App::PropertyFile", "ExportPath", "ROM Model", "Where Export writes the JSON file.")
        obj.addProperty("App::PropertyLength", "FuzzyTolerance", "ROM Model", "Fuzzy tolerance for the boolean operations.")
        obj.addProperty("App::PropertyLength", "NearContactTolerance", "ROM Model", "Gaps smaller than this between regions are reported as near-contacts.")
        obj.addProperty("App::PropertyStringList", "IgnoredInterfaces", "ROM Model", "Region pairs 'region_a|region_b' whose shared faces are exported as unpatched boundaries.")
        obj.addProperty("App::PropertyEnumeration", "RadialNodeRule", "ROM Model", "Radial node r_c of each cell for cylindrical conduction distances.")

        obj.FuzzyTolerance = 0.01
        obj.NearContactTolerance = 0.5
        obj.RadialNodeRule = list(RADIAL_NODE_RULES)
        obj.RadialNodeRule = "geometric_mean"

    def onChanged(self, obj, property_name):
        if "Restore" in obj.State:
            return
        if property_name in PROPERTIES_THAT_CHANGE_REGIONS:
            for region_object in rom_regions_of_model(obj):
                region_object.touch()
            for patch_object in rom_patches_of_model(obj):
                patch_object.touch()

    def execute(self, obj):
        pass

    def dumps(self):
        return None

    def loads(self, state):
        return None


def create_rom_model(doc, label="ROMModel"):
    model_object = doc.addObject("App::DocumentObjectGroupPython", "ROMModel")
    ROMModel(model_object)
    model_object.Label = label
    return model_object


def find_rom_models(doc):
    return [document_object for document_object in doc.Objects if object_type(document_object) == "ROMModel"]


def rom_model_of(document_object):
    """The ROMModel group a region (or other ROM object) belongs to, or None."""
    parent_group = document_object.getParentGroup()
    if parent_group is not None and object_type(parent_group) == "ROMModel":
        return parent_group
    return None


def rom_regions_of_model(model_object):
    """Regions in group order; that order is the cell-numbering order."""
    return [child for child in model_object.Group if object_type(child) == "ROMRegion"]


def rom_patches_of_model(model_object):
    return [child for child in model_object.Group if object_type(child) == "ROMBoundaryPatch"]


def engine_axis_of_model(model_object):
    """Engine axis from the linked LCS (its local Z), else global Z."""
    if model_object is None or model_object.Axis is None:
        return EngineAxis()
    return EngineAxis.from_placement(model_object.Axis.getGlobalPlacement())


def validate_rom_model(model_object, progress_callback=None):
    """Recompute, build and validate. Returns (build, report)."""
    from rom_geometry.core.model_build import build_rom_geometry
    from rom_geometry.core.validation import validate_rom_geometry

    model_object.Document.recompute()
    build = build_rom_geometry(model_spec_from_rom_model(model_object), progress_callback)
    report = validate_rom_geometry(build)
    return build, report


def apply_n_axial_cells_overrides(model_object, n_axial_cells_overrides):
    """Set NAxialCells per region name ("*" = every region). The document is
    changed in memory only; save it yourself if you want to keep the counts."""
    regions_by_name = {}
    for region_object in rom_regions_of_model(model_object):
        regions_by_name[region_object.RegionName or region_object.Label] = region_object

    for region_name, n_axial_cells in n_axial_cells_overrides.items():
        if n_axial_cells < 1:
            raise ValueError(f"n_axial_cells for '{region_name}' must be at least 1 (got {n_axial_cells})")
        if region_name == "*":
            target_regions = list(regions_by_name.values())
        elif region_name in regions_by_name:
            target_regions = [regions_by_name[region_name]]
        else:
            raise ValueError(f"no region named '{region_name}' (regions: {', '.join(regions_by_name)})")
        for region_object in target_regions:
            region_object.NAxialCells = n_axial_cells


def export_rom_model(model_object, output_path=None, n_axial_cells_overrides=None, progress_callback=None):
    """Recompute, validate and write the JSON (to ExportPath by default).

    n_axial_cells_overrides: optional {region_name: n} ("*" = all regions),
    applied before the export (in memory, not saved). A "*" entry is applied
    first, so {"*": 2, "fuel_grain": 10} works as expected.
    Raises ExportRefusedError when validation finds errors."""
    from rom_geometry.core.export_json import export_rom_geometry

    if output_path is None:
        output_path = model_object.ExportPath
    if not output_path:
        raise ValueError(f"ROMModel '{model_object.Label}' has no ExportPath")

    if n_axial_cells_overrides:
        ordered_overrides = {}
        if "*" in n_axial_cells_overrides:
            ordered_overrides["*"] = n_axial_cells_overrides["*"]
        for region_name, n_axial_cells in n_axial_cells_overrides.items():
            if region_name != "*":
                ordered_overrides[region_name] = n_axial_cells
        apply_n_axial_cells_overrides(model_object, ordered_overrides)

    model_object.Document.recompute()
    return export_rom_geometry(model_spec_from_rom_model(model_object), output_path, progress_callback)


def model_spec_from_rom_model(model_object):
    """Collect everything the core needs from the document objects."""
    from rom_geometry.objects.rom_boundary_patch import boundary_patch_spec_from_object
    from rom_geometry.objects.rom_region import region_spec_from_object

    region_specs = []
    for region_object in rom_regions_of_model(model_object):
        region_specs.append(region_spec_from_object(region_object))

    boundary_patch_specs = []
    for patch_object in rom_patches_of_model(model_object):
        boundary_patch_specs.append(boundary_patch_spec_from_object(patch_object, model_object.FuzzyTolerance.Value))

    source_document_path = model_object.Document.FileName.replace("\\", "/")

    return ROMModelSpec(
        regions=region_specs,
        boundary_patches=boundary_patch_specs,
        engine_axis=engine_axis_of_model(model_object),
        fuzzy_tolerance=model_object.FuzzyTolerance.Value,
        near_contact_tolerance=model_object.NearContactTolerance.Value,
        ignored_interfaces=list(model_object.IgnoredInterfaces),
        radial_node_rule=model_object.RadialNodeRule,
        parameter_sheets=list(model_object.ParameterSheets),
        source_document=source_document_path,
    )

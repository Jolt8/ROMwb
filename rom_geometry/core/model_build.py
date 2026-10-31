"""Build the whole ROM geometry from a model spec, in the order the physics
needs it:

  regions -> cells -> one generalFuse -> sub-faces -> patch assignment
  -> merged half-faces -> neighbour links -> half-cell distances
  -> cell sets / face sets -> overlaps and near-contacts -> parameters

Everything stays in FreeCAD units (mm) here. export_json.py converts to SI.
"""

from dataclasses import dataclass, field

from rom_geometry.core.axis_geometry import EngineAxis, outward_face_samples, shape_volume_and_centroid
from rom_geometry.core.boundary_patches import assign_boundary_sub_faces_to_patches
from rom_geometry.core.distances import cell_radial_extent, compute_cell_face_distances
from rom_geometry.core.interfaces import CellRecord, build_cell_faces, connect_interface_half_faces, find_near_contacts, split_cells_into_sub_faces
from rom_geometry.core.parameters import read_spreadsheet_parameters
from rom_geometry.core.regions import build_region_geometry


@dataclass
class ROMModelSpec:
    """Everything the export needs. The order of `regions` is meaningful: it
    sets cell numbering and which side of an interface its face set holds."""
    regions: list
    boundary_patches: list = field(default_factory=list)
    engine_axis: EngineAxis = field(default_factory=EngineAxis)
    fuzzy_tolerance: float = 0.01
    near_contact_tolerance: float = 0.5
    ignored_interfaces: list = field(default_factory=list)
    radial_node_rule: str = "geometric_mean"
    parameter_sheets: list = field(default_factory=list)
    source_document: str = ""


@dataclass
class ROMGeometryBuild:
    model_spec: ROMModelSpec
    region_geometries: list = field(default_factory=list)
    cells: list = field(default_factory=list)
    sub_faces: list = field(default_factory=list)
    cellsets: dict = field(default_factory=dict)
    facetsets: dict = field(default_factory=dict)
    unassigned_boundary_faces: list = field(default_factory=list)
    near_contacts: list = field(default_factory=list)
    overlaps: list = field(default_factory=list)
    parameters: dict = field(default_factory=dict)
    refused_merges: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


@dataclass
class InterfaceSummary:
    """One region pair's shared surface, for the interface review panel."""
    region_a: str
    region_b: str
    area: float
    faces: list
    ignored: bool


@dataclass
class UnassignedSurfaceSummary:
    """A region's boundary area that no patch claims (adiabatic unless patched)."""
    region: str
    area: float
    faces: list


def summarize_for_review(build):
    """Region-pair interfaces (detected and ignored), near-contacts and
    unassigned outer surfaces, with the actual faces so the GUI can
    highlight them. Areas in mm^2.

    Each interface is counted once, from the side of the region that comes
    first in the model order. Axial faces inside one region are not listed:
    they are not a modelling choice.
    """
    cell_by_id = {cell.cell_id: cell for cell in build.cells}
    interface_summaries = {}
    unassigned_summaries = {}

    for cell in build.cells:
        for cell_face in cell.faces:
            sub_face_shapes = [build.sub_faces[sub_face_id].face for sub_face_id in cell_face.sub_face_ids]

            if cell_face.kind in ("interface", "ignored_interface"):
                if cell_face.kind == "interface":
                    neighbor_cell = cell_by_id[cell_face.neighbor_cell]
                else:
                    neighbor_cell = cell_by_id[cell_face.ignored_neighbor_cell]
                if neighbor_cell.region_name == cell.region_name:
                    continue
                if cell.region_order > neighbor_cell.region_order:
                    continue

                region_pair = (cell.region_name, neighbor_cell.region_name)
                if region_pair not in interface_summaries:
                    interface_summaries[region_pair] = InterfaceSummary(cell.region_name, neighbor_cell.region_name, 0.0, [], cell_face.kind == "ignored_interface")
                interface_summaries[region_pair].area += cell_face.area
                interface_summaries[region_pair].faces.extend(sub_face_shapes)

            elif cell_face.kind == "boundary" and cell_face.patch is None:
                if cell.region_name not in unassigned_summaries:
                    unassigned_summaries[cell.region_name] = UnassignedSurfaceSummary(cell.region_name, 0.0, [])
                unassigned_summaries[cell.region_name].area += cell_face.area
                unassigned_summaries[cell.region_name].faces.extend(sub_face_shapes)

    near_contact_summaries = []
    for near_contact in build.near_contacts:
        near_contact_with_solids = dict(near_contact)
        near_contact_with_solids["solids"] = [cell_by_id[near_contact["cell_a"]].solid, cell_by_id[near_contact["cell_b"]].solid]
        near_contact_summaries.append(near_contact_with_solids)

    return list(interface_summaries.values()), near_contact_summaries, list(unassigned_summaries.values())


def parse_ignored_interfaces(ignored_interfaces, region_names):
    """"region_a|region_b" strings -> set of frozensets. Order does not matter."""
    ignored_region_pairs = set()
    warnings = []
    for ignored_interface in ignored_interfaces:
        region_pair_names = [name.strip() for name in ignored_interface.split("|")]
        if len(region_pair_names) != 2:
            warnings.append(f"ignored interface '{ignored_interface}' is not of the form 'region_a|region_b'; skipped")
            continue
        for region_name in region_pair_names:
            if region_name not in region_names:
                warnings.append(f"ignored interface '{ignored_interface}' names unknown region '{region_name}'")
        ignored_region_pairs.add(frozenset(region_pair_names))
    return ignored_region_pairs, warnings


def build_rom_geometry(model_spec, progress_callback=None):
    """progress_callback(fraction, stage_text), if given, is called as the
    build goes along (fraction 0..1), so a GUI can show a progress bar. The
    core itself never touches the GUI."""
    build = ROMGeometryBuild(model_spec=model_spec)
    engine_axis = model_spec.engine_axis
    fuzzy_tolerance = model_spec.fuzzy_tolerance

    def report_progress(fraction, stage_text):
        if progress_callback is not None:
            progress_callback(fraction, stage_text)

    # 1. Regions -> cell solids.
    region_count = max(len(model_spec.regions), 1)
    for region_position, region_spec in enumerate(model_spec.regions):
        report_progress(0.25 * region_position / region_count, f"Cutting region {region_spec.name} into cells")
        region_geometry = build_region_geometry(region_spec, engine_axis, fuzzy_tolerance)
        build.region_geometries.append(region_geometry)
        build.errors.extend(region_geometry.errors)
        build.warnings.extend(region_geometry.warnings)

    # 2. Number the cells: region order first, then increasing axial coordinate.
    next_cell_id = 1
    for region_order, region_geometry in enumerate(build.region_geometries):
        region_cell_ids = []
        for axial_position, cell_solid in enumerate(region_geometry.cell_solids):
            cell_axial_start, cell_axial_end = region_geometry.cell_axial_bounds[axial_position]
            cell_volume, cell_centroid = shape_volume_and_centroid(cell_solid)
            r_inner, r_outer = cell_radial_extent(cell_solid, engine_axis, cell_axial_start, cell_axial_end)

            build.cells.append(CellRecord(
                cell_id=next_cell_id,
                region_name=region_geometry.spec.name,
                region_order=region_order,
                axial_index=axial_position + 1,
                solid=cell_solid,
                volume=cell_volume,
                centroid=cell_centroid,
                z_min=cell_axial_start,
                z_max=cell_axial_end,
                r_inner=r_inner,
                r_outer=r_outer,
            ))
            region_cell_ids.append(next_cell_id)
            next_cell_id += 1

        build.cellsets[region_geometry.spec.name] = region_cell_ids

    if len(build.cells) == 0:
        build.errors.append("no cells were built")
        return build

    # 3. One generalFuse over every cell: shared sub-faces become interfaces.
    report_progress(0.3, f"Finding shared faces between {len(build.cells)} cells")
    sub_faces, cell_sub_face_ids, overlaps, split_errors = split_cells_into_sub_faces(build.cells, fuzzy_tolerance, engine_axis)
    build.overlaps = overlaps
    build.sub_faces = sub_faces
    build.errors.extend(split_errors)

    # 4. Which boundary sub-faces lie on a picked patch face.
    report_progress(0.4, "Assigning boundary faces to patches")
    cell_by_id = {cell.cell_id: cell for cell in build.cells}
    boundary_sub_faces = []
    for sub_face in sub_faces:
        if len(sub_face.owner_cell_ids) == 1:
            owner_cell = cell_by_id[sub_face.owner_cell_ids[0]]
            point_on_sub_face = outward_face_samples(sub_face.face, owner_cell.solid)[0]
            boundary_sub_faces.append((sub_face.sub_face_id, sub_face.face, point_on_sub_face))

    for patch_spec in model_spec.boundary_patches:
        build.errors.extend(patch_spec.errors)
        build.warnings.extend(patch_spec.warnings)

    patch_of_sub_face, patch_errors = assign_boundary_sub_faces_to_patches(boundary_sub_faces, model_spec.boundary_patches, fuzzy_tolerance)
    build.errors.extend(patch_errors)

    # 5. Merge sub-faces into one half-face per neighbour, link the two
    #    halves of each interface, then compute the half-cell distances.
    region_names = [region_spec.name for region_spec in model_spec.regions]
    ignored_region_pairs, ignored_warnings = parse_ignored_interfaces(model_spec.ignored_interfaces, region_names)
    build.warnings.extend(ignored_warnings)

    def report_cell_face_progress(cell_fraction):
        report_progress(0.45 + 0.35 * cell_fraction, "Building cell faces and distances")

    build.refused_merges = build_cell_faces(build.cells, sub_faces, cell_sub_face_ids, patch_of_sub_face, ignored_region_pairs, engine_axis, fuzzy_tolerance, report_cell_face_progress)
    build.errors.extend(connect_interface_half_faces(build.cells))
    compute_cell_face_distances(build.cells, engine_axis, model_spec.radial_node_rule, fuzzy_tolerance)

    # 6. Face sets. add_patch! loops over every (cell, face) in a set, so a
    #    set must hold only ONE half of each interface, otherwise the flux is
    #    applied twice:
    #      "<a>__<b>"  region a's half-faces, a = earlier region in the order
    #      "<r>__<r>"  internal axial faces, the lower-z cell's half-face
    #      "<patch>"   the patch's boundary half-faces
    region_order_by_name = {}
    for region_order, region_name in enumerate(region_names):
        region_order_by_name.setdefault(region_name, region_order)

    for cell in build.cells:
        for cell_face in cell.faces:
            if cell_face.kind == "interface":
                neighbor_cell = cell_by_id[cell_face.neighbor_cell]
                if neighbor_cell.region_name == cell.region_name:
                    if neighbor_cell.axial_index > cell.axial_index:
                        face_set_name = f"{cell.region_name}__{cell.region_name}"
                        build.facetsets.setdefault(face_set_name, []).append([cell.cell_id, cell_face.local_idx])
                elif region_order_by_name[cell.region_name] < region_order_by_name[neighbor_cell.region_name]:
                    face_set_name = f"{cell.region_name}__{neighbor_cell.region_name}"
                    build.facetsets.setdefault(face_set_name, []).append([cell.cell_id, cell_face.local_idx])

            elif cell_face.kind == "boundary" and cell_face.patch is not None:
                build.facetsets.setdefault(cell_face.patch, []).append([cell.cell_id, cell_face.local_idx])

            elif cell_face.kind == "boundary" and cell_face.patch is None:
                build.unassigned_boundary_faces.append({"cell": cell.cell_id, "local_idx": cell_face.local_idx, "area": cell_face.area, "centroid": cell_face.centroid, "region": cell.region_name})

    # 7. Interfering CAD and gaps.
    report_progress(0.85, "Checking gaps between cells")
    overlapping_cell_pairs = {frozenset((overlap["cell_a"], overlap["cell_b"])) for overlap in build.overlaps}
    build.near_contacts, missed_contact_warnings = find_near_contacts(build.cells, fuzzy_tolerance, model_spec.near_contact_tolerance, overlapping_cell_pairs)
    build.warnings.extend(missed_contact_warnings)

    # 8. Spreadsheet parameters.
    build.parameters, parameter_warnings = read_spreadsheet_parameters(model_spec.parameter_sheets)
    build.warnings.extend(parameter_warnings)

    report_progress(1.0, "Done")
    return build

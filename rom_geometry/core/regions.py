"""Regions: source solids cut to an axial (and optional radial) range, then
split into equal-length axial cells.

A region is a modelling decision, not a CAD object. The same CAD body can be
used by several regions (e.g. the housing split into top / middle / bottom),
and one region can fuse several bodies.

An inverted region is the opposite: the EMPTY space inside its limits that
the sources do not fill. That is how fluid regions (fluid_domain,
pre_chamber, ...) are made, since they are not CAD bodies.
"""

from dataclasses import dataclass, field

import Part

from rom_geometry.core.axis_geometry import largest_radius_of_shape, total_bounding_box
from rom_geometry.core.source_shapes import merge_source_shapes


@dataclass
class RegionSpec:
    """What the user defines for one region. Lengths in mm along the engine axis.

    r_min / r_max of 0 mean "unbounded". source_shapes must already carry
    their global placement (use Part.getShape on the source objects).

    invert=True makes the region the empty space inside the limits minus the
    sources. Its unbounded r_max is then the sources' largest radius (the
    engine envelope), not infinity. keep_largest_piece drops the small voids
    (seal grooves, gaps outside a flange) an inversion leaves behind.
    """
    name: str
    material: str
    source_labels: list
    source_shapes: list
    z_min: float
    z_max: float
    r_min: float = 0.0
    r_max: float = 0.0
    n_axial_cells: int = 1
    z_min_expression: str = ""
    z_max_expression: str = ""
    invert: bool = False
    keep_largest_piece: bool = True


@dataclass
class RegionGeometry:
    spec: RegionSpec
    region_solid: object
    cell_solids: list
    cell_axial_bounds: list
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def build_region_geometry(region_spec, engine_axis, fuzzy_tolerance):
    """Fuse the sources, intersect with the slab/annulus, split into cells."""
    errors = []
    warnings = []
    region_name = region_spec.name

    if region_spec.n_axial_cells < 1:
        errors.append(f"region '{region_name}': NAxialCells must be at least 1 (got {region_spec.n_axial_cells})")
        return RegionGeometry(region_spec, None, [], [], errors, warnings)

    if region_spec.z_max <= region_spec.z_min:
        errors.append(f"region '{region_name}': ZMax ({region_spec.z_max} mm) must be greater than ZMin ({region_spec.z_min} mm)")
        return RegionGeometry(region_spec, None, [], [], errors, warnings)

    radial_range_is_bounded = region_spec.r_max > 0
    if radial_range_is_bounded and region_spec.r_max <= region_spec.r_min:
        errors.append(f"region '{region_name}': RMax ({region_spec.r_max} mm) must be greater than RMin ({region_spec.r_min} mm)")
        return RegionGeometry(region_spec, None, [], [], errors, warnings)

    if len(region_spec.source_shapes) == 0:
        errors.append(f"region '{region_name}': no source shapes")
        return RegionGeometry(region_spec, None, [], [], errors, warnings)

    # Fuse every source (body, screws, seals, split fastener halves) into one
    # shape. Overlaps between a region's own sources vanish here.
    fused_source_shape = merge_source_shapes(region_spec.source_shapes, fuzzy_tolerance)[0]

    # "Unbounded" radius = a cylinder that is certainly larger than the sources.
    source_bounding_box = total_bounding_box(region_spec.source_shapes)
    unbounded_radius = engine_axis.radial_distance(source_bounding_box.Center) + source_bounding_box.DiagonalLength + 1.0

    # For an inverted region "unbounded" means the engine envelope: the
    # largest radius any source reaches. Beyond it is the outside world.
    if radial_range_is_bounded:
        outer_radius = region_spec.r_max
    elif region_spec.invert:
        outer_radius = largest_radius_of_shape(fused_source_shape, engine_axis)
    else:
        outer_radius = unbounded_radius

    # The region's bounding volume: an axial slab, optionally an annulus.
    bounding_volume = engine_axis.make_axial_cylinder(outer_radius, region_spec.z_min, region_spec.z_max)
    if region_spec.r_min > 0:
        inner_exclusion = engine_axis.make_axial_cylinder(region_spec.r_min, region_spec.z_min, region_spec.z_max)
        bounding_volume = bounding_volume.cut(inner_exclusion)

    if region_spec.invert:
        # Empty space = bounding volume minus every source.
        region_solid = bounding_volume.cut(fused_source_shape.Solids, fuzzy_tolerance)
        empty_space_pieces = list(region_solid.Solids)
        if region_spec.keep_largest_piece and len(empty_space_pieces) > 1:
            largest_piece = max(empty_space_pieces, key=lambda piece: piece.Volume)
            dropped_volume = region_solid.Volume - largest_piece.Volume
            warnings.append(f"region '{region_name}': inverted; kept the largest empty space and dropped {len(empty_space_pieces) - 1} smaller void(s) totalling {dropped_volume:.6g} mm^3")
            region_solid = largest_piece
    else:
        region_solid = fused_source_shape.common(bounding_volume)
    region_solid_count = len(region_solid.Solids)

    if region_solid_count == 0 or region_solid.Volume <= 0:
        if region_spec.invert:
            errors.append(f"region '{region_name}': empty. The sources fill all of z = [{region_spec.z_min}, {region_spec.z_max}] mm, r < {outer_radius:.6g} mm")
        else:
            errors.append(f"region '{region_name}': empty. The sources do not intersect z = [{region_spec.z_min}, {region_spec.z_max}] mm" + (f", r = [{region_spec.r_min}, {region_spec.r_max}] mm" if radial_range_is_bounded or region_spec.r_min > 0 else ""))
        return RegionGeometry(region_spec, None, [], [], errors, warnings)

    if region_solid_count > 1:
        warnings.append(f"region '{region_name}': made of {region_solid_count} disconnected solids (e.g. a merged seal or fastener that does not touch the body). Each cell is still one lumped volume")

    # Split into equal axial slabs. Cell k spans [z_k, z_{k+1}], numbered in
    # increasing axial coordinate.
    axial_cell_length = (region_spec.z_max - region_spec.z_min) / region_spec.n_axial_cells
    cell_solids = []
    cell_axial_bounds = []

    for axial_index in range(region_spec.n_axial_cells):
        cell_axial_start = region_spec.z_min + axial_index * axial_cell_length
        cell_axial_end = region_spec.z_min + (axial_index + 1) * axial_cell_length
        if axial_index == region_spec.n_axial_cells - 1:
            cell_axial_end = region_spec.z_max

        axial_slab = engine_axis.make_axial_cylinder(unbounded_radius, cell_axial_start, cell_axial_end)
        cell_shape = region_solid.common(axial_slab)
        cell_pieces = [piece.removeSplitter() for piece in cell_shape.Solids]

        if len(cell_pieces) == 0:
            errors.append(f"region '{region_name}': axial cell {axial_index + 1} (z = [{cell_axial_start:.6g}, {cell_axial_end:.6g}] mm) is empty")
            continue

        if len(cell_pieces) == 1:
            cell_solid = cell_pieces[0]
        else:
            cell_solid = Part.makeCompound(cell_pieces)
            warnings.append(f"region '{region_name}': axial cell {axial_index + 1} is {len(cell_pieces)} disconnected pieces lumped into one cell")

        cell_solids.append(cell_solid)
        cell_axial_bounds.append((cell_axial_start, cell_axial_end))

    return RegionGeometry(region_spec, region_solid, cell_solids, cell_axial_bounds, errors, warnings)


def make_cavity_solid(enclosing_shape, subtracted_shapes, fuzzy_tolerance):
    """Fluid volume = enclosing solid minus the solid parts.

    Fluid regions (fluid_domain, pre_chamber, ...) are the empty space inside
    the engine, so they are not CAD bodies. Give an enclosing solid (or a
    cylinder on the axis from EngineAxis.make_axial_cylinder) and the solids
    that bound the cavity.
    """
    cavity_shape = enclosing_shape.cut(subtracted_shapes, fuzzy_tolerance).removeSplitter()
    cavity_solids = cavity_shape.Solids

    if len(cavity_solids) == 0:
        raise ValueError("the cavity is empty: the subtracted solids fill the whole enclosing solid")

    if len(cavity_solids) == 1:
        return cavity_solids[0]
    return Part.makeCompound(cavity_solids)

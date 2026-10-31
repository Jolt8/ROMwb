"""Half-cell distances: the conduction lengths that set each face's resistance.

FVMFramework's conduction is written in flat-wall form,
    heat_flow = k * area * delta_T / distance,
so every face needs the distance that makes that formula right for its shape.

  Planar face:      d = |dot(face_centroid - cell_centroid, face_normal)|
                    (the same definition FVMFramework uses for mesh cells)

  Coaxial cylinder: the exact resistance of a cylindrical shell from the
                    cell's radial node r_c to the face radius r_f is
                        R = ln(r_f / r_c) / (2 pi k L).
                    Writing that as d / (k A_f) with A_f = 2 pi r_f L gives
                        d_eq = r_f * |ln(r_f / r_c)|.

  Anything else:    normal distance from the cell node to the face at the
                    face's centroid, marked "approximate".
"""

import math

from rom_geometry.core.axis_geometry import largest_radius_of_shape


RADIAL_NODE_RULES = ("geometric_mean", "arithmetic_mean", "area_mean")


def radial_node_position(r_inner, r_outer, radial_node_rule, length_tolerance):
    """Radius at which a cell's temperature is assumed to sit.

    geometric_mean  r_c = sqrt(r_inner * r_outer). Then
                    ln(r_c / r_inner) = ln(r_outer / r_c), so the inner and
                    outer half-cell resistances of a shell are equal: the
                    shell's node sits at its thermal midpoint.
    arithmetic_mean r_c = (r_inner + r_outer) / 2 (the radial midpoint).
    area_mean       r_c = sqrt((r_inner^2 + r_outer^2) / 2), the radius that
                    splits the shell's cross-section (and so its volume)
                    into two equal halves.

    A solid cylinder (r_inner = 0) has no geometric mean and ln(r_f / 0) is
    infinite, so all rules fall back to r_c = r_outer / 2.

    Returns (r_c, note).
    """
    if radial_node_rule not in RADIAL_NODE_RULES:
        raise ValueError(f"unknown radial node rule '{radial_node_rule}'; choose one of {RADIAL_NODE_RULES}")

    cell_is_solid_cylinder = r_inner < length_tolerance
    if cell_is_solid_cylinder:
        radial_node = r_outer / 2.0
        note = "solid cell (r_inner = 0): radial node taken as r_outer / 2"
        return radial_node, note

    if radial_node_rule == "geometric_mean":
        radial_node = math.sqrt(r_inner * r_outer)
    elif radial_node_rule == "arithmetic_mean":
        radial_node = 0.5 * (r_inner + r_outer)
    else:
        radial_node = math.sqrt(0.5 * (r_inner * r_inner + r_outer * r_outer))

    return radial_node, ""


def planar_half_cell_distance(face_centroid, cell_centroid, face_normal):
    centroid_offset = face_centroid - cell_centroid
    return abs(centroid_offset.dot(face_normal))


def cylindrical_half_cell_distance(face_radius, radial_node):
    return face_radius * abs(math.log(face_radius / radial_node))


def compute_cell_face_distances(cells, engine_axis, radial_node_rule, length_tolerance):
    """Fill half_cell_distance, distance_method, meridional_normal and normals
    for every face, then neighbour and centroid distances for interfaces."""
    cell_by_id = {cell.cell_id: cell for cell in cells}

    for cell in cells:
        radial_node, radial_node_note = radial_node_position(cell.r_inner, cell.r_outer, radial_node_rule, length_tolerance)
        cell_axial_centroid = engine_axis.axial_coordinate(cell.centroid)

        for cell_face in cell.faces:

            if cell_face.surface_type == "plane":
                face_half_cell_distance = planar_half_cell_distance(cell_face.centroid, cell.centroid, cell_face.outward_normal)
                cell_face.half_cell_distance = face_half_cell_distance
                cell_face.distance_method = "planar"
                continue

            if cell_face.surface_type == "cylinder" and cell_face.is_coaxial:
                face_half_cell_distance = cylindrical_half_cell_distance(cell_face.radius, radial_node)
                cell_face.half_cell_distance = face_half_cell_distance
                cell_face.distance_method = "cylindrical_log"
                if radial_node_note:
                    cell_face.notes.append(radial_node_note)
                if cell_face.full_revolution:
                    # A full 360 degree cylinder has no single normal: its
                    # centroid is on the axis. Exported as normal = null with
                    # outward = "+r" / "-r".
                    cell_face.outward_normal = None
                else:
                    radial_unit_vector = engine_axis.radial_vector(cell_face.centroid).normalize()
                    if cell_face.outward_radial_direction == "+r":
                        cell_face.outward_normal = radial_unit_vector
                    else:
                        cell_face.outward_normal = radial_unit_vector * -1.0
                continue

            # Cones (nozzle), spheres, tori, fillets, non-coaxial cylinders:
            # approximate.
            cell_face.distance_method = "approximate"
            outward_normal_at_sample = cell_face.outward_normal
            sample_point = cell_face.representative_point
            sample_radial_unit_vector = engine_axis.radial_vector(sample_point).normalize()

            if cell_face.is_coaxial and cell_face.full_revolution:
                # Revolved face around the axis: its 3D centroid is on the
                # axis, so work in the meridional (r, z) plane instead. The
                # cell node is (r_c, z_cell); the face point is
                # (r at the sample, z at the sample); the meridional normal is
                # (n_r, n_z). This reduces to the planar rule for an axial
                # plane and to |r_f - r_c| for a cylinder.
                normal_radial_component = outward_normal_at_sample.dot(sample_radial_unit_vector)
                normal_axial_component = outward_normal_at_sample.dot(engine_axis.direction)
                sample_radius = engine_axis.radial_distance(sample_point)
                sample_axial_coordinate = engine_axis.axial_coordinate(sample_point)

                radial_offset = sample_radius - radial_node
                axial_offset = sample_axial_coordinate - cell_axial_centroid
                face_half_cell_distance = abs(radial_offset * normal_radial_component + axial_offset * normal_axial_component)

                cell_face.half_cell_distance = face_half_cell_distance
                cell_face.meridional_normal = (normal_radial_component, normal_axial_component)
                cell_face.outward_normal = None
                cell_face.notes.append("approximate: meridional normal distance from the cell node (r_c, z_centroid) to a point on the face")
                if radial_node_note:
                    cell_face.notes.append(radial_node_note)
            else:
                face_half_cell_distance = planar_half_cell_distance(sample_point, cell.centroid, outward_normal_at_sample)
                cell_face.half_cell_distance = face_half_cell_distance
                cell_face.notes.append("approximate: normal distance from the cell centroid to a point on the face, using the normal there")

    # Neighbour distance = sum of the two half-cell distances, so that the
    # series resistance d_a / k_a + d_b / k_b is what the physics sees. The
    # straight centroid-to-centroid distance is kept for reference only.
    for cell in cells:
        for cell_face in cell.faces:
            if cell_face.kind != "interface" or cell_face.neighbor_local_idx == 0:
                continue
            neighbor_cell = cell_by_id[cell_face.neighbor_cell]
            neighbor_face = neighbor_cell.faces[cell_face.neighbor_local_idx - 1]

            cell_face.neighbor_distance = cell_face.half_cell_distance + neighbor_face.half_cell_distance
            cell_face.centroid_distance = (neighbor_cell.centroid - cell.centroid).Length


def cell_radial_extent(cell_solid, engine_axis, axial_start, axial_end):
    """(r_inner, r_outer) of a cell around the engine axis, in mm.

    r_inner = distance from the axis to the solid (0 if the axis passes
    through it). r_outer = largest radius reached by any edge; for solids of
    revolution the outermost material is always bounded by an edge (circle)
    or a coaxial cylinder, whose seam edge lies at that radius.
    """
    axis_line = engine_axis.make_axis_line(axial_start - 1.0, axial_end + 1.0)
    r_inner = axis_line.distToShape(cell_solid)[0]
    r_outer = largest_radius_of_shape(cell_solid, engine_axis)
    return r_inner, r_outer

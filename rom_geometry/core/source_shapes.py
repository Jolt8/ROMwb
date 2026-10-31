"""Preparing region sources: merging small parts into a body, and splitting a
fastener that crosses from one body into another.

Merging. A region may list several sources (e.g. injector_bulkhead,
injector_disk and the injector disk screws). They are fused into one solid.
Overlaps between them (threads, press fits) disappear in the fuse, so they no
longer count as interfering CAD. A seal or fastener that does not touch the
rest stays a separate piece of the same lumped cell, and a warning says so.

Splitting. A bolt through a flange joint belongs half to each side.
split_shape_between_bodies cuts it on the contact plane between the two
bodies and hands each half to the body on its side. Each half is then a
source of that body's region, and the two halves meet on the same plane as
the bodies, so the bolt's cross-section becomes part of the interface.
"""

from dataclasses import dataclass, field

import FreeCAD as App
import Part

from rom_geometry.core.axis_geometry import shape_volume_and_centroid


# Planar contact faces whose normals agree to this tolerance, and whose
# planes are closer than the fuzzy tolerance, are treated as one contact plane.
PARALLEL_PLANE_TOLERANCE = 1.0e-9


def merge_source_shapes(source_shapes, fuzzy_tolerance):
    """Fuse sources into one shape. Returns (merged_shape, n_disconnected_pieces).

    removeSplitter merges the coplanar / co-cylindrical faces a fuse leaves
    behind (e.g. a screw head flush with the disk surface), so later each
    cell ends up with one face per neighbour instead of several slivers.
    """
    if len(source_shapes) == 0:
        raise ValueError("no source shapes to merge")

    if len(source_shapes) == 1:
        merged_shape = source_shapes[0].copy()
    else:
        first_source_shape = source_shapes[0]
        other_source_shapes = source_shapes[1:]
        merged_shape = first_source_shape.fuse(other_source_shapes, fuzzy_tolerance).removeSplitter()

    n_disconnected_pieces = len(merged_shape.Solids)
    return merged_shape, n_disconnected_pieces


def split_shape_by_plane(shape, plane_point, plane_normal):
    """Cut a shape with a plane. Returns (part_on_positive_side, part_on_negative_side).

    The positive side is the side `plane_normal` points to. The half-space is
    a box much larger than the shape, whose bottom face lies on the plane.
    """
    unit_normal = App.Vector(plane_normal).normalize()
    plane_point = App.Vector(plane_point)

    distance_to_shape = (shape.BoundBox.Center - plane_point).Length
    half_space_size = 2.0 * (distance_to_shape + shape.BoundBox.DiagonalLength) + 1.0

    # Box occupying local z = 0 .. size, centred in local x and y, then turned
    # so that local z is the plane normal and moved onto the plane point.
    half_space_box = Part.makeBox(half_space_size, half_space_size, half_space_size, App.Vector(-0.5 * half_space_size, -0.5 * half_space_size, 0))
    half_space_box.Placement = App.Placement(plane_point, App.Rotation(App.Vector(0, 0, 1), unit_normal))

    part_on_positive_side = shape.common(half_space_box)
    part_on_negative_side = shape.cut(half_space_box)
    return part_on_positive_side, part_on_negative_side


def find_planar_contact(body_a, body_b, fuzzy_tolerance):
    """The plane on which two bodies touch, from the faces they share.

    Returns (plane_point, plane_normal, contact_area) with plane_normal
    pointing from body_a into body_b, or None if they share no planar face.
    If they touch on several planes, the one with the largest contact area is
    used.
    """
    # generalFuse reports nothing for Compound arguments (and cut() results
    # are compounds), so pass the bodies' solids one by one.
    body_a_solids = list(body_a.Solids)
    body_b_solids = list(body_b.Solids)
    argument_solids = body_a_solids + body_b_solids
    fused_result, pieces_per_argument = argument_solids[0].generalFuse(argument_solids[1:], fuzzy_tolerance)

    body_a_pieces = []
    for argument_pieces in pieces_per_argument[:len(body_a_solids)]:
        body_a_pieces.extend(argument_pieces)
    body_b_pieces = []
    for argument_pieces in pieces_per_argument[len(body_a_solids):]:
        body_b_pieces.extend(argument_pieces)

    if len(body_a_pieces) == 0 or len(body_b_pieces) == 0:
        return None

    body_b_faces_by_hash = {}
    for piece in body_b_pieces:
        for face in piece.Faces:
            body_b_faces_by_hash.setdefault(hash(face), []).append(face)

    # Group the shared planar faces into planes: (normal, point, total area).
    contact_planes = []
    for piece in body_a_pieces:
        for face in piece.Faces:
            is_shared = any(face.isSame(candidate) for candidate in body_b_faces_by_hash.get(hash(face), []))
            if not is_shared or not isinstance(face.Surface, Part.Plane):
                continue

            face_normal = App.Vector(face.Surface.Axis).normalize()
            face_point = App.Vector(face.CenterOfMass)

            for contact_plane in contact_planes:
                same_direction = abs(contact_plane["normal"].dot(face_normal)) > 1.0 - PARALLEL_PLANE_TOLERANCE
                same_offset = abs((face_point - contact_plane["point"]).dot(contact_plane["normal"])) < fuzzy_tolerance
                if same_direction and same_offset:
                    contact_plane["area"] += face.Area
                    break
            else:
                contact_planes.append({"normal": face_normal, "point": face_point, "area": face.Area})

    if len(contact_planes) == 0:
        return None

    largest_contact_plane = max(contact_planes, key=lambda contact_plane: contact_plane["area"])
    plane_point = largest_contact_plane["point"]
    plane_normal = largest_contact_plane["normal"]

    # Point the normal from A into B: body_b's centroid is on the + side.
    body_b_centroid = shape_volume_and_centroid(body_b)[1]
    if (body_b_centroid - plane_point).dot(plane_normal) < 0:
        plane_normal = plane_normal * -1.0

    return plane_point, plane_normal, largest_contact_plane["area"]


@dataclass
class ShapeSplit:
    part_for_body_a: object
    part_for_body_b: object
    plane_point: object
    plane_normal: object
    contact_area: float
    notes: list = field(default_factory=list)


def split_shape_between_bodies(shape_to_split, body_a, body_b, fuzzy_tolerance, plane_point=None, plane_normal=None):
    """Cut a fastener (or seal, pin, ...) that crosses from body_a into body_b,
    and give each body the part on its side.

    The cut plane is the bodies' planar contact face, found automatically,
    unless `plane_point` and `plane_normal` are given (needed when the bodies
    do not touch, e.g. a gasket gap, or touch on a curved face).

    Which side belongs to which body is decided by the bodies' material near
    the fastener, not by their overall centroids: a long housing has its
    centroid far away from a flange bolt.
    """
    notes = []

    if plane_point is None or plane_normal is None:
        planar_contact = find_planar_contact(body_a, body_b, fuzzy_tolerance)
        if planar_contact is None:
            raise ValueError("the two bodies share no planar contact face; give plane_point and plane_normal explicitly")
        plane_point, plane_normal, contact_area = planar_contact
        notes.append(f"split on the bodies' contact plane ({contact_area:.6g} mm^2 of contact)")
    else:
        plane_point = App.Vector(plane_point)
        plane_normal = App.Vector(plane_normal).normalize()
        contact_area = 0.0
        notes.append("split on the plane given by the user")

    # The bodies' material close to the fastener: each body intersected with
    # a box around the fastener. Its centroid tells which side the body is on.
    fastener_neighbourhood = App.BoundBox(shape_to_split.BoundBox)
    fastener_neighbourhood.enlarge(max(1.0, 0.25 * shape_to_split.BoundBox.DiagonalLength))
    neighbourhood_box = Part.makeBox(fastener_neighbourhood.XLength, fastener_neighbourhood.YLength, fastener_neighbourhood.ZLength, App.Vector(fastener_neighbourhood.XMin, fastener_neighbourhood.YMin, fastener_neighbourhood.ZMin))

    body_side_signs = []
    for body_label, body in (("body_a", body_a), ("body_b", body_b)):
        body_near_fastener = body.common(neighbourhood_box)
        if body_near_fastener.Volume <= 0:
            raise ValueError(f"{body_label} has no material near the shape being split")
        body_near_fastener_centroid = shape_volume_and_centroid(body_near_fastener)[1]
        signed_distance = (body_near_fastener_centroid - plane_point).dot(plane_normal)
        body_side_signs.append(1 if signed_distance > 0 else -1)

    if body_side_signs[0] == body_side_signs[1]:
        raise ValueError("near the shape being split, both bodies lie on the same side of the cut plane")

    part_on_positive_side, part_on_negative_side = split_shape_by_plane(shape_to_split, plane_point, plane_normal)
    if body_side_signs[0] > 0:
        part_for_body_a, part_for_body_b = part_on_positive_side, part_on_negative_side
    else:
        part_for_body_a, part_for_body_b = part_on_negative_side, part_on_positive_side

    if part_for_body_a.Volume <= 0 or part_for_body_b.Volume <= 0:
        notes.append("the shape lies entirely on one side of the cut plane, so one part is empty")

    return ShapeSplit(part_for_body_a, part_for_body_b, plane_point, plane_normal, contact_area, notes)

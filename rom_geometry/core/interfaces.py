"""Shared faces between cells, boundary faces, overlaps and near-contacts.

The idea: run ONE generalFuse over every cell solid. In the fused result, two
touching cells literally share the same TopoDS face. So:
  - a sub-face owned by two cells is an interface half-face pair,
  - a sub-face owned by one cell is a boundary half-face of that cell.
Sub-faces between the same two cells are then merged into one face per
neighbour (when they are the same kind of surface), because FVMFramework
physics functions are written per face.
"""

from dataclasses import dataclass, field

import FreeCAD as App

from rom_geometry.core.axis_geometry import centroid_sample_on_face, describe_face_surface, outward_face_samples


# Relative tolerance for "these planar normals are parallel".
PARALLEL_NORMAL_TOLERANCE = 1.0e-9


@dataclass
class SubFace:
    """One face of the generalFuse result, shared by one or two cells."""
    sub_face_id: int
    face: object
    owner_cell_ids: list
    surface: dict


@dataclass
class CellFace:
    """One exported half-face of a cell (possibly several merged sub-faces). mm units."""
    kind: str
    neighbor_cell: int
    patch: object
    sub_face_ids: list
    surface_type: str
    is_coaxial: bool
    full_revolution: bool
    radius: object
    area: float
    centroid: object
    outward_normal: object
    outward_radial_direction: object
    area_vector: object
    area_vector_is_exact: bool
    representative_point: object
    ignored_neighbor_cell: int = 0
    local_idx: int = 0
    neighbor_local_idx: int = 0
    half_cell_distance: float = 0.0
    neighbor_distance: float = 0.0
    centroid_distance: float = 0.0
    distance_method: str = ""
    meridional_normal: object = None
    notes: list = field(default_factory=list)


@dataclass
class CellRecord:
    """One lumped cell. Lengths in mm along/around the engine axis."""
    cell_id: int
    region_name: str
    region_order: int
    axial_index: int
    solid: object
    volume: float
    centroid: object
    z_min: float
    z_max: float
    r_inner: float
    r_outer: float
    faces: list = field(default_factory=list)


def split_cells_into_sub_faces(cells, fuzzy_tolerance, engine_axis):
    """Run one generalFuse over all cell solids and record who owns each sub-face.

    Returns (sub_faces, cell_sub_face_ids, errors), where cell_sub_face_ids[i]
    lists the sub-face ids on the boundary of cells[i].
    """
    errors = []

    # generalFuse only reports what happened to Solid arguments: for a
    # Compound argument its map entry is always empty. So every solid of
    # every cell goes in as its own argument (a cell is a compound when its
    # region is several disconnected pieces), and we remember its owner.
    argument_solids = []
    argument_owner_cell_index = []
    for cell_index, cell in enumerate(cells):
        for cell_solid in cell.solid.Solids:
            argument_solids.append(cell_solid)
            argument_owner_cell_index.append(cell_index)

    if len(argument_solids) == 1:
        pieces_per_argument = [[argument_solids[0]]]
    else:
        fused_result, pieces_per_argument = argument_solids[0].generalFuse(argument_solids[1:], fuzzy_tolerance)

    # generalFuse only maps shapes it modified. A solid that touches nothing
    # (e.g. across a gap) comes back with an empty list, and the original
    # solid is its own piece.
    pieces_per_cell = [[] for cell in cells]
    for argument_index, argument_pieces in enumerate(pieces_per_argument):
        if len(argument_pieces) == 0:
            argument_pieces = [argument_solids[argument_index]]
        pieces_per_cell[argument_owner_cell_index[argument_index]].extend(argument_pieces)

    # Faces from different pieces that are the same TopoDS face have equal
    # hashes but are not equal as dict keys, so bucket by hash and confirm
    # with isSame.
    sub_faces = []
    sub_face_ids_by_hash = {}

    def find_or_add_sub_face(face):
        face_hash = hash(face)
        candidate_ids = sub_face_ids_by_hash.setdefault(face_hash, [])
        for candidate_id in candidate_ids:
            if sub_faces[candidate_id].face.isSame(face):
                return candidate_id
        new_sub_face_id = len(sub_faces)
        surface_description = describe_face_surface(face, engine_axis, fuzzy_tolerance)
        sub_faces.append(SubFace(new_sub_face_id, face, [], surface_description))
        candidate_ids.append(new_sub_face_id)
        return new_sub_face_id

    cell_sub_face_ids = []
    for cell_index, cell_pieces in enumerate(pieces_per_cell):
        # A cell normally maps to one piece. It maps to several only when
        # another cell overlaps it; a face shared between two pieces of the
        # same cell is then inside the cell and is not a boundary.
        occurrence_count = {}
        for piece in cell_pieces:
            for face in piece.Faces:
                sub_face_id = find_or_add_sub_face(face)
                occurrence_count[sub_face_id] = occurrence_count.get(sub_face_id, 0) + 1

        boundary_sub_face_ids = []
        for sub_face_id in sorted(occurrence_count):
            if occurrence_count[sub_face_id] == 1:
                boundary_sub_face_ids.append(sub_face_id)
                sub_faces[sub_face_id].owner_cell_ids.append(cells[cell_index].cell_id)

        cell_sub_face_ids.append(boundary_sub_face_ids)

    for sub_face in sub_faces:
        if len(sub_face.owner_cell_ids) > 2:
            owner_list = ", ".join(str(cell_id) for cell_id in sub_face.owner_cell_ids)
            errors.append(f"a face of area {sub_face.face.Area:.6g} mm^2 is shared by more than two cells ({owner_list}); the cells must overlap")

    return sub_faces, cell_sub_face_ids, errors


def _sub_faces_can_merge(face_a, face_b, length_tolerance):
    """Two half-faces of one cell may merge if they are the same kind of surface
    facing the same way: parallel planes with the same outward normal, or
    coaxial cylinders of the same radius on the same side."""
    if face_a.surface_type == "plane" and face_b.surface_type == "plane":
        normal_alignment = face_a.outward_normal.dot(face_b.outward_normal)
        return normal_alignment > 1.0 - PARALLEL_NORMAL_TOLERANCE

    both_coaxial_cylinders = face_a.surface_type == "cylinder" and face_b.surface_type == "cylinder" and face_a.is_coaxial and face_b.is_coaxial
    if both_coaxial_cylinders:
        same_radius = abs(face_a.radius - face_b.radius) < length_tolerance
        same_side = face_a.outward_radial_direction == face_b.outward_radial_direction
        return same_radius and same_side

    return False


def build_cell_faces(cells, sub_faces, cell_sub_face_ids, patch_of_sub_face, ignored_region_pairs, engine_axis, length_tolerance):
    """Turn each cell's sub-faces into its exported faces.

    Fills cell.faces (with local_idx) for every cell and returns
    (refused_merge_messages). Neighbour local indices are filled afterwards
    by connect_interface_half_faces.
    """
    cell_by_id = {cell.cell_id: cell for cell in cells}
    refused_merge_messages = []

    for cell_index, cell in enumerate(cells):
        single_sub_face_records = []

        # 1. Describe every sub-face from this cell's point of view.
        for sub_face_id in cell_sub_face_ids[cell_index]:
            sub_face = sub_faces[sub_face_id]
            other_owner_ids = [owner_id for owner_id in sub_face.owner_cell_ids if owner_id != cell.cell_id]

            if len(other_owner_ids) == 0:
                kind = "boundary"
                neighbor_cell_id = 0
                ignored_neighbor_cell_id = 0
                patch_name = patch_of_sub_face.get(sub_face_id)
            else:
                neighbor_cell_id = other_owner_ids[0]
                neighbor_region_name = cell_by_id[neighbor_cell_id].region_name
                region_pair = frozenset((cell.region_name, neighbor_region_name))
                patch_name = None
                if region_pair in ignored_region_pairs and neighbor_region_name != cell.region_name:
                    kind = "ignored_interface"
                    ignored_neighbor_cell_id = neighbor_cell_id
                    neighbor_cell_id = 0
                else:
                    kind = "interface"
                    ignored_neighbor_cell_id = 0

            representative_point, outward_normal, tessellated_area_vector = outward_face_samples(sub_face.face, cell.solid)
            surface = sub_face.surface

            has_exact_distance_rule = surface["surface_type"] == "plane" or (surface["surface_type"] == "cylinder" and surface["is_coaxial"])
            if not has_exact_distance_rule:
                is_revolved_about_axis = surface["is_coaxial"] and surface["full_revolution"]
                representative_point, outward_normal = centroid_sample_on_face(sub_face.face, engine_axis, representative_point, outward_normal, is_revolved_about_axis)

            outward_radial_direction = None
            if surface["surface_type"] == "cylinder" and surface["is_coaxial"]:
                radial_unit_vector = engine_axis.radial_vector(representative_point).normalize()
                if outward_normal.dot(radial_unit_vector) > 0:
                    outward_radial_direction = "+r"
                else:
                    outward_radial_direction = "-r"

            # Area vectors (sum of n dA): exact for planes and for full
            # coaxial cylinders (zero); from the tessellation otherwise.
            if surface["surface_type"] == "plane":
                area_vector = outward_normal * sub_face.face.Area
                area_vector_is_exact = True
            elif surface["surface_type"] == "cylinder" and surface["full_revolution"]:
                area_vector = App.Vector(0, 0, 0)
                area_vector_is_exact = True
            else:
                area_vector = tessellated_area_vector
                area_vector_is_exact = False

            single_sub_face_records.append(CellFace(
                kind=kind,
                neighbor_cell=neighbor_cell_id,
                patch=patch_name,
                sub_face_ids=[sub_face_id],
                surface_type=surface["surface_type"],
                is_coaxial=surface["is_coaxial"],
                full_revolution=surface["full_revolution"],
                radius=surface["radius"],
                area=sub_face.face.Area,
                centroid=App.Vector(sub_face.face.CenterOfMass),
                outward_normal=outward_normal,
                outward_radial_direction=outward_radial_direction,
                area_vector=area_vector,
                area_vector_is_exact=area_vector_is_exact,
                representative_point=representative_point,
                ignored_neighbor_cell=ignored_neighbor_cell_id,
            ))

        # 2. Merge sub-faces with the same neighbour / patch / kind that are
        #    the same surface type facing the same way.
        merged_faces = []
        for sub_face_record in single_sub_face_records:
            merged_into_existing = False
            for merged_face in merged_faces:
                same_destination = merged_face.kind == sub_face_record.kind and merged_face.neighbor_cell == sub_face_record.neighbor_cell and merged_face.ignored_neighbor_cell == sub_face_record.ignored_neighbor_cell and merged_face.patch == sub_face_record.patch
                if not same_destination:
                    continue
                if not _sub_faces_can_merge(merged_face, sub_face_record, length_tolerance):
                    continue

                combined_area = merged_face.area + sub_face_record.area
                combined_centroid = (merged_face.centroid * merged_face.area + sub_face_record.centroid * sub_face_record.area) * (1.0 / combined_area)

                merged_face.centroid = combined_centroid
                merged_face.area = combined_area
                merged_face.area_vector = merged_face.area_vector + sub_face_record.area_vector
                merged_face.area_vector_is_exact = merged_face.area_vector_is_exact and sub_face_record.area_vector_is_exact
                merged_face.sub_face_ids = merged_face.sub_face_ids + sub_face_record.sub_face_ids
                merged_face.full_revolution = engine_axis.radial_distance(combined_centroid) < length_tolerance and merged_face.full_revolution
                merged_into_existing = True
                break

            if not merged_into_existing:
                merged_faces.append(sub_face_record)

        # 3. Report refused merges: faces to the same neighbour (or patch)
        #    kept separate because they are different surfaces. Unassigned
        #    boundary faces are not "per neighbour", so they are not reported.
        for merged_face in merged_faces:
            is_unassigned_boundary = merged_face.kind == "boundary" and merged_face.patch is None
            if is_unassigned_boundary:
                continue

            siblings = []
            for other_face in merged_faces:
                if other_face is merged_face:
                    continue
                same_destination = other_face.kind == merged_face.kind and other_face.neighbor_cell == merged_face.neighbor_cell and other_face.ignored_neighbor_cell == merged_face.ignored_neighbor_cell and other_face.patch == merged_face.patch
                if same_destination:
                    siblings.append(other_face)

            if len(siblings) > 0:
                sibling_types = ", ".join(sibling.surface_type for sibling in siblings)
                if merged_face.kind == "boundary":
                    destination_text = f"patch '{merged_face.patch}'"
                else:
                    destination_text = f"cell {merged_face.neighbor_cell or merged_face.ignored_neighbor_cell}"
                note = f"kept separate from {len(siblings)} other face(s) to {destination_text} ({sibling_types}) because merging a {merged_face.surface_type} with a different surface would leave no single normal or half-cell distance"
                merged_face.notes.append(note)
                refused_merge_messages.append(f"cell {cell.cell_id} ({cell.region_name}): {merged_face.surface_type} face {note}")

        # 4. Deterministic local face order: interfaces by neighbour, then
        #    patch faces, then ignored interfaces, then unassigned faces;
        #    ties broken by position along and around the axis.
        kind_rank = {"interface": 0, "boundary": 1, "ignored_interface": 2}

        def face_sort_key(cell_face):
            rank = kind_rank[cell_face.kind]
            if cell_face.kind == "boundary" and cell_face.patch is None:
                rank = 3
            axial_position = round(engine_axis.axial_coordinate(cell_face.centroid), 6)
            radial_position = round(engine_axis.radial_distance(cell_face.representative_point), 6)
            return (rank, cell_face.neighbor_cell, cell_face.ignored_neighbor_cell, cell_face.patch or "", axial_position, radial_position)

        merged_faces.sort(key=face_sort_key)
        for local_face_position, merged_face in enumerate(merged_faces):
            merged_face.local_idx = local_face_position + 1

        cell.faces = merged_faces

    return refused_merge_messages


def connect_interface_half_faces(cells):
    """Fill neighbor_local_idx for every interface half-face.

    The neighbour's matching half-face is the one that contains the same
    sub-faces. Returns a list of errors for half-faces with no partner.
    """
    errors = []
    cell_by_id = {cell.cell_id: cell for cell in cells}

    for cell in cells:
        for cell_face in cell.faces:
            if cell_face.kind != "interface":
                continue

            neighbor_cell = cell_by_id[cell_face.neighbor_cell]
            this_sub_face_ids = set(cell_face.sub_face_ids)
            matching_neighbor_faces = []
            for neighbor_face in neighbor_cell.faces:
                if neighbor_face.kind == "interface" and neighbor_face.neighbor_cell == cell.cell_id and this_sub_face_ids.intersection(neighbor_face.sub_face_ids):
                    matching_neighbor_faces.append(neighbor_face)

            if len(matching_neighbor_faces) != 1:
                errors.append(f"cell {cell.cell_id} face {cell_face.local_idx}: found {len(matching_neighbor_faces)} matching half-faces on neighbour cell {neighbor_cell.cell_id} (expected 1)")
                continue

            cell_face.neighbor_local_idx = matching_neighbor_faces[0].local_idx

    return errors


def find_cell_overlaps(cells):
    """Volume shared by cells of different regions. Must be ~0; non-zero means
    interfering CAD (threads, press fits) and is an error in validation."""
    overlaps = []
    for index_a in range(len(cells)):
        for index_b in range(index_a + 1, len(cells)):
            cell_a = cells[index_a]
            cell_b = cells[index_b]
            if cell_a.region_name == cell_b.region_name:
                continue
            if not cell_a.solid.BoundBox.intersect(cell_b.solid.BoundBox):
                continue

            overlap_volume = cell_a.solid.common(cell_b.solid).Volume
            smaller_cell_volume = min(cell_a.volume, cell_b.volume)
            if overlap_volume > 1.0e-6 * smaller_cell_volume:
                overlaps.append({"cell_a": cell_a.cell_id, "cell_b": cell_b.cell_id, "region_a": cell_a.region_name, "region_b": cell_b.region_name, "overlap_volume": overlap_volume, "smaller_cell_volume": smaller_cell_volume})
    return overlaps


def find_near_contacts(cells, fuzzy_tolerance, near_contact_tolerance, overlapping_cell_pairs):
    """Pairs of cells (different regions) that share no face but are closer
    than near_contact_tolerance.

    A gap is a real thermal resistance, so it is reported, not hidden.
    Pairs that touch only along an edge or at a point (distance ~0, no
    shared face) are ignored: that contact has zero area. If such a pair
    actually touches over an area, generalFuse missed an interface (fuzzy
    tolerance too small for the CAD) and a warning is returned.

    Returns (near_contacts, missed_contact_warnings).
    """
    sharing_cell_pairs = set()
    for cell in cells:
        for cell_face in cell.faces:
            if cell_face.kind == "interface":
                sharing_cell_pairs.add(frozenset((cell.cell_id, cell_face.neighbor_cell)))
            if cell_face.kind == "ignored_interface":
                sharing_cell_pairs.add(frozenset((cell.cell_id, cell_face.ignored_neighbor_cell)))

    near_contacts = []
    missed_contact_warnings = []

    for index_a in range(len(cells)):
        for index_b in range(index_a + 1, len(cells)):
            cell_a = cells[index_a]
            cell_b = cells[index_b]
            cell_pair = frozenset((cell_a.cell_id, cell_b.cell_id))

            if cell_a.region_name == cell_b.region_name:
                continue
            if cell_pair in sharing_cell_pairs or cell_pair in overlapping_cell_pairs:
                continue

            enlarged_bounding_box = App.BoundBox(cell_a.solid.BoundBox)
            enlarged_bounding_box.enlarge(near_contact_tolerance)
            if not enlarged_bounding_box.intersect(cell_b.solid.BoundBox):
                continue

            gap = cell_a.solid.distToShape(cell_b.solid)[0]

            if gap <= fuzzy_tolerance:
                touching_area = 0.0
                for face_a in cell_a.solid.Faces:
                    for face_b in cell_b.solid.Faces:
                        if face_a.BoundBox.intersect(face_b.BoundBox):
                            touching_area += face_a.common(face_b, fuzzy_tolerance).Area
                smaller_cell_area = min(cell_a.solid.Area, cell_b.solid.Area)
                if touching_area > 1.0e-6 * smaller_cell_area:
                    missed_contact_warnings.append(f"cells {cell_a.cell_id} ({cell_a.region_name}) and {cell_b.cell_id} ({cell_b.region_name}) touch over {touching_area:.6g} mm^2 but no interface was found; try a larger FuzzyTolerance")
                continue

            if gap < near_contact_tolerance:
                near_contacts.append({"cell_a": cell_a.cell_id, "cell_b": cell_b.cell_id, "region_a": cell_a.region_name, "region_b": cell_b.region_name, "gap": gap})

    return near_contacts, missed_contact_warnings

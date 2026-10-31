"""Boundary patches: named sets of CAD faces (e.g. flat_plate_ambient_heat_exchange).

Two jobs:
  1. Fingerprints. FreeCAD's "FaceN" names change when the CAD is edited
     upstream, so each picked face is stored with a geometric fingerprint and
     re-found by geometry, never by index. Missing or ambiguous matches are
     errors; a different face is never silently used.
  2. Assignment. Every boundary half-face of every cell is tested against the
     patch faces: it belongs to a patch if a point on it lies on a patch face
     and at least 99% of its area is covered by that face.
"""

from dataclasses import dataclass, field

import FreeCAD as App
import Part


# A boundary half-face belongs to a patch face if at least this fraction of
# its area overlaps the patch face.
PATCH_COVERAGE_FRACTION = 0.99

# Tight fingerprint match: the face did not change, only its index did.
FINGERPRINT_AREA_RELATIVE_TOLERANCE = 1.0e-6

# Loose fingerprint match: the face moved or resized a little (e.g. a
# spreadsheet length changed). Accepted only when exactly one candidate
# qualifies, and reported as a warning.
LOOSE_AREA_RELATIVE_TOLERANCE = 0.2
LOOSE_CENTROID_DISTANCE_FRACTION = 0.2


@dataclass
class BoundaryPatchSpec:
    """A patch with its (already re-matched) faces, in mm and global placement."""
    name: str
    faces: list
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def face_fingerprint(face):
    """Geometric fingerprint of a face: centre of mass, area, surface type,
    normal at the centre, and radius/axis for cylinders. mm units."""
    surface = face.Surface
    center_of_mass = App.Vector(face.CenterOfMass)

    surface_u, surface_v = surface.parameter(center_of_mass)
    normal_at_center = App.Vector(face.normalAt(surface_u, surface_v)).normalize()

    fingerprint = {
        "center_of_mass": [center_of_mass.x, center_of_mass.y, center_of_mass.z],
        "area": face.Area,
        "surface_type": type(surface).__name__,
        "normal_at_center": [normal_at_center.x, normal_at_center.y, normal_at_center.z],
    }

    if isinstance(surface, Part.Cylinder):
        cylinder_axis = App.Vector(surface.Axis).normalize()
        fingerprint["radius"] = surface.Radius
        fingerprint["axis"] = [cylinder_axis.x, cylinder_axis.y, cylinder_axis.z]
        fingerprint["axis_point"] = [surface.Center.x, surface.Center.y, surface.Center.z]

    return fingerprint


def _surface_matches_fingerprint(face, fingerprint, length_tolerance):
    """Same surface type, and for planes the same normal line, for cylinders
    the same radius and axis line. Position along the surface may differ."""
    surface = face.Surface
    if type(surface).__name__ != fingerprint["surface_type"]:
        return False

    if isinstance(surface, Part.Plane):
        candidate_normal = App.Vector(face.normalAt(*surface.parameter(face.CenterOfMass))).normalize()
        fingerprint_normal = App.Vector(*fingerprint["normal_at_center"])
        return candidate_normal.dot(fingerprint_normal) > 1.0 - 1.0e-9

    if isinstance(surface, Part.Cylinder):
        same_radius = abs(surface.Radius - fingerprint["radius"]) < length_tolerance
        fingerprint_axis = App.Vector(*fingerprint["axis"])
        axis_is_parallel = abs(App.Vector(surface.Axis).normalize().dot(fingerprint_axis)) > 1.0 - 1.0e-9
        axis_point_offset = App.Vector(surface.Center) - App.Vector(*fingerprint["axis_point"])
        axis_point_offset_perpendicular = axis_point_offset - fingerprint_axis * axis_point_offset.dot(fingerprint_axis)
        same_axis_line = axis_is_parallel and axis_point_offset_perpendicular.Length < length_tolerance
        return same_radius and same_axis_line

    return True


def rematch_face(fingerprint, candidate_faces, length_tolerance):
    """Find the one face among `candidate_faces` that has this fingerprint.

    Returns (face_index or None, status, message) where status is "exact",
    "loose" or "error".
    """
    fingerprint_center = App.Vector(*fingerprint["center_of_mass"])
    fingerprint_area = fingerprint["area"]

    exact_matches = []
    loose_matches = []

    for face_index, candidate_face in enumerate(candidate_faces):
        if not _surface_matches_fingerprint(candidate_face, fingerprint, length_tolerance):
            continue

        center_distance = (App.Vector(candidate_face.CenterOfMass) - fingerprint_center).Length
        area_relative_difference = abs(candidate_face.Area - fingerprint_area) / fingerprint_area

        if center_distance < length_tolerance and area_relative_difference < FINGERPRINT_AREA_RELATIVE_TOLERANCE:
            exact_matches.append(face_index)
            continue

        loose_center_distance_limit = LOOSE_CENTROID_DISTANCE_FRACTION * fingerprint_area ** 0.5
        if center_distance < loose_center_distance_limit and area_relative_difference < LOOSE_AREA_RELATIVE_TOLERANCE:
            loose_matches.append(face_index)

    if len(exact_matches) == 1:
        return exact_matches[0], "exact", ""

    if len(exact_matches) > 1:
        return None, "error", f"ambiguous: {len(exact_matches)} faces match the fingerprint exactly"

    if len(loose_matches) == 1:
        return loose_matches[0], "loose", "the face changed shape or position; it was re-matched by surface type and nearby centroid"

    if len(loose_matches) > 1:
        return None, "error", f"ambiguous: the original face is gone and {len(loose_matches)} similar faces are nearby"

    return None, "error", "no face matches the fingerprint (it was removed or changed too much)"


def assign_boundary_sub_faces_to_patches(boundary_sub_faces, patch_specs, length_tolerance):
    """Decide which patch (if any) each boundary sub-face belongs to.

    boundary_sub_faces: list of (sub_face_id, face, point_on_face).
    Returns (patch_of_sub_face, errors). A sub-face on two patches is an
    error, because its heat flux would then be applied twice.
    """
    patch_of_sub_face = {}
    errors = []

    for sub_face_id, sub_face, point_on_sub_face in boundary_sub_faces:
        point_vertex = Part.Vertex(point_on_sub_face)
        matching_patch_names = []

        for patch_spec in patch_specs:
            sub_face_is_on_patch = False

            for patch_face in patch_spec.faces:
                enlarged_patch_bounding_box = App.BoundBox(patch_face.BoundBox)
                enlarged_patch_bounding_box.enlarge(length_tolerance)
                if not enlarged_patch_bounding_box.intersect(sub_face.BoundBox):
                    continue

                # The point must lie on the patch face's surface...
                distance_to_patch_face = point_vertex.distToShape(patch_face)[0]
                if distance_to_patch_face > length_tolerance:
                    continue

                # ...and the patch face must cover (nearly) all of the sub-face.
                covered_area = sub_face.common(patch_face, length_tolerance).Area
                if covered_area >= PATCH_COVERAGE_FRACTION * sub_face.Area:
                    sub_face_is_on_patch = True
                    break

            if sub_face_is_on_patch:
                matching_patch_names.append(patch_spec.name)

        if len(matching_patch_names) == 1:
            patch_of_sub_face[sub_face_id] = matching_patch_names[0]
        elif len(matching_patch_names) > 1:
            patch_list = ", ".join(matching_patch_names)
            center = sub_face.CenterOfMass
            errors.append(f"a boundary face (area {sub_face.Area:.6g} mm^2, centroid ({center.x:.4g}, {center.y:.4g}, {center.z:.4g}) mm) belongs to more than one patch: {patch_list}")

    return patch_of_sub_face, errors

"""ROMBoundaryPatch: a named set of CAD faces, e.g. flat_plate_ambient_heat_exchange.

Every boundary half-face of every cell that lies on one of these faces is
put in the patch's face set (see core/boundary_patches.py).

Topological naming: "Face12" changes when the CAD is edited upstream. So
each picked face is stored with a geometric fingerprint (centre of mass,
area, surface type, normal, radius/axis for cylinders) and is re-found by
geometry on every recompute and export, never by index:
  - same face, new index      -> the stored name is updated, no fuss;
  - face moved / resized a bit -> accepted (one similar face nearby), the
                                  fingerprint is refreshed, warning;
  - face gone or ambiguous    -> error naming the patch and the face, and
                                  the export is refused. Re-pick it.

Faces are stored as (document, top object, link path), e.g.
(hybrid_assembly_for_cfd, Assembly, "Body.Face12"), so faces picked through
an Assembly4 link are found where they sit in the assembly.
"""

import json

import FreeCAD as App
import Part

from rom_geometry.core.boundary_patches import BoundaryPatchSpec, face_fingerprint, rematch_face
from rom_geometry.objects.object_helpers import owner_shape_global, plain_sub_path, report_to_console, split_element_path
from rom_geometry.objects.rom_model import rom_model_of


DEFAULT_FUZZY_TOLERANCE = 0.01


class ROMBoundaryPatch:
    Type = "ROMBoundaryPatch"

    def __init__(self, obj):
        obj.Proxy = self
        obj.addProperty("App::PropertyString", "PatchName", "Boundary patch", "Face-set name the Julia model uses, e.g. flat_plate_ambient_heat_exchange. Must be unique and must not contain '__'.")
        obj.addProperty("App::PropertyXLinkSubList", "Faces", "Boundary patch", "The picked CAD faces (kept in sync with Fingerprints; edit them in the patch panel).")
        obj.addProperty("App::PropertyString", "Fingerprints", "Boundary patch", "JSON: each picked face's document, object, link path and geometric fingerprint.")
        obj.addProperty("App::PropertyColor", "Color", "Boundary patch", "Preview colour.")
        obj.addProperty("App::PropertyString", "Status", "Boundary patch", "Result of the last re-match.")
        obj.Fingerprints = "[]"
        obj.Color = (1.0, 0.35, 0.1)
        obj.setEditorMode("Fingerprints", 2)
        obj.setEditorMode("Status", 1)

    def execute(self, obj):
        fuzzy_tolerance = patch_fuzzy_tolerance(obj)
        matched_faces, errors, warnings, updated_entries = rematch_patch_faces(obj, fuzzy_tolerance)

        # Keep the stored names and fingerprints current, so the next
        # re-match is exact.
        if updated_entries is not None:
            write_patch_entries(obj, updated_entries)

        if errors:
            obj.Shape = Part.Shape()
            obj.Status = "; ".join(errors)
            report_to_console(f"ROMBoundaryPatch '{obj.Label}': {obj.Status}", True)
            return

        obj.Shape = Part.makeCompound(matched_faces) if matched_faces else Part.Shape()
        total_area = sum(face.Area for face in matched_faces)
        status_text = f"{len(matched_faces)} face(s), {total_area:.6g} mm^2"
        if warnings:
            status_text += "; " + "; ".join(warnings)
            report_to_console(f"ROMBoundaryPatch '{obj.Label}': {status_text}", True)
        obj.Status = status_text

    def dumps(self):
        return None

    def loads(self, state):
        return None


def patch_fuzzy_tolerance(patch_object):
    model_object = rom_model_of(patch_object)
    if model_object is not None:
        return model_object.FuzzyTolerance.Value
    return DEFAULT_FUZZY_TOLERANCE


def read_patch_entries(patch_object):
    return json.loads(patch_object.Fingerprints or "[]")


def write_patch_entries(patch_object, patch_entries):
    """Store the entries and mirror them into the Faces link property, which
    is what makes FreeCAD recompute the patch when that CAD changes."""
    patch_object.Fingerprints = json.dumps(patch_entries)

    sub_paths_by_object = {}
    linked_objects = []
    for patch_entry in patch_entries:
        entry_document = App.listDocuments().get(patch_entry["document"])
        if entry_document is None:
            continue
        top_object = entry_document.getObject(patch_entry["object"])
        if top_object is None:
            continue
        if top_object not in sub_paths_by_object:
            sub_paths_by_object[top_object] = []
            linked_objects.append(top_object)
        sub_paths_by_object[top_object].append(patch_entry["sub"])

    patch_object.Faces = [(top_object, sub_paths_by_object[top_object]) for top_object in linked_objects]


def entry_description(patch_entry):
    return f"{patch_entry['document']}#{patch_entry['object']}.{patch_entry['sub']}"


def rematch_patch_faces(patch_object, length_tolerance):
    """Find every picked face again by its fingerprint.

    Returns (matched_faces, errors, warnings, updated_entries). The faces are
    in global coordinates. updated_entries is None when nothing changed,
    otherwise the entries with new face names / fingerprints to store.
    """
    patch_name = patch_object.PatchName or patch_object.Label
    patch_entries = read_patch_entries(patch_object)
    matched_faces = []
    errors = []
    warnings = []
    entries_changed = False

    if len(patch_entries) == 0:
        warnings.append("no faces picked")

    for patch_entry in patch_entries:
        description = entry_description(patch_entry)

        entry_document = App.listDocuments().get(patch_entry["document"])
        top_object = entry_document.getObject(patch_entry["object"]) if entry_document is not None else None
        if top_object is None:
            errors.append(f"patch '{patch_name}': face {description}: its object is not open or was deleted")
            continue

        object_path, element_name = split_element_path(patch_entry["sub"])
        try:
            candidate_faces = owner_shape_global(top_object, object_path).Faces
        except Exception as resolve_error:
            errors.append(f"patch '{patch_name}': face {description}: {resolve_error}")
            continue

        matched_index, match_status, match_message = rematch_face(patch_entry["fingerprint"], candidate_faces, length_tolerance)
        if match_status == "error":
            errors.append(f"patch '{patch_name}': face {description}: {match_message}. Re-pick it in the patch panel")
            continue

        matched_face = candidate_faces[matched_index]
        rematched_sub_path = f"{object_path}Face{matched_index + 1}"
        if rematched_sub_path != patch_entry["sub"]:
            patch_entry["sub"] = rematched_sub_path
            entries_changed = True
        if match_status == "loose":
            warnings.append(f"face {description}: {match_message}")
            patch_entry["fingerprint"] = face_fingerprint(matched_face)
            entries_changed = True

        matched_faces.append(matched_face)

    return matched_faces, errors, warnings, (patch_entries if entries_changed else None)


def add_faces_to_patch(patch_object, picked_faces):
    """picked_faces: list of (top_object, sub_path) with sub_path ending in
    "FaceN", e.g. (Assembly, "Body.Face12") from a selection. Records each
    face's fingerprint. Already-picked faces are skipped. Returns the number added."""
    patch_entries = read_patch_entries(patch_object)
    existing_keys = {(patch_entry["document"], patch_entry["object"], patch_entry["sub"]) for patch_entry in patch_entries}
    added_count = 0

    for top_object, picked_sub_path in picked_faces:
        sub_path = plain_sub_path(picked_sub_path)
        object_path, element_name = split_element_path(sub_path)
        if not element_name.startswith("Face"):
            raise ValueError(f"{top_object.Label}.{sub_path} is not a face")
        entry_key = (top_object.Document.Name, top_object.Name, sub_path)
        if entry_key in existing_keys:
            continue

        face_index = int(element_name[len("Face"):]) - 1
        picked_face = owner_shape_global(top_object, object_path).Faces[face_index]
        patch_entries.append({"document": top_object.Document.Name, "object": top_object.Name, "sub": sub_path, "fingerprint": face_fingerprint(picked_face)})
        existing_keys.add(entry_key)
        added_count += 1

    write_patch_entries(patch_object, patch_entries)
    return added_count


def remove_patch_faces(patch_object, entry_indices):
    patch_entries = read_patch_entries(patch_object)
    for entry_index in sorted(entry_indices, reverse=True):
        del patch_entries[entry_index]
    write_patch_entries(patch_object, patch_entries)


def replace_patch_face(patch_object, entry_index, top_object, sub_path):
    """Re-pick: swap one stored face for a newly selected one."""
    remove_patch_faces(patch_object, [entry_index])
    add_faces_to_patch(patch_object, [(top_object, sub_path)])
    patch_entries = read_patch_entries(patch_object)
    moved_entry = patch_entries.pop()
    patch_entries.insert(entry_index, moved_entry)
    write_patch_entries(patch_object, patch_entries)


def default_patch_name(model_object):
    existing_patch_names = {child.PatchName for child in model_object.Group if hasattr(child, "PatchName")}
    patch_number = 1
    while f"patch_{patch_number}" in existing_patch_names:
        patch_number += 1
    return f"patch_{patch_number}"


def create_boundary_patch(model_object, patch_name, picked_faces):
    doc = model_object.Document
    if patch_name is None:
        patch_name = default_patch_name(model_object)
    patch_object = doc.addObject("Part::FeaturePython", "ROMBoundaryPatch")
    ROMBoundaryPatch(patch_object)
    patch_object.Label = patch_name
    patch_object.PatchName = patch_name
    model_object.addObject(patch_object)
    if picked_faces:
        add_faces_to_patch(patch_object, picked_faces)
    return patch_object


def boundary_patch_spec_from_object(patch_object, length_tolerance):
    """Re-match the faces and hand them to the core. Re-match errors become
    validation errors there, so the export is refused."""
    matched_faces, errors, warnings, updated_entries = rematch_patch_faces(patch_object, length_tolerance)
    patch_name = patch_object.PatchName or patch_object.Label
    prefixed_warnings = [f"patch '{patch_name}': {warning}" for warning in warnings]
    return BoundaryPatchSpec(patch_name, matched_faces, errors, prefixed_warnings)

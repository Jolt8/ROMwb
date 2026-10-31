"""Checks run before every export (and by the Validate command).

Errors refuse the export. Warnings are reported but allowed: they describe
modelling choices (gaps, adiabatic surfaces, unused parts of bodies) that the
user should see and decide on. Notes are information only (e.g. a stepped
contact kept as two faces); nothing needs doing about them.
"""

from dataclasses import dataclass, field

import FreeCAD as App


REGION_VOLUME_RELATIVE_TOLERANCE = 1.0e-6
INTERFACE_AREA_RELATIVE_TOLERANCE = 1.0e-6

# Closure: sum of outward area vectors over a closed cell is zero. With only
# exact faces (planes, full coaxial cylinders) that holds to round-off; a
# tessellated face (cone) carries a polygon error of about 1e-4.
CLOSURE_RELATIVE_TOLERANCE_EXACT = 1.0e-6
CLOSURE_RELATIVE_TOLERANCE_TESSELLATED = 1.0e-3


@dataclass
class ValidationReport:
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def is_valid(self):
        return len(self.errors) == 0

    def as_text(self):
        report_lines = [f"{len(self.errors)} error(s), {len(self.warnings)} warning(s), {len(self.notes)} note(s)"]
        for error in self.errors:
            report_lines.append(f"ERROR:   {error}")
        for warning in self.warnings:
            report_lines.append(f"WARNING: {warning}")
        for note in self.notes:
            report_lines.append(f"NOTE:    {note}")
        return "\n".join(report_lines)


def validate_rom_geometry(build):
    report = ValidationReport()
    report.errors.extend(build.errors)
    report.warnings.extend(build.warnings)

    cell_by_id = {cell.cell_id: cell for cell in build.cells}

    # --- Error 4: region names unique and regions not empty -----------------
    seen_region_names = set()
    for region_spec in build.model_spec.regions:
        if region_spec.name == "":
            report.errors.append("a region has an empty name")
        if region_spec.name in seen_region_names:
            report.errors.append(f"region name '{region_spec.name}' is used more than once")
        seen_region_names.add(region_spec.name)
        if "__" in region_spec.name:
            report.warnings.append(f"region name '{region_spec.name}' contains '__', which makes interface face-set names ambiguous")

    for region_geometry in build.region_geometries:
        if len(region_geometry.cell_solids) == 0 and not region_geometry.errors:
            report.errors.append(f"region '{region_geometry.spec.name}' has no cells")

    patch_names = [patch_spec.name for patch_spec in build.model_spec.boundary_patches]
    for patch_name in patch_names:
        if patch_names.count(patch_name) > 1 and f"boundary patch name '{patch_name}' is used more than once" not in report.errors:
            report.errors.append(f"boundary patch name '{patch_name}' is used more than once")
        if "__" in patch_name:
            report.errors.append(f"boundary patch name '{patch_name}' contains '__', which is reserved for interface face sets")

    # --- Error 1: cell volumes add up to the region volume -------------------
    for region_geometry in build.region_geometries:
        if region_geometry.region_solid is None:
            continue
        region_volume = region_geometry.region_solid.Volume
        cell_volume_sum = 0.0
        for cell_solid in region_geometry.cell_solids:
            cell_volume_sum += cell_solid.Volume
        relative_volume_error = abs(cell_volume_sum - region_volume) / region_volume
        if relative_volume_error > REGION_VOLUME_RELATIVE_TOLERANCE:
            report.errors.append(f"region '{region_geometry.spec.name}': cell volumes sum to {cell_volume_sum:.9g} mm^3 but the region is {region_volume:.9g} mm^3 (relative error {relative_volume_error:.3g})")

    # --- Error 2: interfaces are reciprocal with equal areas -----------------
    for cell in build.cells:
        for cell_face in cell.faces:
            if cell_face.kind != "interface":
                continue
            if cell_face.neighbor_local_idx == 0:
                continue  # already reported by connect_interface_half_faces

            neighbor_cell = cell_by_id[cell_face.neighbor_cell]
            neighbor_face = neighbor_cell.faces[cell_face.neighbor_local_idx - 1]
            points_back = neighbor_face.neighbor_cell == cell.cell_id and neighbor_face.neighbor_local_idx == cell_face.local_idx
            if not points_back:
                report.errors.append(f"cell {cell.cell_id} face {cell_face.local_idx} -> cell {neighbor_cell.cell_id} face {neighbor_face.local_idx}, but that face points to cell {neighbor_face.neighbor_cell} face {neighbor_face.neighbor_local_idx}")

            relative_area_difference = abs(cell_face.area - neighbor_face.area) / max(cell_face.area, neighbor_face.area)
            if relative_area_difference > INTERFACE_AREA_RELATIVE_TOLERANCE:
                report.errors.append(f"interface cell {cell.cell_id} face {cell_face.local_idx} has area {cell_face.area:.9g} mm^2 but its partner on cell {neighbor_cell.cell_id} has {neighbor_face.area:.9g} mm^2")

    # --- Error 3: no overlapping volume between regions ----------------------
    for overlap in build.overlaps:
        report.errors.append(f"cells {overlap['cell_a']} ({overlap['region_a']}) and {overlap['cell_b']} ({overlap['region_b']}) overlap by {overlap['overlap_volume']:.6g} mm^3 ({100.0 * overlap['overlap_volume'] / overlap['smaller_cell_volume']:.4g}% of the smaller cell): interfering CAD such as threads or press fits")

    # --- Error 6: a face set never holds both halves of an interface --------
    for face_set_name, face_set_entries in build.facetsets.items():
        entries_in_set = set()
        for cell_id, local_idx in face_set_entries:
            entries_in_set.add((cell_id, local_idx))
        for cell_id, local_idx in face_set_entries:
            cell_face = cell_by_id[cell_id].faces[local_idx - 1]
            if cell_face.kind == "interface" and (cell_face.neighbor_cell, cell_face.neighbor_local_idx) in entries_in_set:
                report.errors.append(f"face set '{face_set_name}' holds both halves of the interface between cell {cell_id} and cell {cell_face.neighbor_cell}; its flux would be applied twice")

    for face_set_name in build.facetsets:
        is_interface_set = "__" in face_set_name
        if is_interface_set and face_set_name in patch_names:
            report.errors.append(f"boundary patch '{face_set_name}' has the same name as an interface face set")

    # --- Error 7: closure, sum of outward area vectors ~ 0 per cell ---------
    for cell in build.cells:
        summed_area_vector = App.Vector(0, 0, 0)
        total_face_area = 0.0
        all_faces_exact = True
        for cell_face in cell.faces:
            summed_area_vector = summed_area_vector + cell_face.area_vector
            total_face_area += cell_face.area
            all_faces_exact = all_faces_exact and cell_face.area_vector_is_exact

        if all_faces_exact:
            closure_tolerance = CLOSURE_RELATIVE_TOLERANCE_EXACT
        else:
            closure_tolerance = CLOSURE_RELATIVE_TOLERANCE_TESSELLATED

        relative_closure_error = summed_area_vector.Length / total_face_area
        if relative_closure_error > closure_tolerance:
            report.errors.append(f"cell {cell.cell_id} ({cell.region_name}) does not close: |sum of area vectors| / total area = {relative_closure_error:.3g} (limit {closure_tolerance:.1g}); a face is missing or duplicated")

    # --- Warning: how much of each source body the regions use --------------
    source_shape_by_label = {}
    regions_using_source = {}
    for region_geometry in build.region_geometries:
        region_spec = region_geometry.spec
        if region_spec.invert:
            continue  # an inverted (fluid) region uses the space around its sources, not the sources
        for source_label, source_shape in zip(region_spec.source_labels, region_spec.source_shapes):
            source_shape_by_label.setdefault(source_label, source_shape)
            regions_using_source.setdefault(source_label, []).append(region_geometry)

    for source_label, source_shape in source_shape_by_label.items():
        source_volume = source_shape.Volume
        used_volume = 0.0
        for region_geometry in regions_using_source[source_label]:
            if region_geometry.region_solid is None:
                continue
            used_volume += region_geometry.region_solid.common(source_shape).Volume
        used_fraction = used_volume / source_volume
        if abs(used_fraction - 1.0) > 1.0e-6:
            report.warnings.append(f"source body '{source_label}': regions use {100.0 * used_fraction:.4g}% of its {source_volume:.6g} mm^3")

    # --- Warning: near-contacts --------------------------------------------
    for near_contact in build.near_contacts:
        report.warnings.append(f"near-contact: cells {near_contact['cell_a']} ({near_contact['region_a']}) and {near_contact['cell_b']} ({near_contact['region_b']}) are {near_contact['gap']:.4g} mm apart but share no face")

    # --- Warning: unassigned boundary area per region ------------------------
    unassigned_area_by_region = {}
    for unassigned_face in build.unassigned_boundary_faces:
        region_name = unassigned_face["region"]
        unassigned_area_by_region[region_name] = unassigned_area_by_region.get(region_name, 0.0) + unassigned_face["area"]
    for region_name, unassigned_area in unassigned_area_by_region.items():
        report.warnings.append(f"region '{region_name}': {unassigned_area:.6g} mm^2 of boundary has no patch (adiabatic unless you add one)")

    # --- Warning: approximate and zero distances; note: stepped contacts ----
    for cell in build.cells:
        for cell_face in cell.faces:
            if cell_face.distance_method == "approximate":
                report.warnings.append(f"cell {cell.cell_id} ({cell.region_name}) face {cell_face.local_idx}: {cell_face.surface_type} face, half-cell distance is approximate")
            if cell_face.half_cell_distance <= 1.0e-9:
                report.warnings.append(f"cell {cell.cell_id} ({cell.region_name}) face {cell_face.local_idx}: half-cell distance is zero (the face passes through the cell centroid)")

    for stepped_contact_note in build.refused_merges:
        report.notes.append(stepped_contact_note)

    return report

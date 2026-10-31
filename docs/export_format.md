# ROM geometry export format (format_version 1)

This repository owns this format. FVMFramework's loader
(`src/geometry/rom_geometry_loading.jl`, phase 4) reads it.

A reduced-order model is a very coarse unstructured mesh with oddly shaped
cells. The file therefore describes cells, half-faces, neighbours, areas,
distances, cell sets and face sets with the **same meaning FVMFramework
already uses** for its `FVMGeometry` / `CellFaceLayout`. That way
`add_region!`, `add_patch!`, `interface_geometry`, `boundary_geometry` and
`heat_diffusion!` work unchanged.

## Conventions

- All ids are **1-based**. `neighbor_cell = 0` / `neighbor_local_idx = 0` means "no neighbour".
- All values are **SI**: m, m², m³, rad. FreeCAD's internal unit is mm, and
  the conversion happens only at export.
- **Initial geometry only.** Regression (the fuel port growing) is a state
  in the Julia model; patch functions compute changing areas from `u`.
- The engine axis is `axis.origin` + t · `axis.direction` (default: global Z).
  "z" anywhere below means the coordinate along that axis, and "r" the
  distance from it.

## Top level

| key | meaning |
|---|---|
| `format_version` | `1`. The loader must refuse other versions. |
| `generator` | `{name, version, freecad_version}` |
| `source_document` | path of the `.FCStd` that was exported |
| `exported_at` | ISO 8601 timestamp with UTC offset |
| `axis` | `{origin: [x,y,z] m, direction: unit vector}` |
| `radial_node_rule` | how each cell's radial node `r_c` was chosen (below) |
| `parameters` | every spreadsheet alias, `{value, unit}` in SI |
| `regions` | region definitions, in the model's region order |
| `cells` | every cell with its half-faces |
| `cellsets` | region name → cell ids (increasing z) |
| `facetsets` | face-set name → list of `[cell_id, local_idx]` |
| `unassigned_boundary_faces` | boundary faces with no patch (adiabatic unless you add one) |
| `near_contacts` | cell pairs closer than `NearContactTolerance` that share no face |
| `validation` | `{errors: [], warnings: [...]}`. An exported file always has no errors. |

### `parameters`

Lengths → m, areas → m², volumes → m³, angles → rad. The general rule is
`si = internal × (1e-3)^length_exponent × (π/180)^angle_exponent`, and the
`unit` string is built from the SI base units (e.g. `"m^-1*kg*s^-2"`).
Dimensionless values keep their value and get `unit: ""`. Text cells are
skipped with a warning.

### `regions[]`

`name, material, source_shapes (labels), z_min, z_max, r_min, r_max
(null = unbounded), n_axial_cells, cell_ids, z_min_expression,
z_max_expression`.

The **region order** sets cell numbering (region by region, increasing z
inside each region) and the direction of interface face sets.

### `cells[]`

`id, region, axial_index (1 = lowest z), volume, centroid, z_min, z_max,
r_inner, r_outer, faces`.

`r_inner` is the distance from the axis to the cell (0 if the axis passes
through it). `r_outer` is the largest radius the cell reaches.

### `cells[].faces[]`, the half-faces

| key | meaning |
|---|---|
| `local_idx` | 1-based index within the cell (FVMFramework's `face_idx`) |
| `kind` | `"interface"`, `"boundary"` or `"ignored_interface"` |
| `neighbor_cell`, `neighbor_local_idx` | the other half of an interface; 0 otherwise |
| `patch` | boundary faces only: patch name or `null` |
| `ignored_neighbor_cell` | ignored interfaces only: the cell on the other side |
| `area` | m² |
| `surface` | `plane`, `cylinder`, `cone`, `sphere`, `torus`, `revolution`, `bspline`, `other` |
| `radius` | coaxial cylinders only, m; otherwise `null` |
| `outward` | coaxial cylinders only: `"+r"` (cell is inside the face) or `"-r"`; otherwise `null` |
| `normal` | unit normal pointing **out of this cell**, or `null` (see below) |
| `meridional_normal` | revolved non-cylindrical faces only: `[n_r, n_z]` of the outward normal |
| `centroid` | area centroid, m. For a full revolved face this lies on the axis. |
| `half_cell_distance` | conduction length from the cell node to the face, m |
| `neighbor_distance` | interfaces only: own + neighbour's half-cell distance, m |
| `centroid_distance` | interfaces only: straight centroid-to-centroid distance, m (reference only) |
| `distance_method` | `planar`, `cylindrical_log` or `approximate` |
| `notes` | why something was done (fallbacks, refused merges), `; `-separated |

Local face order is: interfaces (by neighbour id), then patch faces, then
ignored interfaces, then unassigned boundary faces.

**Normals.** Planar faces get their outward unit normal. A full 360° coaxial
cylinder has no single normal (its centroid is on the axis), so it is
exported with `normal: null` and `outward: "+r"/"-r"`. A full revolved cone
or other revolved face also gets `normal: null` plus `meridional_normal`. The
Julia loader turns `null` into a zero vector.

## One face per neighbour

All cell solids are cut against each other in a single `generalFuse`. A
sub-face owned by two cells is an interface; a sub-face owned by one cell is
a boundary. Sub-faces of one cell that go to the same neighbour (or the same
patch) are merged into one face **if they are the same kind of surface
facing the same way**: planes with the same outward normal, or coaxial
cylinders of the same radius on the same side. Merged area = sum and merged
centroid = area-weighted centroid.

A plane and a cylinder to the same neighbour (e.g. a plug sitting in a cup)
are **not** merged, because the merged face would have no single normal or
distance. They stay separate faces with a `notes` entry, and validation
reports a "refused merge" warning.

## Half-cell distances

These set the conduction resistance. FVMFramework's conduction is in
flat-wall form, `Q = k A ΔT / d`, so each face gets the `d` that makes that
form right for its shape.

**Planar faces.** This is the same definition FVMFramework uses for mesh cells:

    d = |dot(face_centroid − cell_centroid, face_normal)|

**Coaxial cylindrical faces** (radial conduction through shells). The exact
resistance of a cylindrical shell from the cell's radial node `r_c` to the
face radius `r_f` is

    R = ln(r_f / r_c) / (2π k L)

where `k` is the conductivity and `L` the axial length. Writing it in
flat-wall form `R = d / (k A_f)`, with face area `A_f = 2π r_f L`, gives

    d_eq = r_f · |ln(r_f / r_c)|

The radial node `r_c` (`radial_node_rule`) is chosen as follows:

| rule | `r_c` | why |
|---|---|---|
| `geometric_mean` (default) | `sqrt(r_inner · r_outer)` | `ln(r_c/r_inner) = ln(r_outer/r_c)`: the inner and outer half-resistances are equal, so the node is at the shell's thermal midpoint |
| `arithmetic_mean` | `(r_inner + r_outer) / 2` | radial midpoint |
| `area_mean` | `sqrt((r_inner² + r_outer²) / 2)` | splits the cross-section (volume) in half |

A **solid** cell (`r_inner = 0`) has `ln(r_f / 0) = ∞`, so every rule falls
back to `r_c = r_outer / 2`, and the face's `notes` say so.

`r_inner` and `r_outer` are the cell's own extent. For a cell that isn't a
simple annulus (e.g. a stepped one) the rule still uses those two radii.

**Any other surface** (nozzle cones, fillets, non-coaxial cylinders) gets
`distance_method = "approximate"`:

- A face revolved fully about the axis has its 3D centroid on the axis, so
  the distance is worked out in the meridional (r, z) plane. The face point
  is its area-weighted meridional centroid (r̄, z̄). With the cell node at
  `(r_c, z_cell)` and the outward meridional normal `(n_r, n_z)`,
  `d = |(r̄ − r_c) n_r + (z̄ − z_cell) n_z|`. This reduces to the planar rule
  for an axial plane.
- Any other face uses the normal distance from the cell centroid to the
  face's centroid projected onto the face, with the normal there.

**Interfaces.** `neighbor_distance = d_a + d_b`, so that the series resistance
`d_a/(k_a A) + d_b/(k_b A)` is what the physics sees. Note that
FVMFramework's current `heat_diffusion!` uses the harmonic mean of `k` with
the total distance, which is exact only when `d_a = d_b`. A per-side
series-resistance variant has been proposed separately.

## Face sets and direction (important)

`add_patch!` loops over every `(cell, face)` in a face set and looks up the
neighbour. If a set held both halves of an interface, its flux would be
applied twice. So:

| set name | contents |
|---|---|
| `"<region_a>__<region_b>"` | **only region_a's** half-faces, where region_a is the region that comes **earlier** in the region order |
| `"<region>__<region>"` | internal axial faces of one region, each once: the **lower-z** cell's half-face |
| `"<patch>"` | that patch's boundary half-faces |

Cell sets: one per region name, cell ids in increasing z.

Ignored interfaces (`IgnoredInterfaces`, `"region_a|region_b"`) are
exported as `kind = "ignored_interface"` with no neighbour and no patch, so
their area is still accounted for. They appear in no face set.

## Boundary patch assignment

A boundary half-face belongs to patch P if a point on it lies on one of P's
(re-matched) faces within `FuzzyTolerance`, and the boolean common of the two
covers at least 99% of the half-face's area. A half-face on two patches is
an error. Half-faces with no patch go in `unassigned_boundary_faces`, so
adiabatic surfaces are a visible choice.

## Validation

Errors (the export is refused):

1. Each region's cell volumes sum to the region solid's volume (relative 1e-6).
2. Interfaces are reciprocal (the neighbour points back) and `area_a ≈ area_b` (relative 1e-6).
3. No overlap volume between cells of different regions (above 1e-6 of the smaller cell).
4. Region names are unique, and no region is empty.
5. A boundary half-face belongs to at most one patch. Every patch face is re-matched uniquely.
6. No face set holds both halves of an interface.
7. Closure: for each cell, |Σ outward area vectors| / Σ area is below 1e-6. That holds
   exactly for planes and full coaxial cylinders (whose area vector is 0). Cells with a
   tessellated face (cone) use a 1e-3 limit.

Warnings:

- How much of each source body the regions use, if not 100%.
- Near-contacts (gap below `NearContactTolerance`, no shared face). Pairs that
  touch only along an edge or at a point are not reported. If such a pair
  actually touches over an area, a "no interface was found" warning is given
  (raise `FuzzyTolerance`).
- Unassigned boundary area per region (m²).
- Faces with `approximate` distances.
- Refused merges.
- Zero half-cell distances.

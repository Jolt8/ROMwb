# freecad-rom-geometry

**ROMGeometry**: a FreeCAD 1.1 workbench for defining the lumped regions of a
reduced-order thermal/flow model on a CAD assembly, and exporting their
cells, half-faces, areas and conduction distances as one JSON file that
FVMFramework loads.

The splitting of the engine into regions is a modelling decision, so you
define the regions yourself: a solid cut to a z-range (optionally a radial
range), split into N equal axial cells. The tool finds the interfaces, the
boundary faces, the gaps and the overlaps, and computes the distances.

## Status

| phase | content | state |
|---|---|---|
| 1 | headless geometry core, fixture, tests; merge parts / split fasteners | **done** |
| 2 | the workbench: toolbar, `ROMModel` / `ROMRegion` objects (incl. inverse / fluid regions, LCS and face references), region task panel, coloured preview, Split Fastener, Validate / Export (text report), `scripts/export_headless.py` | **done** |
| 3 | boundary patches (face picking, fingerprint re-matching, re-pick), interface review panel with ignore toggles and 3D highlighting | **done** |
| 4 | Julia loader in FVMFramework (`src/geometry/rom_geometry_loading.jl`) | **done** |

## Loading the export in FVMFramework

```julia
using FVMFramework, ComponentArrays, Unitful

rom_data = read_rom_geometry_json("rom_geometry.json")
add_virtual_cell!(rom_data; name = "recirculation_zone", volume = 2.0e-6,
    faces = [(neighbor_region = "fluid_domain", neighbor_axial_index = :last, area = 1.0e-4, half_cell_distance = 1.0e-3, neighbor_half_cell_distance = 2.0e-3)])

n_cells = rom_cell_count(rom_data)  # after adding virtual cells
config = create_fvm_config_from_rom(rom_data, ComponentVector(temp = zeros(n_cells)u"K"))
# then add_setup_syms!, add_region!(config, "fuel_grain"; ...), add_patch!(config, "ambient_heat_exchange_vertical"; ...),
# add_patch!(config, "fuel_grain__fluid_domain"; ...) and finish_fvm_config as for a mesh.
```

`config.geo.cellsets[region]` and `config.geo.facetsets[name]` hold the sets, and
`get_facetset(geo, region_a, region_b)` gives the faces between two regions from
`region_a`'s side. `heat_diffusion_series_resistance!` uses both half-cell
distances, so a thin liner against a thick wall is conducted correctly.

## Install

FreeCAD 1.1 loads workbenches from `%APPDATA%\FreeCAD\v1-1\Mod\`. The
easiest setup while the workbench is changing is a directory junction to
this repository, so FreeCAD always runs the current code (run in `cmd`):

```bash
mklink /J "%APPDATA%\FreeCAD\v1-1\Mod\freecad-rom-geometry" "C:\Users\wille\Desktop\ROMwb"
```

A plain copy also works, but it is a snapshot that you have to replace after
every update. Keep only one of them in `Mod`, because two copies register the
same workbench. Restart FreeCAD and pick **ROM Geometry** in the workbench
selector.

## Workflow in FreeCAD

1. **Make a separate ROM document.** Open the assembly, create a new document
   and **save it** (e.g. `hybrid_rom.FCStd`). Keep the ROM model there, so the
   assembly file is never modified. Cross-document expressions only work
   once the ROM document has been saved.
2. **Create ROM Model.** This creates a group in the ROM document. Its properties are:
   - `Axis`: an LCS whose Z is the engine axis (empty = global Z).
   - `ParameterSheets`: e.g. the `table_hybrid` spreadsheet. All its aliases are exported in SI.
   - `ExportPath`, `FuzzyTolerance`, `NearContactTolerance`, `IgnoredInterfaces`.
   - `RadialNodeRule`: `geometric_mean` by default.
3. **Add Region.** Select one or more solids (in any open document, e.g. the
   assembly's 3D view) and click Add Region. It is named `<part>_region`
   (e.g. `aluminum_housing_region`, then `_region_2`, ...); strip the suffix
   in Julia if you like. The task panel opens with the sources' z-extent as
   defaults:
   - **Sources** are fused. Add the injector bulkhead, the disk and its
     screws to make one lump. *Add selected* / *Remove highlighted* edit
     the list.
   - **Inverse** turns it into a fluid region: the *empty space* inside the
     limits that the sources do not fill. Select the solids around the gas
     (housing, liner, fuel grain, nozzle, injector), tick Inverse, and set the
     z-limits. Without R max, the outer limit is the sources' largest radius,
     so the outside world is not included. **Keep only the largest empty
     space** (on by default) drops seal grooves and small gaps.
   - **Z max / Z min / R max / R min** (max above min): each can be set three ways.
     - Type a **value**.
     - Press **`=`** in the box (or click its f(x) icon) for FreeCAD's
       expression editor, with autocomplete. Type `table_hybrid#Spreadsheet.`
       and it lists the aliases, e.g.
       `table_hybrid#Spreadsheet.aluminum_casing_length - 5 mm`.
     - **Pick a reference**: select an LCS in the assembly tree or 3D view
       (e.g. `fuel_grain_bottom_lcs` under the assembly's `fuel_grain` link),
       then click **Pick** on that row. The **Offset** box adds to it, and
       accepts expressions too. The limit then follows the LCS when the CAD
       changes. You can also pick a vertex, a planar face (gives z), a
       coaxial cylindrical face (gives r, e.g. the liner's inner wall for
       R max) or a coaxial circular edge. A reference wins over a value or
       an expression; **✕** removes it.

       Select the LCS *under the assembly's link*, not inside its own part
       document. Only the link path places an Assembly4 LCS where it sits in
       the assembly.

       LCS references are stored by object name, so they survive any CAD
       edit. Edges, faces and vertices are stored by name (`Edge12`). In
       tests they followed moved geometry (e.g. a changed pad length), but if
       an edit renumbers the elements so that the name now points at a
       different *kind* of element, the region stops with "please re-pick
       it" instead of using the wrong geometry. The check cannot tell two
       circles apart, so prefer LCSs for limits you will edit a lot.
   - **Axial cells**: N equal slabs, numbered in increasing z.
   - Changes apply as you type, and the region recomputes after a short
     pause, so the 3D view shows the cells in the region's colour.
     **Cancel** undoes everything done in the panel. To remove a region you
     just added, use Undo.
   - Double-click a region in the tree to edit it again.
4. **Order matters.** The order of regions inside the ROMModel group sets the
   cell numbering, and which side of an interface its face set holds.
5. **Split Fastener Between Bodies.** Select the fastener, then body A, then
   body B. You get two `ROMFastenerPart`s inside the ROM model, cut on the
   bodies' contact plane; add each to its body's region. Use
   `UseCustomPlane` when the bodies don't touch.
6. **Add Boundary Patch.** Select CAD faces in the assembly's 3D view (Ctrl
   for several) and click it. The patch panel lets you name it (e.g.
   `flat_plate_ambient_heat_exchange`; no `__`), add more selected faces,
   remove faces, or **Re-pick** one. Every cell half-face lying on those
   faces goes into the patch's face set. Surfaces without a patch are
   adiabatic and are listed, so that is a visible choice.

   Patch faces are re-found by a geometric fingerprint, never by their
   `FaceN` number:
   - **renumbered by a CAD edit:** the stored name is updated;
   - **moved or resized a little:** accepted, with a warning;
   - **gone or ambiguous:** the export stops with an error naming the patch
     and the face, and you re-pick it.
7. **Review Interfaces** lists:
   - every interface between two regions, with its area and an **Ignore** box;
   - the near-contacts (gaps), with their size;
   - each region's unassigned outer surface.

   Click a row to highlight it in the 3D view (drawn on top, so interfaces
   inside the engine are visible). **OK** saves the ticked pairs to
   `IgnoredInterfaces`. An ignored interface is exported as a boundary with
   no patch, so no heat crosses it.
8. **Validate** shows every error and warning. **Export JSON** validates
   first and refuses to write if there are errors.

Headless export of a saved ROM document (linked documents open automatically):

```bash
python scripts/export_headless.py hybrid_rom.FCStd rom_geometry.json
```

Override cell counts for one export, without changing the `.FCStd` (`*` = every
region, applied first):

```bash
python scripts/export_headless.py hybrid_rom.FCStd rom_fine.json --cells "*=1" --cells fuel_grain=20 --cells fluid_domain=20
```

From Python (e.g. a mesh-convergence loop), the same is
`export_rom_model(model_object, "out.json", n_axial_cells_overrides={"fuel_grain": 20})`
from `rom_geometry.objects.rom_model`.

## Layout

```
Init.py, InitGui.py, package.xml   workbench registration / Addon Manager metadata
rom_geometry/
  core/                 pure geometry, no FreeCADGui imports
    axis_geometry.py    engine axis, face classification, outward normals
    regions.py          sources ∩ slab/annulus → region solid → axial cells; cavity helper
    source_shapes.py    merge parts into one source; split a fastener between two bodies
    interfaces.py       one generalFuse → shared / boundary sub-faces → one face per neighbour;
                        overlaps, near-contacts
    distances.py        half-cell distance rules (planar, cylindrical log, approximate)
    boundary_patches.py face fingerprints, re-matching, patch assignment
    parameters.py       spreadsheet aliases → SI
    model_build.py      runs the steps in order
    validation.py       all checks; errors refuse export
    export_json.py      writes format_version 1
  objects/              FeaturePython objects (no GUI imports; recompute headless)
    rom_model.py        ROMModel group; document → core spec; validate / export
    rom_region.py       ROMRegion
    rom_boundary_patch.py  ROMBoundaryPatch (fingerprints, re-matching)
    source_features.py  ROMFastenerPart (and the old ROMCavity, kept for older documents)
  gui/                  view providers, commands, region / patch / interface-review panels, 3D highlighting
  resources/icons/
  freecad_bootstrap.py  re-launch scripts under FreeCAD's bundled Python 3.11
scripts/export_headless.py
tests/
  build_fixture.py      builds the test .FCStd from code
  test_core.py          core tests
  test_objects.py       document-object tests (save/reopen, cross-document expressions)
  expected/fixture_export.json
docs/export_format.md   the format spec (this repo owns it)
```

## Running the tests

FreeCAD 1.1 at `C:\Program Files\FreeCAD 1.1\` (set `FREECAD_BIN` otherwise):

```bash
python tests/test_core.py
```

```bash
python tests/test_objects.py
```

The scripts re-launch themselves under FreeCAD's `python.exe`. To regenerate
the expected export after an intentional change:

```bash
python tests/test_core.py --write-expected
```

## Using the core from a script

```python
import FreeCAD as App, Part
from rom_geometry.core.regions import RegionSpec
from rom_geometry.core.model_build import ROMModelSpec
from rom_geometry.core.export_json import export_rom_geometry

doc = App.openDocument("copy_of_assembly.FCStd")
housing = Part.getShape(doc.getObjectsByLabel("aluminum_housing")[0])
regions = [RegionSpec("aluminum_housing_top", "aluminum_6061", ["aluminum_housing"], [housing], z_min=0.0, z_max=15.0)]
build, report, export = export_rom_geometry(ROMModelSpec(regions=regions), "rom_geometry.json")
print(report.as_text())
```

Lengths in the spec are mm (FreeCAD's internal unit). The JSON is SI.

### Merging parts into one region

A region's sources are fused. Interference between a region's own sources
(threads, press fits) vanishes in the fuse, so it is not reported as an
overlap. A part that doesn't touch the rest (e.g. a seal with clearance)
gives a "disconnected solids" warning but stays in the same lumped cell. The
region's z-range must cover every merged part, because anything outside it
is cut off.

### Splitting a fastener between two bodies

```python
from rom_geometry.core.source_shapes import split_shape_between_bodies

shape_split = split_shape_between_bodies(fasteners_shape, housing_shape, bulkhead_shape, fuzzy_tolerance=0.01)
# shape_split.part_for_body_a -> add to the housing region's sources
# shape_split.part_for_body_b -> add to the bulkhead region's sources
```

The cut plane is the bodies' planar contact face, found automatically. Each
half goes to the body whose material lies on its side, near the fastener.
If the bodies don't touch (gasket gap) or touch on a curved face, pass
`plane_point=` and `plane_normal=`. On the real assembly this splits
`flange_fasteners_top` at z = 376.46 mm (housing / injector_bulkhead) and
`flange_fasteners_bottom` at z = 5.00 mm (nozzle casing / housing) into
equal halves.

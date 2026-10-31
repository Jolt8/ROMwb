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
| 2 | the workbench: toolbar, `ROMModel` / `ROMRegion` objects, region task panel, coloured preview, Make Cavity / Split Fastener, Validate / Export (text report), `scripts/export_headless.py` | **done** |
| 3 | boundary-patch picking + fingerprints, interface review panel with ignore toggles | core functions done (`core/boundary_patches.py`), GUI not started |
| 4 | Julia loader in FVMFramework | not started |

Until Phase 3, exports have no boundary patches: every outer surface is listed
in `unassigned_boundary_faces`. Interfaces can already be ignored by typing
`region_a|region_b` into the model's `IgnoredInterfaces` property.

## Install

Clone (or copy) this folder into FreeCAD 1.1's user Mod folder and restart
FreeCAD. The folder is `%APPDATA%\FreeCAD\v1-1\Mod\` (check
*Help → About → Copy to clipboard* for the user data path):

```bash
git clone <this repo> "$APPDATA/FreeCAD/v1-1/Mod/freecad-rom-geometry"
```

Then pick **ROM Geometry** in the workbench selector.

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
   assembly's 3D view) and click Add Region. The task panel opens with the
   sources' z-extent as defaults:
   - **Sources** are fused. Add the injector bulkhead, the disk and its
     screws to make one lump. *Add selected* / *Remove highlighted* edit
     the list.
   - **Z min / Z max / R min / R max** take a value, or an expression that
     overrides it, e.g. `table_hybrid#Spreadsheet.pre_combustion_chamber_length`
     or `table_hybrid#Spreadsheet.aluminum_casing_length - 5 mm`.
   - **Axial cells**: N equal slabs, numbered in increasing z.
   - **Preview** recomputes and shows the cells in the region's colour.
     **Cancel** reverts the edit. To remove a region you just added, use Undo.
   - Double-click a region in the tree to edit it again.
4. **Order matters.** The order of regions inside the ROMModel group sets the
   cell numbering, and which side of an interface its face set holds.
5. **Make Cavity Solid** (for fluid regions). Select the solids that bound the
   cavity and click it. You get a `ROMCavity`: an axial cylinder around them,
   minus them, which updates when the CAD changes. Set `Enclosure` to use
   your own enclosing solid instead. `KeepOnlyLargestSolid` drops small
   leftover voids (seal grooves).
6. **Split Fastener Between Bodies.** Select the fastener, then body A, then
   body B. You get two `ROMFastenerPart`s, cut on the bodies' contact plane;
   add each to its body's region. Use `UseCustomPlane` when the bodies
   don't touch. Keep cavities and fastener parts at the top level of the
   document, not inside an `App::Part`.
7. **Validate** shows every error and warning. **Export JSON** validates
   first and refuses to write if there are errors.

Headless export of a saved ROM document (linked documents open automatically):

```bash
python scripts/export_headless.py hybrid_rom.FCStd rom_geometry.json
```

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
    source_features.py  ROMCavity, ROMFastenerPart
  gui/                  view providers, commands, region task panel
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

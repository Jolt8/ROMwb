# freecad-rom-geometry

**ROMGeometry**: a FreeCAD 1.1 workbench (in progress) for defining the lumped regions of a
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
| 1 | headless geometry core, fixture, tests | **done** |
| 2 | the workbench itself: toolbar, `ROMModel` / `ROMRegion` objects, region task panel, coloured preview, Make Cavity / Split Fastener commands, `scripts/export_headless.py` | not started |
| 3 | boundary-patch picking + fingerprints, interface review, validate/export commands | core functions done (`core/boundary_patches.py`), GUI not started |
| 4 | Julia loader in FVMFramework | not started |

## Layout

```
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
  freecad_bootstrap.py  re-launch scripts under FreeCAD's bundled Python 3.11
tests/
  build_fixture.py      builds the test .FCStd from code
  test_core.py          headless tests
  expected/fixture_export.json
docs/export_format.md   the format spec (this repo owns it)
```

## Running the tests

FreeCAD 1.1 at `C:\Program Files\FreeCAD 1.1\` (set `FREECAD_BIN` otherwise):

```bash
python tests/test_core.py
```

The script re-launches itself under FreeCAD's `python.exe`. To regenerate the
expected export after an intentional change:

```bash
python tests/test_core.py --write-expected
```

## Using the core from a script (until the GUI exists)

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

This also works today inside the FreeCAD GUI: open *View → Panels → Python
console*, run `import sys; sys.path.append(r"C:\path\to\freecad-rom-geometry")`,
and use the same code on the open document (`App.ActiveDocument`).

### Merging parts into one region

A region's sources are fused, so to make the injector one lump just list
every part:

```python
injector_parts = ["injector_bulkhead", "injector_disk", "injector_bulkhead_screws"]
injector_shapes = [Part.getShape(doc.getObjectsByLabel(label)[0]) for label in injector_parts]
RegionSpec("injector_assembly", "aluminum_6061", injector_parts, injector_shapes, z_min=376.462, z_max=396.562)
```

Interference between a region's own sources (threads, press fits) vanishes
in the fuse, so it is not reported as an overlap. A part that doesn't touch
the rest (e.g. a seal with clearance) gives a "disconnected solids" warning.
It stays in the same lumped cell. The region's z-range must cover every
merged part, because anything outside it is cut off.

### Splitting a fastener between two bodies

A bolt through a flange joint belongs half to each side:

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

## Install (once the workbench exists, phase 2)

Clone into FreeCAD 1.1's user Mod folder, `%APPDATA%\FreeCAD\v1-1\Mod\`
(check *Help → About → Copy to clipboard* for the user data path), then
restart FreeCAD.

# Code style (applies to Python here and to the Julia loader in FVMFramework)

Copied from section 1 of FVMFramework/examples/1D_hybrid_rocket_engine/CLAUDE.md.

- When explaining equations, define and name every variable immediately after introducing the equation.
- When discussing engineering decisions, focus on physical reasoning, practical engineering value, and how a method contributes to designing, testing, or improving real systems.
- Code should follow an "engineering-first, physically explicit" style.
- Keep the main physical (or geometric) calculation visible. Avoid hiding important physics behind generic abstractions, excessive helper functions, or unnecessary framework machinery.
- Use descriptive, domain-specific variable names (`cell_face_half_cell_distance`, not `d`). Long names are acceptable when they make physical meaning clearer.
- Prefer explicit intermediate variables and straightforward loops over compact programming idioms.
- Group code according to the sequence of physical operations being performed.
- Use comments to explain physical assumptions, geometry, and implementation reasoning (e.g. why `r_c` is the geometric mean).
- Major physics functions should be readable from top to bottom without requiring frequent navigation into other functions.
- Avoid aggressive indentation and unnecessary line wrapping in code.
- Keep assignments on a single line where practical instead of wrapping the assignment operator and expression onto separate lines.
- Include blank lines between logically distinct calculations and physical operations.
- Prioritize readability and explicit physical meaning over clever syntax or highly abstract software architecture. A few plain modules beat a deep class hierarchy.

Prefer:

```
solid_fuel_specific_energy = u.volumetric_energy[fuel_cell_id] / solid_fuel_density

injected_fuel_specific_energy = solid_fuel_specific_energy - p.fuel_heat_of_pyrolysis
```

Avoid:

```
solid_fuel_specific_energy =
            u.volumetric_energy[fuel_cell_id] / solid_fuel_density
```

# Repository rules

- `rom_geometry/core/` and `rom_geometry/objects/` must never import `FreeCADGui`. They must run headless.
- Axial positions (ZMin/ZMax) are `App::PropertyDistance`, not `App::PropertyLength`, because `PropertyLength` silently clamps negative values to 0.
- `generalFuse` returns empty map entries for Compound arguments (and `cut()` returns compounds). Always pass `.Solids` individually.
- Core lengths are FreeCAD mm. Only `core/export_json.py` converts to SI.
- The export format is specified in `docs/export_format.md`. Change the doc whenever you change the format.
- Tests run under FreeCAD's bundled Python: `python tests/test_core.py` re-launches itself under `C:\Program Files\FreeCAD 1.1\bin\python.exe` (override with `FREECAD_BIN`).
- After an intentional change to the export, regenerate the expected file with `python tests/test_core.py --write-expected`, and review the diff.
- Never modify files in `hybrid-rocket-CAD`. Test against copies.

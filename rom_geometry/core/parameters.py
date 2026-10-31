"""Spreadsheet aliases -> SI values for the export's "parameters" block.

FreeCAD stores quantities in its internal units: mm, kg, s, A, K, mol, cd and
degrees. A quantity's Unit.Signature gives the exponent of each, so the SI
value is
    si_value = internal_value * (1e-3)^length_exponent * (pi/180)^angle_exponent
and every other base unit is already SI.
"""

import math


SI_BASE_UNIT_NAMES = ("m", "kg", "s", "A", "K", "mol", "cd", "rad")


def si_unit_string(unit_signature):
    """e.g. (1,0,0,0,0,0,0,0) -> "m", (-1,1,-2,0,0,0,0,0) -> "m^-1*kg*s^-2"."""
    unit_parts = []
    for base_unit_name, exponent in zip(SI_BASE_UNIT_NAMES, unit_signature):
        if exponent == 0:
            continue
        if exponent == 1:
            unit_parts.append(base_unit_name)
        else:
            unit_parts.append(f"{base_unit_name}^{exponent}")
    return "*".join(unit_parts)


def quantity_to_si(quantity):
    """Return (si_value, si_unit_string) for a FreeCAD Quantity."""
    unit_signature = tuple(quantity.Unit.Signature)
    length_exponent = unit_signature[0]
    angle_exponent = unit_signature[7]

    millimetre_to_metre_factor = 1.0e-3 ** length_exponent
    degree_to_radian_factor = (math.pi / 180.0) ** angle_exponent
    si_value = quantity.Value * millimetre_to_metre_factor * degree_to_radian_factor

    return si_value, si_unit_string(unit_signature)


def read_spreadsheet_parameters(spreadsheets):
    """Every alias of every sheet, evaluated (the document must be recomputed).

    Returns (parameters, warnings) where parameters maps
    alias -> {"value": si_value, "unit": si_unit, "sheet": sheet_label}.
    Text cells are skipped with a warning. If two sheets use the same alias,
    the first sheet wins and a warning is given.
    """
    parameters = {}
    warnings = []

    for spreadsheet in spreadsheets:
        for cell_address in spreadsheet.getUsedCells():
            alias = spreadsheet.getAlias(cell_address)
            if not alias:
                continue

            if alias in parameters:
                warnings.append(f"parameter '{alias}' is defined in both '{parameters[alias]['sheet']}' and '{spreadsheet.Label}'; using '{parameters[alias]['sheet']}'")
                continue

            cell_value = spreadsheet.get(cell_address)

            if hasattr(cell_value, "Unit"):
                si_value, si_unit = quantity_to_si(cell_value)
            elif isinstance(cell_value, bool):
                si_value, si_unit = int(cell_value), ""
            elif isinstance(cell_value, (int, float)):
                si_value, si_unit = cell_value, ""
            else:
                warnings.append(f"parameter '{alias}' in '{spreadsheet.Label}' is not a number ({cell_value!r}); skipped")
                continue

            parameters[alias] = {"value": si_value, "unit": si_unit, "sheet": spreadsheet.Label}

    return parameters, warnings

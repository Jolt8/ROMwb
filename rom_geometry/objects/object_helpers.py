"""Small helpers shared by the ROMGeometry document objects."""

import FreeCAD as App
import Part


def global_shape(document_object):
    """The object's shape in global coordinates.

    Part.getShape applies the object's own Placement (and an App::Link's
    placement), but not the placement of an App::Part container the object
    sits in. Assemblies put bodies inside such containers, so the
    container's global placement is applied here.
    """
    shape = Part.getShape(document_object)
    parent_container = document_object.getParentGeoFeatureGroup()
    if parent_container is not None:
        shape = shape.copy()
        shape.Placement = parent_container.getGlobalPlacement().multiply(shape.Placement)
    return shape


def object_type(document_object):
    """ROMGeometry type string ("ROMModel", "ROMRegion", ...) or None."""
    proxy = getattr(document_object, "Proxy", None)
    return getattr(proxy, "Type", None)


def property_expression(document_object, property_name):
    """The expression bound to a property, or "" if it has none."""
    for bound_property_name, expression in document_object.ExpressionEngine:
        if bound_property_name == property_name:
            return expression
    return ""


def length_property_text(document_object, property_name):
    """What the user typed for a length: its expression, or its value in mm."""
    expression = property_expression(document_object, property_name)
    if expression:
        return expression
    return f"{getattr(document_object, property_name).Value:.12g} mm"


def report_to_console(message, is_problem):
    if is_problem:
        App.Console.PrintWarning(message + "\n")
    else:
        App.Console.PrintMessage(message + "\n")

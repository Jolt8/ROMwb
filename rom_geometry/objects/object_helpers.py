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


def plain_sub_path(sub_path):
    """Drop FreeCAD 1.x's topological-naming part from a selection path.

    Clicking a face gives e.g. "Body.;Face12;:H10eb,F.Face12": the link path
    "Body.", the mapped element name ";Face12;:H10eb,F" and the plain name
    "Face12". The ROM objects store the plain form "Body.Face12" and re-find
    faces by their own geometric fingerprint instead.
    """
    if ";" not in sub_path:
        return sub_path
    object_path = sub_path[:sub_path.index(";")]
    plain_element_name = sub_path.rsplit(".", 1)[-1]
    return object_path + plain_element_name


def split_element_path(sub_path):
    """"Body.Face12" -> ("Body.", "Face12"); "Face3" -> ("", "Face3")."""
    if "." not in sub_path:
        return "", sub_path
    object_path, element_name = sub_path.rsplit(".", 1)
    return object_path + ".", element_name


def owner_shape_global(top_object, object_path):
    """Shape of the object an element belongs to, in global coordinates.

    `object_path` is the link path below `top_object` ("" = top_object
    itself), e.g. "BodyLink." when a face was picked as
    (Assembly, "BodyLink.Face12"). Its faces are numbered exactly like the
    element names in that path, so "Face12" is shape.Faces[11].
    """
    if object_path:
        shape = top_object.getSubObject(object_path)
    else:
        shape = Part.getShape(top_object)
    parent_container = top_object.getParentGeoFeatureGroup()
    if parent_container is not None:
        shape = shape.copy()
        shape.Placement = parent_container.getGlobalPlacement().multiply(shape.Placement)
    return shape


def reference_position(link_sub_value, engine_axis, length_tolerance=1.0e-6):
    """Where a picked reference sits relative to the engine axis.

    `link_sub_value` is what an App::PropertyXLinkSub holds: (top_object,
    [sub_name]). An Assembly4 LCS lives inside a body in its own part
    document; its own Placement is relative to that document. Its position
    in the assembly is only known through the link path the user clicked
    (e.g. link "Body" + "injector_attachment_lcs."), so the reference keeps
    that path and resolves it here.

    What the reference gives:
      LCS / datum / any object   its origin: axial and radial position
      Vertex                     the point: axial and radial position
      planar face (normal along the axis)   axial position only
      coaxial cylindrical face   radius only
      coaxial circular edge      axial position and radius

    Returns (axial_coordinate or None, radial_distance or None, description).
    """
    top_object, sub_names = link_sub_value
    sub_name = sub_names[0] if sub_names else ""
    element_name = sub_name.split(".")[-1] if sub_name else ""
    parent_container = top_object.getParentGeoFeatureGroup()
    description = f"{top_object.Label}.{plain_sub_path(sub_name)}" if sub_name else top_object.Label

    # A face, edge or vertex: getSubObject returns the element shape already
    # moved through the link chain. The top object's own container is applied
    # on top, as in global_shape.
    if element_name.startswith(("Face", "Edge", "Vertex")):
        element_shape = top_object.getSubObject(sub_name)
        if parent_container is not None:
            element_shape = element_shape.copy()
            element_shape.Placement = parent_container.getGlobalPlacement().multiply(element_shape.Placement)

        if element_name.startswith("Vertex"):
            vertex_point = element_shape.Point
            return engine_axis.axial_coordinate(vertex_point), engine_axis.radial_distance(vertex_point), description

        if element_name.startswith("Face"):
            surface = element_shape.Surface
            if isinstance(surface, Part.Plane):
                plane_normal = App.Vector(surface.Axis).normalize()
                if abs(plane_normal.dot(engine_axis.direction)) > 1.0 - 1.0e-9:
                    return engine_axis.axial_coordinate(element_shape.CenterOfMass), None, description
                raise ValueError(f"{description}: planar face is not perpendicular to the engine axis")
            if isinstance(surface, Part.Cylinder):
                cylinder_axis = App.Vector(surface.Axis).normalize()
                is_coaxial = abs(cylinder_axis.dot(engine_axis.direction)) > 1.0 - 1.0e-9 and engine_axis.radial_distance(surface.Center) < length_tolerance
                if is_coaxial:
                    return None, surface.Radius, description
                raise ValueError(f"{description}: cylindrical face is not coaxial with the engine axis")
            raise ValueError(f"{description}: only planar (z) and coaxial cylindrical (r) faces can be used as references")

        curve = element_shape.Curve
        if isinstance(curve, Part.Circle):
            circle_axis = App.Vector(curve.Axis).normalize()
            is_coaxial = abs(circle_axis.dot(engine_axis.direction)) > 1.0 - 1.0e-9 and engine_axis.radial_distance(curve.Center) < length_tolerance
            if is_coaxial:
                return engine_axis.axial_coordinate(curve.Center), curve.Radius, description
        raise ValueError(f"{description}: only coaxial circular edges can be used as references")

    # An object (LCS, datum point, body): its origin, through the link path.
    if sub_name:
        reference_placement = top_object.getSubObject(sub_name, retType=3)
    else:
        reference_placement = top_object.Placement
    if parent_container is not None:
        reference_placement = parent_container.getGlobalPlacement().multiply(reference_placement)

    reference_origin = reference_placement.Base
    return engine_axis.axial_coordinate(reference_origin), engine_axis.radial_distance(reference_origin), description


def reference_element_kind(link_sub_value):
    """What kind of thing a reference points at: "object" (LCS, datum, body),
    "Vertex", "Face:Plane", "Face:Cylinder", "Edge:Circle", "Edge:Line", ..."""
    top_object, sub_names = link_sub_value
    sub_name = sub_names[0] if sub_names else ""
    element_name = sub_name.split(".")[-1] if sub_name else ""
    if not element_name.startswith(("Face", "Edge", "Vertex")):
        return "object"

    element_shape = top_object.getSubObject(sub_name)
    if element_shape is None or element_shape.isNull():
        raise ValueError(f"{top_object.Label}.{sub_name} no longer exists; please re-pick it")
    if element_name.startswith("Vertex"):
        return "Vertex"
    if element_name.startswith("Face"):
        return f"Face:{type(element_shape.Surface).__name__}"
    return f"Edge:{type(element_shape.Curve).__name__}"


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

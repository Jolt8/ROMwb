"""Temporary highlighting of shapes in the 3D view.

Interfaces and unassigned surfaces are faces of the internal generalFuse
result, not document objects, so they cannot be selected. Instead they are
drawn straight into the scene graph, on top of everything (most interfaces
are buried inside the engine), and removed again when the panel closes.
Nothing is added to any document.

The triangles are built directly as Coin nodes from a display-resolution
tessellation. (Going through Shape.writeInventor() produced megabytes of
text for the nozzle cones, and parsing it crashed FreeCAD.)
"""

import FreeCADGui as Gui
from pivy import coin


# Display tessellation: fine enough to look smooth on 10-50 mm parts, coarse
# enough to stay a few thousand triangles per face.
DISPLAY_TESSELLATION_TOLERANCE = 0.05


def triangle_mesh_node(shapes):
    """One SoSeparator with an SoIndexedFaceSet holding every face's triangles."""
    point_coordinates = []
    face_vertex_indices = []

    for shape in shapes:
        for face in shape.Faces:
            face_points, face_triangles = face.tessellate(DISPLAY_TESSELLATION_TOLERANCE)
            first_point_index = len(point_coordinates)
            for face_point in face_points:
                point_coordinates.append((face_point.x, face_point.y, face_point.z))
            for triangle in face_triangles:
                face_vertex_indices.extend([first_point_index + triangle[0], first_point_index + triangle[1], first_point_index + triangle[2], -1])

    coordinates_node = coin.SoCoordinate3()
    coordinates_node.point.setValues(0, len(point_coordinates), point_coordinates)
    faces_node = coin.SoIndexedFaceSet()
    faces_node.coordIndex.setValues(0, len(face_vertex_indices), face_vertex_indices)

    # Triangle winding differs between faces, so light both sides.
    shape_hints = coin.SoShapeHints()
    shape_hints.vertexOrdering = coin.SoShapeHints.UNKNOWN_ORDERING
    shape_hints.shapeType = coin.SoShapeHints.UNKNOWN_SHAPE_TYPE

    mesh_node = coin.SoSeparator()
    mesh_node.addChild(shape_hints)
    mesh_node.addChild(coordinates_node)
    mesh_node.addChild(faces_node)
    return mesh_node


class ShapeHighlighter:

    def __init__(self):
        self.highlight_root = None
        self.scene_graph = None

    def show(self, shapes, color_rgb):
        self.clear()
        if len(shapes) == 0 or Gui.ActiveDocument is None:
            return

        highlight_material = coin.SoMaterial()
        highlight_material.diffuseColor.setValue(*color_rgb)
        highlight_material.emissiveColor.setValue(*[0.6 * component for component in color_rgb])

        # SoAnnotation draws after everything else, so the highlight shows
        # through the solids around it.
        highlight_root = coin.SoAnnotation()
        highlight_root.addChild(highlight_material)
        highlight_root.addChild(triangle_mesh_node(shapes))

        self.scene_graph = Gui.ActiveDocument.ActiveView.getSceneGraph()
        self.scene_graph.addChild(highlight_root)
        self.highlight_root = highlight_root

    def clear(self):
        if self.highlight_root is not None and self.scene_graph is not None:
            self.scene_graph.removeChild(self.highlight_root)
        self.highlight_root = None
        self.scene_graph = None

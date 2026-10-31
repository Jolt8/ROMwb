"""Engine axis and the classification of CAD faces relative to it.

All lengths in the core are FreeCAD's internal unit (mm). Conversion to SI
happens only in export_json.py.
"""

import FreeCAD as App
import Part


# Step used to probe which side of a face the cell material is on. One micron
# is far below any wall thickness in the engine, but far above OCC's
# classifier precision (about 1e-7 mm).
INSIDE_PROBE_STEP = 1.0e-3

# Tessellation tolerance used when a face has no closed-form area vector
# (cones, fillets, B-splines). The polygon area error is roughly
# (tolerance / radius) of the projected area, which is about 1e-4 for the
# engine's 10-20 mm radii.
TESSELLATION_TOLERANCE = 1.0e-3


class EngineAxis:
    """The axis that z-limits, radial limits and cylindrical faces refer to.

    `origin` is a point on the axis and `direction` points towards increasing
    axial coordinate. The default is the global Z axis through the origin.
    """

    def __init__(self, origin=None, direction=None):
        if origin is None:
            origin = App.Vector(0, 0, 0)
        if direction is None:
            direction = App.Vector(0, 0, 1)

        direction = App.Vector(direction)
        if direction.Length == 0:
            raise ValueError("the engine axis direction cannot be a zero vector")

        self.origin = App.Vector(origin)
        self.direction = direction.normalize()

    @classmethod
    def from_placement(cls, placement):
        """Axis = the local Z axis of a placement (e.g. an LCS)."""
        axis_direction = placement.Rotation.multVec(App.Vector(0, 0, 1))
        return cls(placement.Base, axis_direction)

    def axial_coordinate(self, point):
        return (App.Vector(point) - self.origin).dot(self.direction)

    def point_at(self, axial_coordinate):
        return self.origin + self.direction * axial_coordinate

    def radial_vector(self, point):
        offset_from_origin = App.Vector(point) - self.origin
        axial_part = self.direction * offset_from_origin.dot(self.direction)
        return offset_from_origin - axial_part

    def radial_distance(self, point):
        return self.radial_vector(point).Length

    def make_axial_cylinder(self, radius, axial_start, axial_end):
        """Solid cylinder on the axis between two axial coordinates."""
        cylinder_length = axial_end - axial_start
        return Part.makeCylinder(radius, cylinder_length, self.point_at(axial_start), self.direction)

    def make_axis_line(self, axial_start, axial_end):
        return Part.makeLine(self.point_at(axial_start), self.point_at(axial_end))

    def as_dict(self):
        return {"origin": [self.origin.x, self.origin.y, self.origin.z], "direction": [self.direction.x, self.direction.y, self.direction.z]}


def describe_face_surface(face, engine_axis, length_tolerance):
    """Classify the underlying surface of a face relative to the engine axis.

    Returns a dict with:
      surface_type     "plane", "cylinder", "cone", "sphere", "torus",
                       "revolution", "bspline" or "other"
      is_coaxial       True for cylinders/cones/... whose axis is the engine axis
      full_revolution  True if a coaxial face goes all the way round (its area
                       centroid then lies on the axis, so it has no single normal)
      radius           cylinder radius (coaxial cylinders only), mm
      plane_normal     unit normal of the plane (unoriented), planes only
    """
    surface = face.Surface
    description = {"surface_type": "other", "is_coaxial": False, "full_revolution": False, "radius": None, "plane_normal": None}

    if isinstance(surface, Part.Plane):
        description["surface_type"] = "plane"
        description["plane_normal"] = App.Vector(surface.Axis).normalize()
        return description

    surface_axis_direction = None
    surface_axis_point = None

    if isinstance(surface, Part.Cylinder):
        description["surface_type"] = "cylinder"
        surface_axis_direction = surface.Axis
        surface_axis_point = surface.Center
    elif isinstance(surface, Part.Cone):
        description["surface_type"] = "cone"
        surface_axis_direction = surface.Axis
        surface_axis_point = surface.Apex
    elif isinstance(surface, Part.Sphere):
        description["surface_type"] = "sphere"
        surface_axis_direction = engine_axis.direction
        surface_axis_point = surface.Center
    elif isinstance(surface, Part.Toroid):
        description["surface_type"] = "torus"
        surface_axis_direction = surface.Axis
        surface_axis_point = surface.Center
    elif isinstance(surface, Part.SurfaceOfRevolution):
        description["surface_type"] = "revolution"
        surface_axis_direction = surface.Direction
        surface_axis_point = surface.Location
    elif isinstance(surface, (Part.BSplineSurface, Part.BezierSurface)):
        description["surface_type"] = "bspline"
        return description
    else:
        return description

    # A surface of revolution is coaxial when its axis is parallel to the
    # engine axis and passes through it.
    axis_alignment = abs(App.Vector(surface_axis_direction).normalize().dot(engine_axis.direction))
    axis_is_parallel = axis_alignment > 1.0 - 1.0e-9
    axis_offset = engine_axis.radial_distance(surface_axis_point)
    is_coaxial = axis_is_parallel and axis_offset < length_tolerance
    description["is_coaxial"] = is_coaxial

    if is_coaxial:
        # A revolved face that goes all the way round has its area centroid
        # on the axis. A partial (sector) face does not.
        face_centroid_radial_offset = engine_axis.radial_distance(face.CenterOfMass)
        description["full_revolution"] = face_centroid_radial_offset < length_tolerance

    if description["surface_type"] == "cylinder" and is_coaxial:
        description["radius"] = surface.Radius

    return description


def outward_face_samples(face, cell_shape):
    """Points on the face, the outward normal there, and the outward area vector.

    "Outward" means pointing out of `cell_shape`. Because an interface
    sub-face is shared by two cells, its stored orientation only suits one of
    them. So the side is found geometrically: step a micron along the normal
    and ask whether that point is inside the cell.

    Returns (representative_point, outward_normal_at_point, outward_area_vector).
    The representative point is the projection of the centroid of the face's
    largest tessellation triangle onto the surface, so it always lies on the
    face (an area centroid can fall in a hole, e.g. for an annulus).
    The area vector is the sum over tessellation triangles of
    (triangle area x triangle normal), which is exact for planes and close
    for curved faces.
    """
    tessellation_points, tessellation_triangles = face.tessellate(TESSELLATION_TOLERANCE)

    largest_triangle_area = -1.0
    largest_triangle_centroid = None
    tessellated_area_vector = App.Vector(0, 0, 0)

    for triangle in tessellation_triangles:
        point_a = tessellation_points[triangle[0]]
        point_b = tessellation_points[triangle[1]]
        point_c = tessellation_points[triangle[2]]

        triangle_area_vector = (point_b - point_a).cross(point_c - point_a) * 0.5
        tessellated_area_vector = tessellated_area_vector + triangle_area_vector

        triangle_area = triangle_area_vector.Length
        if triangle_area > largest_triangle_area:
            largest_triangle_area = triangle_area
            largest_triangle_centroid = (point_a + point_b + point_c) * (1.0 / 3.0)
            largest_triangle_area_vector = triangle_area_vector

    surface_u, surface_v = face.Surface.parameter(largest_triangle_centroid)
    representative_point = face.Surface.value(surface_u, surface_v)
    surface_normal = App.Vector(face.Surface.normal(surface_u, surface_v)).normalize()

    probe_point = representative_point + surface_normal * INSIDE_PROBE_STEP
    probe_is_inside_cell = cell_shape.isInside(probe_point, 1.0e-9, True)
    if probe_is_inside_cell:
        outward_normal = surface_normal * -1.0
    else:
        outward_normal = surface_normal

    # Tessellation triangles of one face are consistently wound, so one
    # triangle tells us whether the summed area vector points outward.
    if largest_triangle_area_vector.dot(outward_normal) < 0:
        tessellated_area_vector = tessellated_area_vector * -1.0

    return representative_point, outward_normal, tessellated_area_vector


def centroid_sample_on_face(face, engine_axis, sample_point, sample_outward_normal, is_revolved_about_axis):
    """The point on the face's surface "at the face's centroid", and the
    outward normal there. Used for faces with no exact distance rule.

    A face revolved all the way round the axis (a nozzle cone) has its 3D
    centroid on the axis, which is not on the face. For those, use the
    area-weighted meridional centroid (r_mean, z_mean) of the face, placed on
    the same meridian as `sample_point`. For any other face, project the 3D
    centroid onto the surface.

    The normal is oriented to agree with `sample_outward_normal`, which was
    found by probing the cell (see outward_face_samples).
    """
    if is_revolved_about_axis:
        tessellation_points, tessellation_triangles = face.tessellate(TESSELLATION_TOLERANCE)
        total_triangle_area = 0.0
        area_weighted_radius = 0.0
        area_weighted_axial_coordinate = 0.0

        for triangle in tessellation_triangles:
            point_a = tessellation_points[triangle[0]]
            point_b = tessellation_points[triangle[1]]
            point_c = tessellation_points[triangle[2]]
            triangle_area = (point_b - point_a).cross(point_c - point_a).Length * 0.5
            triangle_centroid = (point_a + point_b + point_c) * (1.0 / 3.0)

            total_triangle_area += triangle_area
            area_weighted_radius += triangle_area * engine_axis.radial_distance(triangle_centroid)
            area_weighted_axial_coordinate += triangle_area * engine_axis.axial_coordinate(triangle_centroid)

        meridional_centroid_radius = area_weighted_radius / total_triangle_area
        meridional_centroid_axial_coordinate = area_weighted_axial_coordinate / total_triangle_area
        sample_radial_unit_vector = engine_axis.radial_vector(sample_point).normalize()
        target_point = engine_axis.point_at(meridional_centroid_axial_coordinate) + sample_radial_unit_vector * meridional_centroid_radius
    else:
        target_point = App.Vector(face.CenterOfMass)

    surface_u, surface_v = face.Surface.parameter(target_point)
    centroid_point = face.Surface.value(surface_u, surface_v)
    centroid_normal = App.Vector(face.Surface.normal(surface_u, surface_v)).normalize()
    if centroid_normal.dot(sample_outward_normal) < 0:
        centroid_normal = centroid_normal * -1.0

    return centroid_point, centroid_normal


def shape_volume_and_centroid(shape):
    """(volume, centroid). CenterOfMass is not defined on compounds, so a
    shape made of several disconnected solids gets the volume-weighted
    centroid of its solids."""
    if hasattr(shape, "CenterOfMass"):
        return shape.Volume, App.Vector(shape.CenterOfMass)

    total_volume = 0.0
    volume_moment = App.Vector(0, 0, 0)
    for solid in shape.Solids:
        total_volume += solid.Volume
        volume_moment = volume_moment + App.Vector(solid.CenterOfMass) * solid.Volume
    return total_volume, volume_moment * (1.0 / total_volume)


def total_bounding_box(shapes):
    bounding_box = App.BoundBox()
    for shape in shapes:
        bounding_box.add(shape.BoundBox)
    return bounding_box

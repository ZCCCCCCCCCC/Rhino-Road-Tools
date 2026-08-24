#! python 3
# -*- coding: utf-8 -*-

"""Extract centerlines from elongated greenbelt/road-panel regions.

Select mesh, surface, polysurface, extrusion, or an existing closed outline
curve. Mesh-like objects are meshed internally and their upward-facing outer
boundary is extracted first. The centerline is then calculated from transverse
sections and the midpoint of each valid section.
"""

import math

import Rhino
import Rhino.Geometry as rg
import rhinoscriptsyntax as rs
import scriptcontext as sc


STICKY_PREFIX = "ROAD_TOOLS_GREENBELT_CENTERLINE_"
DEFAULT_TOP_NORMAL_THRESHOLD = 0.8
DEFAULT_SAMPLE_SPACING = 1.0
DEFAULT_MIN_WIDTH = 0.0


def _sticky_key(name):
    return STICKY_PREFIX + name


def _sticky_float(name, default):
    key = _sticky_key(name)
    if key in sc.sticky:
        try:
            return float(sc.sticky[key])
        except Exception:
            pass
    return default


def _save_sticky(name, value):
    sc.sticky[_sticky_key(name)] = value


def mesh_from_object(object_id):
    """Return one mesh for a supported Rhino object."""
    mesh = rs.coercemesh(object_id)
    if mesh is not None:
        return mesh.DuplicateMesh()

    brep = rs.coercebrep(object_id)
    if brep is None:
        return None

    parts = rg.Mesh.CreateFromBrep(brep, rg.MeshingParameters.Default)
    if not parts:
        return None

    combined = rg.Mesh()
    for part in parts:
        combined.Append(part)

    combined.Vertices.CombineIdentical(True, True)
    combined.Weld(math.pi)
    combined.Compact()
    return combined


def mesh_face_normal(mesh, face_index):
    """Compute a face normal without depending on the normal cache."""
    face = mesh.Faces[face_index]
    a = rg.Point3d(mesh.Vertices[face.A])
    b = rg.Point3d(mesh.Vertices[face.B])
    c = rg.Point3d(mesh.Vertices[face.C])

    normal = rg.Vector3d.CrossProduct(b - a, c - a)
    if not face.IsTriangle:
        d = rg.Point3d(mesh.Vertices[face.D])
        normal = normal + rg.Vector3d.CrossProduct(c - a, d - a)
    return normal


def topology_face_vertices(mesh, face):
    vertex_indexes = [face.A, face.B, face.C]
    if not face.IsTriangle:
        vertex_indexes.append(face.D)
    return [
        mesh.TopologyVertices.TopologyVertexIndex(index)
        for index in vertex_indexes
    ]


def closed_loops(directed_edges):
    """Follow boundary edges and keep only complete closed loops."""
    outgoing = {}
    unused = set()
    for start, end in directed_edges:
        edge = (start, end)
        outgoing.setdefault(start, []).append(end)
        unused.add(edge)

    loops = []
    max_steps = len(directed_edges) + 1
    while unused:
        start, current = next(iter(unused))
        unused.remove((start, current))
        loop = [start, current]

        for _ in range(max_steps):
            if current == start:
                loops.append(loop[:-1])
                break

            candidates = [
                end
                for end in outgoing.get(current, [])
                if (current, end) in unused
            ]
            if len(candidates) != 1:
                break

            following = candidates[0]
            unused.remove((current, following))
            current = following
            loop.append(current)

    return [loop for loop in loops if len(loop) >= 3]


def signed_area_xy(mesh, loop):
    area_twice = 0.0
    for index, vertex_index in enumerate(loop):
        current = mesh.TopologyVertices[vertex_index]
        following = mesh.TopologyVertices[loop[(index + 1) % len(loop)]]
        area_twice += current.X * following.Y - following.X * current.Y
    return area_twice * 0.5


def mesh_top_boundary_points(mesh, normal_threshold):
    """Return point lists for outer boundaries of upward-facing top regions."""
    edges = {}
    for face_index in range(mesh.Faces.Count):
        normal = mesh_face_normal(mesh, face_index)
        if not normal.IsValid or normal.Length <= 0.0:
            continue
        if normal.Z / normal.Length < normal_threshold:
            continue

        topology_vertices = topology_face_vertices(mesh, mesh.Faces[face_index])
        for index, start in enumerate(topology_vertices):
            end = topology_vertices[(index + 1) % len(topology_vertices)]
            key = (min(start, end), max(start, end))
            if key not in edges:
                edges[key] = [1, start, end]
            else:
                edges[key][0] += 1

    boundary_edges = [
        (record[1], record[2])
        for record in edges.values()
        if record[0] == 1
    ]
    if not boundary_edges:
        return []

    loops = closed_loops(boundary_edges)
    if not loops:
        return []

    areas = [(signed_area_xy(mesh, loop), loop) for loop in loops]
    tolerance_area = sc.doc.ModelAbsoluteTolerance ** 2
    outer = [loop for area, loop in areas if area > tolerance_area]
    if outer:
        selected = outer
    else:
        # Some imported meshes have the opposite winding. Keep the largest
        # loop instead of rejecting a valid board because of orientation.
        selected = [max(areas, key=lambda item: abs(item[0]))[1]]

    result = []
    for loop in selected:
        points = [
            rg.Point3d(mesh.TopologyVertices[index])
            for index in loop
        ]
        if len(points) >= 3:
            result.append(points)
    return result


def sample_closed_curve(curve, spacing):
    """Sample a closed curve into a 3D boundary point list."""
    length = curve.GetLength()
    if length <= sc.doc.ModelAbsoluteTolerance:
        return []

    sample_step = max(spacing * 0.5, sc.doc.ModelAbsoluteTolerance * 20.0)
    count = max(32, int(math.ceil(length / sample_step)))
    params = list(curve.DivideByCount(count, True) or [])
    if not params:
        return []

    points = [curve.PointAt(parameter) for parameter in params]
    if len(points) > 1 and points[0].DistanceTo(points[-1]) <= sc.doc.ModelAbsoluteTolerance:
        points.pop()
    return points


def _axis_value(point, origin, axis):
    return (
        (point.X - origin.X) * axis.X
        + (point.Y - origin.Y) * axis.Y
    )


def principal_axes(points):
    """Return centroid and major/minor XY axes using 2D PCA."""
    center_x = sum(point.X for point in points) / len(points)
    center_y = sum(point.Y for point in points) / len(points)
    origin = rg.Point3d(center_x, center_y, 0.0)

    xx = yy = xy = 0.0
    for point in points:
        dx = point.X - center_x
        dy = point.Y - center_y
        xx += dx * dx
        yy += dy * dy
        xy += dx * dy

    angle = 0.5 * math.atan2(2.0 * xy, xx - yy)
    major = rg.Vector3d(math.cos(angle), math.sin(angle), 0.0)
    minor = rg.Vector3d(-major.Y, major.X, 0.0)
    return origin, major, minor


def _dedupe_hits(hits, tolerance):
    hits.sort(key=lambda item: item[0])
    result = []
    for value, z_value in hits:
        if result and abs(value - result[-1][0]) <= tolerance:
            previous_value, previous_z = result[-1]
            result[-1] = (
                (previous_value + value) * 0.5,
                (previous_z + z_value) * 0.5,
            )
        else:
            result.append((value, z_value))
    return result


def section_center(boundary_points, origin, major, minor, major_value, tolerance):
    """Intersect a perpendicular section with the boundary and return center."""
    projected = [
        (
            _axis_value(point, origin, major),
            _axis_value(point, origin, minor),
            point.Z,
        )
        for point in boundary_points
    ]

    hits = []
    for index, first in enumerate(projected):
        second = projected[(index + 1) % len(projected)]
        u0, v0, z0 = first
        u1, v1, z1 = second
        du0 = u0 - major_value
        du1 = u1 - major_value

        if abs(du0) <= tolerance:
            hits.append((v0, z0))
        if du0 * du1 < -(tolerance * tolerance):
            ratio = du0 / (du0 - du1)
            hits.append((
                v0 + (v1 - v0) * ratio,
                z0 + (z1 - z0) * ratio,
            ))
        if abs(du1) <= tolerance:
            hits.append((v1, z1))

    hits = _dedupe_hits(hits, tolerance * 2.0)
    if len(hits) < 2:
        return None, 0.0

    intervals = []
    for index in range(0, len(hits) - 1, 2):
        left = hits[index]
        right = hits[index + 1]
        width = right[0] - left[0]
        if width > tolerance:
            intervals.append((width, left, right))
    if not intervals:
        return None, 0.0

    width, left, right = max(intervals, key=lambda item: item[0])
    middle_v = (left[0] + right[0]) * 0.5
    middle_z = (left[1] + right[1]) * 0.5
    point = rg.Point3d(
        origin.X + major.X * major_value + minor.X * middle_v,
        origin.Y + major.Y * major_value + minor.Y * middle_v,
        middle_z,
    )
    return point, width


def centerline_from_boundary(boundary_points, sample_spacing, min_width):
    """Build a centerline from transverse section midpoints."""
    if len(boundary_points) < 3:
        return None, "边界点数量不足"

    origin, major, minor = principal_axes(boundary_points)
    major_values = [
        _axis_value(point, origin, major)
        for point in boundary_points
    ]
    min_major = min(major_values)
    max_major = max(major_values)
    length = max_major - min_major
    if length <= sc.doc.ModelAbsoluteTolerance:
        return None, "板块没有明显的长度方向"

    minor_values = [
        _axis_value(point, origin, minor)
        for point in boundary_points
    ]
    estimated_width = max(minor_values) - min(minor_values)
    width_threshold = max(
        min_width,
        estimated_width * 0.03,
        sc.doc.ModelAbsoluteTolerance * 10.0,
    )

    step = max(sample_spacing, sc.doc.ModelAbsoluteTolerance * 10.0)
    section_count = max(2, int(math.ceil(length / step)) + 1)
    tolerance = max(sc.doc.ModelAbsoluteTolerance * 10.0, length * 1e-8)
    points = []
    for index in range(section_count):
        ratio = float(index) / float(section_count - 1)
        major_value = min_major + length * ratio
        center, width = section_center(
            boundary_points,
            origin,
            major,
            minor,
            major_value,
            tolerance,
        )
        if center is not None and width >= width_threshold:
            points.append(center)

    if len(points) < 2:
        return None, "有效横截面不足，无法生成中线"

    if len(points) >= 4:
        curve = rg.Curve.CreateInterpolatedCurve(points, 3)
    else:
        curve = rg.Polyline(points).ToNurbsCurve()
    if curve is None or not curve.IsValid:
        return None, "中心线曲线创建失败"
    return curve, None


def source_boundaries(object_id, threshold, sample_spacing):
    """Return (boundary_points, source_mesh) pairs for one selected object."""
    curve = rs.coercecurve(object_id)
    if curve is not None:
        if not curve.IsClosed:
            return [], "选择的曲线不是闭合轮廓"
        points = sample_closed_curve(curve, sample_spacing)
        return ([(points, None)] if points else []), None

    mesh = mesh_from_object(object_id)
    if mesh is None or mesh.Faces.Count == 0:
        return [], "无法转换为有效网格"

    boundaries = mesh_top_boundary_points(mesh, threshold)
    if not boundaries:
        return [], "未找到顶部外轮廓"
    return [(points, mesh) for points in boundaries], None


def main():
    supported_filter = (
        rs.filter.mesh
        | rs.filter.surface
        | rs.filter.polysurface
        | rs.filter.curve
    )
    supported_filter |= getattr(rs.filter, "extrusion", 0)
    object_ids = rs.GetObjects(
        "选择绿化带板块或闭合上轮廓线",
        supported_filter,
        preselect=True,
    )
    if not object_ids:
        return

    threshold = rs.GetReal(
        "顶部面最小法线 Z 值（网格/曲面使用）",
        _sticky_float("TOP_NORMAL_THRESHOLD", DEFAULT_TOP_NORMAL_THRESHOLD),
        0.0,
        1.0,
    )
    if threshold is None:
        return

    sample_spacing = rs.GetReal(
        "中心线采样间距",
        _sticky_float("SAMPLE_SPACING", DEFAULT_SAMPLE_SPACING),
        0.01,
        1000.0,
    )
    if sample_spacing is None:
        return

    min_width = rs.GetReal(
        "最小有效宽度（输入 0 自动判断）",
        _sticky_float("MIN_WIDTH", DEFAULT_MIN_WIDTH),
        0.0,
        1000.0,
    )
    if min_width is None:
        return

    _save_sticky("TOP_NORMAL_THRESHOLD", threshold)
    _save_sticky("SAMPLE_SPACING", sample_spacing)
    _save_sticky("MIN_WIDTH", min_width)

    result_ids = []
    failures = []
    rs.EnableRedraw(False)
    try:
        for object_id in object_ids:
            object_name = rs.ObjectName(object_id) or "Greenbelt"
            boundaries, error = source_boundaries(
                object_id,
                threshold,
                sample_spacing,
            )
            if error:
                failures.append("{}：{}".format(object_name, error))
                continue

            source = sc.doc.Objects.FindId(object_id)
            if source is None:
                failures.append("{}：源对象已不存在".format(object_name))
                continue
            attributes = source.Attributes.Duplicate()

            object_result_count = 0
            for index, (boundary_points, _mesh) in enumerate(boundaries, start=1):
                centerline, centerline_error = centerline_from_boundary(
                    boundary_points,
                    sample_spacing,
                    min_width,
                )
                if centerline_error:
                    failures.append(
                        "{}（轮廓 {}）：{}".format(
                            object_name,
                            index,
                            centerline_error,
                        )
                    )
                    continue

                curve_attributes = attributes.Duplicate()
                curve_attributes.Name = "{}_Centerline_{:02d}".format(
                    object_name,
                    index,
                )
                curve_id = sc.doc.Objects.AddCurve(
                    centerline,
                    curve_attributes,
                )
                if curve_id:
                    result_ids.append(curve_id)
                    object_result_count += 1

            if object_result_count == 0 and not error:
                failures.append("{}：未生成有效中线".format(object_name))
    finally:
        rs.EnableRedraw(True)
        sc.doc.Views.Redraw()

    if not result_ids:
        summary = "未能生成绿化带板块中线。"
        if failures:
            summary += "\n\n" + "\n".join(failures)
        rs.MessageBox(summary, 0, "绿化带板块中线")
        return

    group_index = sc.doc.Groups.Add()
    for curve_id in result_ids:
        sc.doc.Groups.AddToGroup(group_index, curve_id)

    rs.UnselectAllObjects()
    rs.SelectObjects(result_ids)
    summary = "已生成 {} 条绿化带板块中线，并已成组。".format(len(result_ids))
    if failures:
        summary += "\n\n以下对象未完成：\n{}".format("\n".join(failures))
    rs.MessageBox(summary, 0, "绿化带板块中线")


if __name__ == "__main__":
    main()

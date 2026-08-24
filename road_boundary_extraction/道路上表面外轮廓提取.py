#! python 3
# -*- coding: utf-8 -*-

"""Extract outer boundaries from upward-facing road surfaces in Rhino 8.

Run in Rhino's ScriptEditor or with RunPythonScript. Select one or more road
meshes, surfaces, polysurfaces, or extrusions. The script meshes every object,
finds faces whose normals face upward, and creates a closed degree-1 NURBS
curve for each outer top boundary. Interior holes are not output.
"""

import math

import Rhino.Geometry as rg
import rhinoscriptsyntax as rs
import scriptcontext as sc


DEFAULT_TOP_NORMAL_THRESHOLD = 0.8


def mesh_from_object(object_id):
    """Return a single welded mesh representing a supported Rhino object."""
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

    # Brep meshing can duplicate vertices on face boundaries. Combine them so
    # adjacent top faces share a single topology edge.
    combined.Vertices.CombineIdentical(True, True)
    combined.Weld(math.pi)
    combined.Compact()
    combined.FaceNormals.ComputeFaceNormals()
    return combined


def topology_face_vertices(mesh, face):
    """Return the face's topology vertex indexes in winding order."""
    vertex_indexes = [face.A, face.B, face.C]
    if not face.IsTriangle:
        vertex_indexes.append(face.D)
    return [mesh.TopologyVertices.TopologyVertexIndex(index) for index in vertex_indexes]


def mesh_face_normal(mesh, face_index):
    """Compute a face normal without relying on the mesh normal cache."""
    face = mesh.Faces[face_index]
    a = rg.Point3d(mesh.Vertices[face.A])
    b = rg.Point3d(mesh.Vertices[face.B])
    c = rg.Point3d(mesh.Vertices[face.C])

    normal = rg.Vector3d.CrossProduct(b - a, c - a)
    if not face.IsTriangle:
        d = rg.Point3d(mesh.Vertices[face.D])
        normal = normal + rg.Vector3d.CrossProduct(c - a, d - a)
    return normal


def upward_boundary_edges(mesh, normal_threshold):
    """Find directed edges belonging to exactly one upward-facing mesh face."""
    edges = {}
    top_face_count = 0

    for face_index in range(mesh.Faces.Count):
        # FaceNormals can be stale or incomplete after mesh conversion,
        # welding, or duplicate/compact operations. Compute from face vertices
        # so a malformed normal cache cannot cause an index-out-of-range error.
        normal = mesh_face_normal(mesh, face_index)
        if not normal.IsValid or normal.Length <= 0.0:
            continue
        if normal.Z / normal.Length < normal_threshold:
            continue

        top_face_count += 1
        topology_vertices = topology_face_vertices(mesh, mesh.Faces[face_index])
        for index, start in enumerate(topology_vertices):
            end = topology_vertices[(index + 1) % len(topology_vertices)]
            key = (min(start, end), max(start, end))
            if key not in edges:
                edges[key] = [1, start, end]
            else:
                edges[key][0] += 1

    return [(record[1], record[2]) for record in edges.values() if record[0] == 1], top_face_count


def closed_loops(directed_edges):
    """Follow directed boundary edges and return only complete, closed loops."""
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

            candidates = [end for end in outgoing.get(current, []) if (current, end) in unused]
            if len(candidates) != 1:
                break

            following = candidates[0]
            unused.remove((current, following))
            current = following
            loop.append(current)

    return [loop for loop in loops if len(loop) >= 3]


def signed_area_xy(mesh, loop):
    """Return the loop area projected to World XY; positive means outer loop."""
    area_twice = 0.0
    for index, vertex_index in enumerate(loop):
        current = mesh.TopologyVertices[vertex_index]
        following = mesh.TopologyVertices[loop[(index + 1) % len(loop)]]
        area_twice += current.X * following.Y - following.X * current.Y
    return area_twice * 0.5


def create_boundary_curve(mesh, loop, attributes, name):
    points = [rg.Point3d(mesh.TopologyVertices[index]) for index in loop]
    points.append(points[0])
    polyline = rg.Polyline(points)
    if not polyline.IsValid or polyline.Count < 4:
        return None

    curve = polyline.ToNurbsCurve()
    attributes.Name = name
    return sc.doc.Objects.AddCurve(curve, attributes)


def source_attributes(object_id):
    source = sc.doc.Objects.FindId(object_id)
    if source is None:
        return None, "道路对象已不存在。"
    return source.Attributes.Duplicate(), None


def extract_boundaries(object_id, threshold):
    """Create all exterior top boundaries for one road object."""
    mesh = mesh_from_object(object_id)
    if mesh is None or mesh.Faces.Count == 0:
        return [], "无法转换为有效网格"

    boundary_edges, top_face_count = upward_boundary_edges(mesh, threshold)
    if top_face_count == 0:
        return [], "未找到满足法线条件的顶部面"
    if not boundary_edges:
        return [], "未找到顶部面的边界边"

    loops = closed_loops(boundary_edges)
    tolerance_area = sc.doc.ModelAbsoluteTolerance ** 2
    outer_loops = [loop for loop in loops if signed_area_xy(mesh, loop) > tolerance_area]
    if not outer_loops:
        return [], "未能形成有效的顶部外轮廓"

    attributes, error = source_attributes(object_id)
    if error:
        return [], error

    source_name = rs.ObjectName(object_id) or "Road"
    result_ids = []
    for index, loop in enumerate(outer_loops, start=1):
        curve_attributes = attributes.Duplicate()
        name = "{}_TopBoundary_{:02d}".format(source_name, index)
        curve_id = create_boundary_curve(mesh, loop, curve_attributes, name)
        if curve_id:
            result_ids.append(curve_id)

    if not result_ids:
        return [], "边界已识别，但闭合曲线创建失败"
    return result_ids, None


def main():
    supported_filter = rs.filter.mesh | rs.filter.surface | rs.filter.polysurface
    supported_filter |= getattr(rs.filter, "extrusion", 0)
    object_ids = rs.GetObjects(
        "选择一个或多个道路板块（Mesh、曲面、多重曲面或挤出体）",
        supported_filter,
        preselect=True,
    )
    if not object_ids:
        return

    threshold = rs.GetReal(
        "顶部面最小法线 Z 值（0 到 1，坡面可适当调低）",
        DEFAULT_TOP_NORMAL_THRESHOLD,
        0.0,
        1.0,
    )
    if threshold is None:
        return

    result_ids = []
    failures = []
    rs.EnableRedraw(False)
    try:
        for object_id in object_ids:
            object_result_ids, error = extract_boundaries(object_id, threshold)
            if error:
                object_name = rs.ObjectName(object_id) or str(object_id)
                failures.append("{}：{}".format(object_name, error))
            else:
                result_ids.extend(object_result_ids)
    finally:
        rs.EnableRedraw(True)
        sc.doc.Views.Redraw()

    if not result_ids:
        summary = "未能为所选道路板块创建顶部外轮廓。"
        if failures:
            summary += "\n\n" + "\n".join(failures)
        rs.MessageBox(summary, 0, "道路顶部外轮廓")
        return

    rs.UnselectAllObjects()
    rs.SelectObjects(result_ids)
    summary = "已处理 {} 个道路板块，生成 {} 条顶部外轮廓。".format(
        len(object_ids), len(result_ids)
    )
    if failures:
        summary += "\n\n以下对象未生成轮廓：\n{}".format("\n".join(failures))
    rs.MessageBox(summary, 0, "道路顶部外轮廓")


if __name__ == "__main__":
    main()

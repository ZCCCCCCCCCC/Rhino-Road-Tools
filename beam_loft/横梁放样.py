#! python 3
# -*- coding: utf-8 -*-

"""从左右立柱对象生成横梁。

流程：分别选择左右立柱对象或图块，自动提取朝向另一根立柱的封闭端面外环，
统一截面方向和闭合曲线接缝后 Loft，并封口生成实体。
"""

import Rhino
import Rhino.Geometry as rg
import scriptcontext as sc
import System
from System.Collections.Generic import List


TOL = sc.doc.ModelAbsoluteTolerance
MAX_INVALID_PICK_COUNT = 5
SEAM_SAMPLE_COUNT = 16
TOP_FACE_BAND_RATIO = 0.35


def _join_edges(edges):
    """Join 外环边，返回唯一闭合曲线。"""
    if not edges:
        return None
    joined = rg.Curve.JoinCurves(list(edges), TOL)
    if joined is None or len(joined) == 0:
        return None
    closed = [c for c in joined if c is not None and c.IsValid and c.IsClosed]
    if len(closed) != 1:
        return None
    return closed[0]


def _profile_from_face(face):
    """从 BrepFace 提取外轮廓，并确保为平面闭合曲线。"""
    if face is None:
        return None

    # 等价于 Rhino 里的 DupFaceBorder：复制单面 Brep 后取外侧 naked edge。
    profile = None
    face_brep = face.DuplicateFace(False)
    if face_brep is not None and face_brep.IsValid:
        try:
            profile = _join_edges(face_brep.DuplicateNakedEdgeCurves(True, False))
        except Exception:
            profile = None

    # 备用路径：从外环 trim 对应边界曲线拼出轮廓。
    loop = face.OuterLoop
    if profile is None and loop is not None:
        edge_curves = []
        try:
            for trim in loop.Trims:
                edge = trim.Edge
                if edge is not None and edge.EdgeCurve is not None:
                    edge_curves.append(edge.EdgeCurve.DuplicateCurve())
        except Exception:
            edge_curves = []
        profile = _join_edges(edge_curves)
        if profile is None:
            profile = loop.To3dCurve()

    if profile is None or not profile.IsValid or not profile.IsClosed:
        return None

    ok, plane = profile.TryGetPlane()
    if not ok:
        return None
    try:
        face_ok, face_plane = face.TryGetPlane()
        if face_ok:
            plane = face_plane
    except Exception:
        pass
    return profile, plane


def _profile_center(profile):
    mass = rg.AreaMassProperties.Compute(profile)
    if mass is not None:
        return mass.Centroid, mass.Area

    bbox = profile.GetBoundingBox(True)
    if bbox.IsValid:
        return bbox.Center, 0.0
    return rg.Point3d.Unset, 0.0


def _breps_center(breps):
    bbox = rg.BoundingBox.Empty
    for brep in breps:
        if brep is None or not brep.IsValid:
            continue
        bbox.Union(brep.GetBoundingBox(True))
    if bbox.IsValid:
        return bbox.Center
    return rg.Point3d.Unset


def _duplicate_brep(geometry, xform):
    brep = None
    if isinstance(geometry, rg.Brep):
        brep = geometry.DuplicateBrep()
    elif isinstance(geometry, rg.Extrusion):
        brep = geometry.ToBrep()
    elif isinstance(geometry, rg.Surface):
        brep = geometry.ToBrep()

    if brep is None or not brep.IsValid:
        return None
    brep.Transform(xform)
    return brep


def _compose_xform(parent_xform, child_xform):
    try:
        return parent_xform * child_xform
    except TypeError:
        return rg.Transform.Multiply(parent_xform, child_xform)


def _collect_breps_from_rhino_object(rhino_object, xform, breps):
    if rhino_object is None:
        return

    if isinstance(rhino_object, Rhino.DocObjects.InstanceObject):
        instance_definition = rhino_object.InstanceDefinition
        if instance_definition is None:
            return
        nested_xform = _compose_xform(xform, rhino_object.InstanceXform)
        for definition_object in instance_definition.GetObjects():
            _collect_breps_from_rhino_object(definition_object, nested_xform, breps)
        return

    brep = _duplicate_brep(rhino_object.Geometry, xform)
    if brep is not None:
        breps.append(brep)


def _breps_from_objref(objref):
    breps = []

    face = objref.Face()
    if face is not None:
        brep = face.DuplicateFace(False)
        if brep is not None and brep.IsValid:
            breps.append(brep)
        return breps

    rhino_object = objref.Object()
    if rhino_object is not None:
        _collect_breps_from_rhino_object(rhino_object, rg.Transform.Identity, breps)
        return breps

    brep = objref.Brep()
    if brep is not None and brep.IsValid:
        breps.append(brep.DuplicateBrep())
    return breps


def _profile_distance_to_point(profile, point):
    if point is None or not point.IsValid:
        center, _ = _profile_center(profile)
        if center.IsValid:
            point = center
        else:
            return System.Double.MaxValue

    ok, t = profile.ClosestPoint(point)
    if ok:
        return point.DistanceTo(profile.PointAt(t))

    center, _ = _profile_center(profile)
    if center.IsValid:
        return point.DistanceTo(center)
    return System.Double.MaxValue


def _pick_endpoint_profile_from_breps(breps, target_point, pick_point):
    candidates = []
    band_candidates = []

    for brep in breps:
        if brep is None or not brep.IsValid:
            continue
        bbox = brep.GetBoundingBox(True)
        if not bbox.IsValid:
            continue
        z_min = bbox.Min.Z
        z_max = bbox.Max.Z
        z_cutoff = z_min + (z_max - z_min) * (1.0 - TOP_FACE_BAND_RATIO)

        for face in brep.Faces:
            data = _profile_from_face(face)
            if data is None:
                continue
            profile, plane = data

            profile_bbox = profile.GetBoundingBox(True)
            if not profile_bbox.IsValid:
                continue
            center, area = _profile_center(profile)
            if not center.IsValid:
                continue
            in_band = center.Z >= z_cutoff

            angle_score = 0.0
            if target_point is not None and target_point.IsValid:
                target_vector = target_point - center
                if target_vector.Length > TOL:
                    target_vector.Unitize()
                    normal = rg.Vector3d(plane.Normal)
                    if normal.Length > TOL:
                        normal.Unitize()
                        angle_score = rg.Vector3d.Multiply(normal, target_vector)

            distance = _profile_distance_to_point(profile, pick_point)
            target_distance = center.DistanceTo(target_point) if target_point is not None and target_point.IsValid else 0.0
            item = (area, -angle_score, target_distance, distance, center.Z, profile, plane)
            candidates.append(item)
            if in_band:
                band_candidates.append(item)

    pool = band_candidates if band_candidates else candidates
    if not pool:
        return None

    # 先看面法线是否朝向另一根立柱，再用距离和面积兜底。
    pool.sort(key=lambda item: (item[0], item[1], item[2], item[3], -item[4]))
    profile, plane = pool[0][5], pool[0][6]
    return profile, plane


def _pick_object(prompt):
    go = Rhino.Input.Custom.GetObject()
    go.SetCommandPrompt(prompt + "（选择立柱对象/图块，按 Esc 取消）")
    go.GeometryFilter = Rhino.DocObjects.ObjectType.Surface
    try:
        go.GeometryFilter = go.GeometryFilter | Rhino.DocObjects.ObjectType.PolysrfFilter
    except AttributeError:
        go.GeometryFilter = go.GeometryFilter | Rhino.DocObjects.ObjectType.Brep
    try:
        go.GeometryFilter = go.GeometryFilter | Rhino.DocObjects.ObjectType.InstanceReference
    except AttributeError:
        pass
    go.EnablePreSelect(False, True)
    try:
        go.EnableGroupSelect(False)
    except AttributeError:
        try:
            go.GroupSelect = False
        except AttributeError:
            pass
    go.SubObjectSelect = False
    go.AcceptNothing(False)

    invalid_count = 0
    while invalid_count < MAX_INVALID_PICK_COUNT:
        result = go.Get()
        if result != Rhino.Input.GetResult.Object:
            return None
        objref = go.Object(0)
        breps = _breps_from_objref(objref)
        if breps:
            pick_point = objref.SelectionPoint()
            center = _breps_center(breps)
            return {
                "objref": objref,
                "breps": breps,
                "pick_point": pick_point,
                "center": center,
            }
        invalid_count += 1
        print("未能从该对象/图块中读取几何，请重新选择（{} / {}）。".format(
            invalid_count,
            MAX_INVALID_PICK_COUNT,
        ))
        try:
            go.ClearObjects()
        except AttributeError:
            pass

    print("连续选择无效，横梁放样已取消。")
    return None


def _sample_curve_points(curve, count):
    points = []
    if curve is None or not curve.IsValid:
        return points

    domain = curve.Domain
    for index in range(count):
        parameter = domain.ParameterAt(float(index) / float(count))
        points.append(curve.PointAt(parameter))
    return points


def _curve_pair_score(first, second, count):
    first_points = _sample_curve_points(first, count)
    second_points = _sample_curve_points(second, count)
    if len(first_points) != len(second_points) or not first_points:
        return System.Double.MaxValue

    total = 0.0
    for index, point in enumerate(first_points):
        total += point.DistanceTo(second_points[index])
    return total


def _closed_curve_seam_candidates(curve):
    candidates = [curve.Domain.Min]

    division_params = curve.DivideByCount(SEAM_SAMPLE_COUNT, False)
    if division_params:
        candidates.extend(list(division_params))

    unique = []
    for parameter in candidates:
        duplicate = False
        for existing in unique:
            if abs(parameter - existing) <= TOL:
                duplicate = True
                break
        if not duplicate:
            unique.append(parameter)
    return unique


def _best_aligned_profile_pair(left, right):
    best = None
    best_score = System.Double.MaxValue

    for reverse_right in (False, True):
        candidate_right = right.DuplicateCurve()
        if reverse_right:
            candidate_right.Reverse()

        for parameter in _closed_curve_seam_candidates(candidate_right):
            aligned_right = candidate_right.DuplicateCurve()
            try:
                aligned_right.ChangeClosedCurveSeam(parameter)
            except Exception:
                continue

            score = _curve_pair_score(left, aligned_right, SEAM_SAMPLE_COUNT)
            if score < best_score:
                best_score = score
                best = aligned_right

    if best is None:
        return left, right
    return left, best


def _normalise_profiles(left_data, right_data):
    """统一法向和 seam，避免 Loft 扭曲。"""
    left, left_plane = left_data
    right, right_plane = right_data

    # 两个截面法向相反时反转第二条曲线，使 Loft 的对应方向一致。
    if rg.Vector3d.Multiply(left_plane.Normal, right_plane.Normal) < 0:
        right.Reverse()

    return _best_aligned_profile_pair(left, right)


def _loft_profiles(left, right):
    curves = List[rg.Curve]()
    curves.Add(left)
    curves.Add(right)
    lofts = rg.Brep.CreateFromLoft(
        curves,
        rg.Point3d.Unset,
        rg.Point3d.Unset,
        rg.LoftType.Straight,
        False,
    )
    if lofts is None or len(lofts) == 0:
        return None

    # 多数情况下返回一个 Brep；兼容 Rhino 返回多个结果的情况。
    brep = lofts[0]
    if brep is None or not brep.IsValid:
        return None
    if not brep.IsSolid:
        capped = brep.CapPlanarHoles(TOL)
        if capped is not None and capped.IsValid:
            brep = capped
    if not brep.IsSolid:
        return None
    return brep


def main():
    left_selection = _pick_object("1/2: 选择左侧立柱")
    if left_selection is None:
        return
    right_selection = _pick_object("2/2: 选择右侧立柱")
    if right_selection is None:
        return

    left_data = _pick_endpoint_profile_from_breps(
        left_selection["breps"],
        right_selection["center"],
        left_selection["pick_point"],
    )
    right_data = _pick_endpoint_profile_from_breps(
        right_selection["breps"],
        left_selection["center"],
        right_selection["pick_point"],
    )
    if left_data is None or right_data is None:
        print("未能自动识别立柱的连接端面，请确认对象里有可用的平面封闭端面。")
        return

    left, right = _normalise_profiles(left_data, right_data)
    beam = _loft_profiles(left, right)
    if beam is None:
        print("Loft 失败：请确认两个连接端面都是平面封闭截面，且轮廓没有自交。")
        return

    obj_id = sc.doc.Objects.AddBrep(beam)
    if obj_id == System.Guid.Empty:
        print("横梁创建失败。")
        return
    sc.doc.Views.Redraw()
    print("横梁 Loft 创建完成。")


if __name__ == "__main__":
    main()

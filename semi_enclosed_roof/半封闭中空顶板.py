#! python 3
# -*- coding: utf-8 -*-

"""
选择两条三维边线，生成中分线，并在中分线两侧各留出 2 米空隙：
    左外边线  <->  中分线左偏 2m
    中分线右偏 2m  <->  右外边线

两块 Loft 顶板会被写入既有图层：声屏障模型::顶。
原始边线不会被修改，也不会创建辅助曲线或组。
"""

import math

import Rhino
import Rhino.Geometry as rg
import rhinoscriptsyntax as rs
import scriptcontext as sc
from System.Collections.Generic import List


TOP_LAYER_PATH = "声屏障模型::顶"
OPENING_HALF_WIDTH_METERS = 2.0


def meters_to_document_units(value_in_meters):
    return value_in_meters * Rhino.RhinoMath.UnitScale(
        Rhino.UnitSystem.Meters,
        sc.doc.ModelUnitSystem
    )


def align_curve_directions(first_curve, second_curve):
    """复制曲线，并让两条曲线的起点和终点互相对应。"""
    first = first_curve.DuplicateCurve()
    second = second_curve.DuplicateCurve()

    same_direction_distance = (
        first.PointAtStart.DistanceTo(second.PointAtStart) +
        first.PointAtEnd.DistanceTo(second.PointAtEnd)
    )
    reversed_direction_distance = (
        first.PointAtStart.DistanceTo(second.PointAtEnd) +
        first.PointAtEnd.DistanceTo(second.PointAtStart)
    )

    if reversed_direction_distance < same_direction_distance:
        second.Reverse()

    return first, second


def sample_count_for(curve_a, curve_b):
    """按约 1 米间距采样，同时限制复杂度。"""
    sample_step = max(meters_to_document_units(1.0), sc.doc.ModelAbsoluteTolerance * 10.0)
    longest_length = max(curve_a.GetLength(), curve_b.GetLength())
    return max(24, min(500, int(math.ceil(longest_length / sample_step))))


def make_interpolated_curve(points):
    point_list = List[rg.Point3d]()
    for point in points:
        point_list.Add(point)

    degree = 3 if len(points) >= 4 else 1
    return rg.Curve.CreateInterpolatedCurve(point_list, degree)


def create_center_curve(first_curve, second_curve):
    """用两条边线等弧长对应点的中点拟合三维中分线。"""
    sample_count = sample_count_for(first_curve, second_curve)
    first_parameters = first_curve.DivideByCount(sample_count, True)
    second_parameters = second_curve.DivideByCount(sample_count, True)

    if not first_parameters or not second_parameters:
        return None

    center_points = []
    for first_parameter, second_parameter in zip(first_parameters, second_parameters):
        first_point = first_curve.PointAt(first_parameter)
        second_point = second_curve.PointAt(second_parameter)
        center_points.append(rg.Point3d(
            (first_point.X + second_point.X) * 0.5,
            (first_point.Y + second_point.Y) * 0.5,
            (first_point.Z + second_point.Z) * 0.5
        ))

    return make_interpolated_curve(center_points)


def horizontal_left_vector(curve):
    """取得路径起点附近的水平左侧方向。"""
    parameters = curve.DivideByCount(12, True)
    if not parameters:
        return None

    for parameter in parameters:
        tangent = curve.TangentAt(parameter)
        horizontal_tangent = rg.Vector3d(tangent.X, tangent.Y, 0.0)
        if horizontal_tangent.Length <= 1e-9:
            continue

        horizontal_tangent.Unitize()
        left = rg.Vector3d.CrossProduct(rg.Vector3d.ZAxis, horizontal_tangent)
        left.Unitize()
        return left

    return None


def classify_left_and_right(first_curve, second_curve, center_curve):
    """按中分线的前进方向判断哪条边线位于左侧。"""
    left_vector = horizontal_left_vector(center_curve)
    if left_vector is None:
        return None, None

    center_start = center_curve.PointAtStart
    first_distance_to_left = rg.Vector3d.Multiply(
        first_curve.PointAtStart - center_start,
        left_vector
    )
    second_distance_to_left = rg.Vector3d.Multiply(
        second_curve.PointAtStart - center_start,
        left_vector
    )

    if first_distance_to_left >= second_distance_to_left:
        return first_curve, second_curve
    return second_curve, first_curve


def offset_center_horizontally(center_curve, offset_distance):
    """按世界 XY 水平法向，生成中分线左右两条等距偏移线。"""
    sample_count = max(24, min(500, int(math.ceil(center_curve.GetLength() / max(
        meters_to_document_units(1.0),
        sc.doc.ModelAbsoluteTolerance * 10.0
    )))))
    parameters = center_curve.DivideByCount(sample_count, True)
    if not parameters:
        return None, None

    left_points = []
    right_points = []
    previous_left = None

    for parameter in parameters:
        point = center_curve.PointAt(parameter)
        tangent = center_curve.TangentAt(parameter)
        horizontal_tangent = rg.Vector3d(tangent.X, tangent.Y, 0.0)

        if horizontal_tangent.Length > 1e-9:
            horizontal_tangent.Unitize()
            current_left = rg.Vector3d.CrossProduct(rg.Vector3d.ZAxis, horizontal_tangent)
            current_left.Unitize()
            previous_left = current_left
        elif previous_left is not None:
            current_left = previous_left
        else:
            return None, None

        left_points.append(point + current_left * offset_distance)
        right_points.append(point - current_left * offset_distance)

    return make_interpolated_curve(left_points), make_interpolated_curve(right_points)


def create_roof_loft(edge_curve, opening_edge_curve):
    loft_curves = List[rg.Curve]()
    loft_curves.Add(edge_curve)
    loft_curves.Add(opening_edge_curve)

    return rg.Brep.CreateFromLoft(
        loft_curves,
        rg.Point3d.Unset,
        rg.Point3d.Unset,
        rg.LoftType.Straight,
        False
    )


def get_top_layer_index():
    layer_index = sc.doc.Layers.FindByFullPath(TOP_LAYER_PATH, -1)
    if layer_index < 0:
        return None
    return sc.doc.Layers[layer_index].Index


def add_roof_panels(breps, layer_index):
    attributes = Rhino.DocObjects.ObjectAttributes()
    attributes.LayerIndex = layer_index

    result_ids = []
    for brep in breps:
        if brep is None:
            continue
        object_id = sc.doc.Objects.AddBrep(brep, attributes)
        if object_id:
            result_ids.append(object_id)
    return result_ids


def select_two_edges_or_curves():
    """支持独立曲线，以及 Brep 或多重曲面的边缘子对象。"""
    selector = Rhino.Input.Custom.GetObject()
    selector.SetCommandPrompt("请选择两条三维边线（可选曲线或 Brep 边缘，顺序不限）")
    selector.GeometryFilter = (
        Rhino.DocObjects.ObjectType.Curve |
        Rhino.DocObjects.ObjectType.EdgeFilter
    )
    selector.SubObjectSelect = True
    selector.EnablePreSelect(True, True)

    result = selector.GetMultiple(2, 2)
    if result != Rhino.Input.GetResult.Object:
        return None
    if selector.ObjectCount != 2:
        return None

    curves = []
    for index in range(selector.ObjectCount):
        curve = selector.Object(index).Curve()
        if curve is None:
            return None
        curves.append(curve)

    return curves


def main():
    top_layer_index = get_top_layer_index()
    if top_layer_index is None:
        print("未找到目标图层“{}”。".format(TOP_LAYER_PATH))
        return

    selected_curves = select_two_edges_or_curves()
    if selected_curves is None:
        print("必须恰好选择两条有效的曲线或边缘。")
        return

    first_curve, second_curve = selected_curves

    first_curve, second_curve = align_curve_directions(first_curve, second_curve)
    center_curve = create_center_curve(first_curve, second_curve)
    if center_curve is None:
        print("无法生成中分线。请检查边线是否有效。")
        return

    left_edge, right_edge = classify_left_and_right(first_curve, second_curve, center_curve)
    if left_edge is None:
        print("无法确定边线的左右方向：中分线不能完全竖直。")
        return

    half_width = meters_to_document_units(OPENING_HALF_WIDTH_METERS)
    left_opening_edge, right_opening_edge = offset_center_horizontally(center_curve, half_width)
    if left_opening_edge is None or right_opening_edge is None:
        print("无法生成水平偏移线：中分线不能完全竖直。")
        return

    left_roof = create_roof_loft(left_edge, left_opening_edge)
    right_roof = create_roof_loft(right_opening_edge, right_edge)
    roof_breps = []
    if left_roof:
        roof_breps.extend(left_roof)
    if right_roof:
        roof_breps.extend(right_roof)

    if not roof_breps:
        print("Loft 未生成结果。请检查两条边线是否具有相近的起终点。")
        return

    rs.EnableRedraw(False)
    try:
        result_ids = add_roof_panels(roof_breps, top_layer_index)
    finally:
        rs.EnableRedraw(True)
        sc.doc.Views.Redraw()

    if result_ids:
        rs.SelectObjects(result_ids)
        print("已生成 {} 块顶板，中间净空宽度为 4 米。".format(len(result_ids)))
    else:
        print("顶板已计算完成，但未能写入图层“{}”。".format(TOP_LAYER_PATH))


if __name__ == "__main__":
    main()

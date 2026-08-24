#! python 3
# -*- coding: utf-8 -*-

"""沿三维曲线或 Brep 边线生成矩形檩条实体。

截面保持 Roadlike Top 姿态：
    截面宽度方向 -> 曲线前进方向的水平左侧
    截面高度方向 -> 世界 Z 轴
"""

import math

import Rhino
import Rhino.Geometry as rg
import scriptcontext as sc
import System
from System.Collections.Generic import List


TOL = sc.doc.ModelAbsoluteTolerance
STICKY_PREFIX = "ROAD_TOOLS_PURLIN_"


def _sticky_value(name, default):
    key = STICKY_PREFIX + name
    if key in sc.sticky:
        try:
            return float(sc.sticky[key])
        except Exception:
            pass
    return default


def _save_sticky(name, value):
    sc.sticky[STICKY_PREFIX + name] = float(value)


def _curve_filter(rhino_object, geometry, component_index):
    return isinstance(geometry, rg.Curve)


def _pick_rails():
    width = _sticky_value("WIDTH", 0.20)
    height = _sticky_value("HEIGHT", 0.12)
    horizontal_offset = _sticky_value("H_OFFSET", 0.0)
    vertical_offset = _sticky_value("V_OFFSET", 0.0)
    sample_spacing = _sticky_value("SAMPLE_SPACING", 1.0)

    while True:
        go = Rhino.Input.Custom.GetObject()
        go.SetCommandPrompt("选择沿线檩条路径曲线/边线，可多选；回车确认参数")
        go.GeometryFilter = Rhino.DocObjects.ObjectType.Curve
        try:
            go.GeometryFilter = go.GeometryFilter | Rhino.DocObjects.ObjectType.EdgeFilter
        except AttributeError:
            pass
        go.SubObjectSelect = True
        go.SetCustomGeometryFilter(_curve_filter)
        go.EnablePreSelect(True, True)
        go.AcceptNothing(False)

        opt_width = Rhino.Input.Custom.OptionDouble(width, TOL, 1000000.0)
        opt_height = Rhino.Input.Custom.OptionDouble(height, TOL, 1000000.0)
        opt_h_offset = Rhino.Input.Custom.OptionDouble(horizontal_offset, -1000000.0, 1000000.0)
        opt_v_offset = Rhino.Input.Custom.OptionDouble(vertical_offset, -1000000.0, 1000000.0)
        opt_spacing = Rhino.Input.Custom.OptionDouble(sample_spacing, TOL, 1000000.0)

        go.AddOptionDouble("Width_宽度", opt_width)
        go.AddOptionDouble("Height_高度", opt_height)
        go.AddOptionDouble("HOffset_水平偏移", opt_h_offset)
        go.AddOptionDouble("VOffset_竖向偏移", opt_v_offset)
        go.AddOptionDouble("Step_采样间距", opt_spacing)

        result = go.GetMultiple(1, 0)
        if result == Rhino.Input.GetResult.Object:
            rails = []
            for index in range(go.ObjectCount):
                curve = go.Object(index).Curve()
                if curve is not None and curve.IsValid:
                    rails.append(curve.DuplicateCurve())
            if not rails:
                print("没有选到有效曲线或边线。")
                return None

            _save_sticky("WIDTH", width)
            _save_sticky("HEIGHT", height)
            _save_sticky("H_OFFSET", horizontal_offset)
            _save_sticky("V_OFFSET", vertical_offset)
            _save_sticky("SAMPLE_SPACING", sample_spacing)

            return rails, width, height, horizontal_offset, vertical_offset, sample_spacing

        if result == Rhino.Input.GetResult.Option:
            width = opt_width.CurrentValue
            height = opt_height.CurrentValue
            horizontal_offset = opt_h_offset.CurrentValue
            vertical_offset = opt_v_offset.CurrentValue
            sample_spacing = opt_spacing.CurrentValue
            continue

        return None


def _sample_parameters(curve, sample_spacing):
    length = curve.GetLength()
    if length <= TOL:
        return []

    if curve.IsClosed:
        count = max(8, min(500, int(math.ceil(length / sample_spacing))))
        return list(curve.DivideByCount(count, False) or [])

    count = max(2, min(500, int(math.ceil(length / sample_spacing)) + 1))
    domain = curve.Domain
    return [
        domain.ParameterAt(float(index) / float(count - 1))
        for index in range(count)
    ]


def _horizontal_forward(curve, parameter, previous_forward):
    tangent = curve.TangentAt(parameter)
    forward = rg.Vector3d(tangent.X, tangent.Y, 0.0)
    if forward.Length <= TOL:
        if previous_forward is not None and previous_forward.Length > TOL:
            return rg.Vector3d(previous_forward)
        forward = rg.Vector3d.XAxis
    forward.Unitize()
    return forward


def _rectangle_at(point, forward, width, height, horizontal_offset, vertical_offset):
    left = rg.Vector3d.CrossProduct(rg.Vector3d.ZAxis, forward)
    if left.Length <= TOL:
        left = rg.Vector3d.YAxis
    left.Unitize()

    center = point + left * horizontal_offset + rg.Vector3d.ZAxis * vertical_offset
    half_width = width * 0.5
    half_height = height * 0.5

    points = [
        center - left * half_width - rg.Vector3d.ZAxis * half_height,
        center + left * half_width - rg.Vector3d.ZAxis * half_height,
        center + left * half_width + rg.Vector3d.ZAxis * half_height,
        center - left * half_width + rg.Vector3d.ZAxis * half_height,
        center - left * half_width - rg.Vector3d.ZAxis * half_height,
    ]
    polyline = rg.Polyline(points)
    return rg.PolylineCurve(polyline)


def _create_purlin(rail, width, height, horizontal_offset, vertical_offset, sample_spacing):
    parameters = _sample_parameters(rail, sample_spacing)
    if len(parameters) < 2:
        return None

    sections = List[rg.Curve]()
    previous_forward = None
    for parameter in parameters:
        forward = _horizontal_forward(rail, parameter, previous_forward)
        previous_forward = forward
        section = _rectangle_at(
            rail.PointAt(parameter),
            forward,
            width,
            height,
            horizontal_offset,
            vertical_offset,
        )
        sections.Add(section)

    breps = rg.Brep.CreateFromLoft(
        sections,
        rg.Point3d.Unset,
        rg.Point3d.Unset,
        rg.LoftType.Straight,
        rail.IsClosed,
    )
    if breps is None or len(breps) == 0:
        return None

    result = breps[0]
    if result is None or not result.IsValid:
        return None

    if not result.IsSolid:
        capped = result.CapPlanarHoles(TOL)
        if capped is not None and capped.IsValid:
            result = capped
    return result if result.IsValid else None


def main():
    picked = _pick_rails()
    if picked is None:
        return

    rails, width, height, h_offset, v_offset, sample_spacing = picked
    attributes = Rhino.DocObjects.ObjectAttributes()
    attributes.LayerIndex = sc.doc.Layers.CurrentLayerIndex

    created = 0
    sc.doc.Views.RedrawEnabled = False
    try:
        for rail in rails:
            purlin = _create_purlin(rail, width, height, h_offset, v_offset, sample_spacing)
            if purlin is None:
                print("有一条路径未能生成檩条，请检查曲线是否有效。")
                continue
            object_id = sc.doc.Objects.AddBrep(purlin, attributes)
            if object_id != System.Guid.Empty:
                created += 1
    finally:
        sc.doc.Views.RedrawEnabled = True
        sc.doc.Views.Redraw()

    print("沿线檩条创建完成：{} 个。".format(created))


if __name__ == "__main__":
    main()

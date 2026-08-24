#! python 3
# -*- coding: utf-8 -*-

"""沿声屏障横断面均匀布置沿线路方向的连续檩条。

用法：
    1. 选择路线曲线/边线
    2. 选择声屏障横断面线
    3. 在横断面线上选择起点和终点
    4. 脚本沿横断面线指定范围均匀取点，每个点生成一根顺路线连续延伸的矩形檩条
"""

import math

import Rhino
import Rhino.Display as rd
import Rhino.Geometry as rg
import scriptcontext as sc
import System
from System.Collections.Generic import List


TOL = sc.doc.ModelAbsoluteTolerance
STICKY_PREFIX = "ROAD_TOOLS_SECTION_PURLIN_"


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


def _pick_curve(prompt):
    go = Rhino.Input.Custom.GetObject()
    go.SetCommandPrompt(prompt)
    go.GeometryFilter = Rhino.DocObjects.ObjectType.Curve
    try:
        go.GeometryFilter = go.GeometryFilter | Rhino.DocObjects.ObjectType.EdgeFilter
    except AttributeError:
        pass
    go.SubObjectSelect = True
    go.SetCustomGeometryFilter(_curve_filter)
    go.EnablePreSelect(False, True)
    go.AcceptNothing(False)

    result = go.Get()
    if result != Rhino.Input.GetResult.Object:
        return None

    curve = go.Object(0).Curve()
    if curve is None or not curve.IsValid:
        return None
    return curve.DuplicateCurve()


def _pick_curve_parameter(curve, prompt):
    gp = Rhino.Input.Custom.GetPoint()
    gp.SetCommandPrompt(prompt)
    try:
        gp.Constrain(curve, False)
    except Exception:
        pass

    result = gp.Get()
    if result != Rhino.Input.GetResult.Point:
        return None

    point = gp.Point()
    ok, parameter = curve.ClosestPoint(point)
    if not ok:
        return None
    return parameter


def _sub_curve_between_parameters(curve, start_parameter, end_parameter):
    if abs(start_parameter - end_parameter) <= TOL:
        return None

    domain = curve.Domain
    if curve.IsClosed and start_parameter > end_parameter:
        first = curve.Trim(start_parameter, domain.Max)
        second = curve.Trim(domain.Min, end_parameter)
        pieces = [piece for piece in (first, second) if piece is not None and piece.IsValid]
        joined = rg.Curve.JoinCurves(pieces, TOL)
        if joined is not None and len(joined) == 1:
            return joined[0]

    if start_parameter < end_parameter:
        return curve.Trim(start_parameter, end_parameter)

    trimmed = curve.Trim(end_parameter, start_parameter)
    if trimmed is not None:
        trimmed.Reverse()
    return trimmed


class PurlinPreviewer:
    def __init__(self, rail, section_curve, source_frame):
        self.rail = rail
        self.section_curve = section_curve
        self.source_frame = source_frame
        self.section_spacing = _sticky_value("SECTION_SPACING", 0.80)
        self.width = _sticky_value("WIDTH", 0.10)
        self.height = _sticky_value("HEIGHT", 0.10)
        self.rail_step = _sticky_value("RAIL_STEP", 1.00)
        self.include_ends = True
        self.center_curves = []
        self.section_rectangles = []
        self.bbox = rg.BoundingBox.Empty
        self.Update()

    def Update(self):
        self.center_curves = []
        self.section_rectangles = []
        self.bbox = rg.BoundingBox.Empty

        section_parameters = _parameters_by_length(
            self.section_curve,
            self.section_spacing,
            self.include_ends,
            8,
        )
        route_parameters = _route_parameters(self.rail, self.rail_step)
        if len(section_parameters) == 0 or len(route_parameters) < 2:
            return

        for section_parameter in section_parameters:
            source_point = self.section_curve.PointAt(section_parameter)
            source_tangent = self.section_curve.TangentAt(section_parameter)
            if source_tangent.Length <= TOL:
                continue
            source_tangent.Unitize()

            points = []
            previous_forward = None
            for route_index, route_parameter in enumerate(route_parameters):
                target_frame, forward = _rail_frame(self.rail, route_parameter, previous_forward)
                previous_forward = forward

                transform = rg.Transform.PlaneToPlane(self.source_frame, target_frame)
                center = rg.Point3d(source_point)
                center.Transform(transform)
                section_tangent = _transform_vector(source_tangent, transform)
                points.append(center)

                if route_index == 0 or route_index == len(route_parameters) - 1:
                    rectangle = _rectangle_at(
                        center,
                        section_tangent,
                        forward,
                        self.width,
                        self.height,
                    )
                    self.section_rectangles.append(rectangle)
                    self.bbox.Union(rectangle.GetBoundingBox(True))

            if len(points) >= 2:
                center_curve = rg.PolylineCurve(rg.Polyline(points))
                self.center_curves.append(center_curve)
                self.bbox.Union(center_curve.GetBoundingBox(True))

    def OnCalcBBox(self, sender, e):
        if self.bbox.IsValid:
            e.IncludeBoundingBox(self.bbox)

    def OnDrawObjects(self, sender, e):
        line_color = System.Drawing.Color.FromArgb(255, 255, 210, 60)
        section_color = System.Drawing.Color.FromArgb(255, 80, 220, 255)
        for curve in self.center_curves:
            e.Display.DrawCurve(curve, line_color, 2)
        for rectangle in self.section_rectangles:
            e.Display.DrawCurve(rectangle, section_color, 2)


def _get_options_with_preview(previewer):
    section_spacing = previewer.section_spacing
    width = previewer.width
    height = previewer.height
    rail_step = previewer.rail_step

    go = Rhino.Input.Custom.GetOption()
    go.SetCommandPrompt("5/5: 设置横断面均布沿线檩条参数，视图中黄色线为预览，回车生成")
    go.AcceptNothing(True)

    opt_section_spacing = Rhino.Input.Custom.OptionDouble(section_spacing, TOL, 1000000.0)
    opt_width = Rhino.Input.Custom.OptionDouble(width, TOL, 1000000.0)
    opt_height = Rhino.Input.Custom.OptionDouble(height, TOL, 1000000.0)
    opt_rail_step = Rhino.Input.Custom.OptionDouble(rail_step, TOL, 1000000.0)
    opt_include_ends = Rhino.Input.Custom.OptionToggle(True, "No_不要", "Yes_要")

    while True:
        go.ClearCommandOptions()
        go.AddOptionDouble("SectionSpacing_横断面间距", opt_section_spacing)
        go.AddOptionDouble("Width_檩条宽度", opt_width)
        go.AddOptionDouble("Height_檩条高度", opt_height)
        go.AddOptionDouble("RailStep_路线采样", opt_rail_step)
        go.AddOptionToggle("Ends_包含横断面两端", opt_include_ends)

        result = go.Get()
        if result == Rhino.Input.GetResult.Nothing:
            section_spacing = previewer.section_spacing
            width = previewer.width
            height = previewer.height
            rail_step = previewer.rail_step
            include_ends = previewer.include_ends

            _save_sticky("SECTION_SPACING", section_spacing)
            _save_sticky("WIDTH", width)
            _save_sticky("HEIGHT", height)
            _save_sticky("RAIL_STEP", rail_step)

            return section_spacing, width, height, rail_step, include_ends

        if result == Rhino.Input.GetResult.Option:
            previewer.section_spacing = opt_section_spacing.CurrentValue
            previewer.width = opt_width.CurrentValue
            previewer.height = opt_height.CurrentValue
            previewer.rail_step = opt_rail_step.CurrentValue
            previewer.include_ends = opt_include_ends.CurrentValue
            previewer.Update()
            sc.doc.Views.Redraw()
            continue

        return None


def _horizontal_forward(curve, parameter, fallback=None):
    tangent = curve.TangentAt(parameter)
    forward = rg.Vector3d(tangent.X, tangent.Y, 0.0)
    if forward.Length <= TOL:
        if fallback is not None and fallback.Length > TOL:
            forward = rg.Vector3d(fallback)
        else:
            forward = rg.Vector3d.XAxis
    forward.Unitize()
    return forward


def _rail_frame(rail, parameter, fallback_forward=None):
    forward = _horizontal_forward(rail, parameter, fallback_forward)
    left = rg.Vector3d.CrossProduct(rg.Vector3d.ZAxis, forward)
    if left.Length <= TOL:
        left = rg.Vector3d.YAxis
    left.Unitize()
    return rg.Plane(rail.PointAt(parameter), forward, left), forward


def _curve_center(curve):
    bbox = curve.GetBoundingBox(True)
    if bbox.IsValid:
        return bbox.Center
    return rg.Point3d.Unset


def _parameters_by_length(curve, spacing, include_ends, closed_min_count=8):
    length = curve.GetLength()
    if length <= TOL:
        return []

    if curve.IsClosed:
        count = max(closed_min_count, min(500, int(math.ceil(length / spacing))))
        parameters = list(curve.DivideByCount(count, False) or [])
    else:
        parameters = list(curve.DivideByLength(spacing, True) or [])
        if include_ends:
            domain = curve.Domain
            if not parameters or abs(parameters[0] - domain.Min) > TOL:
                parameters.insert(0, domain.Min)
            if abs(parameters[-1] - domain.Max) > TOL:
                parameters.append(domain.Max)

    unique = []
    for parameter in parameters:
        duplicate = False
        for existing in unique:
            if abs(parameter - existing) <= TOL:
                duplicate = True
                break
        if not duplicate:
            unique.append(parameter)
    return unique


def _route_parameters(rail, rail_step):
    if rail.IsClosed:
        return _parameters_by_length(rail, rail_step, False, 12)
    return _parameters_by_length(rail, rail_step, True, 12)


def _rectangle_at(center, section_tangent, line_forward, width, height):
    across = rg.Vector3d(section_tangent)
    if across.Length <= TOL:
        across = rg.Vector3d.ZAxis
    across.Unitize()

    forward = rg.Vector3d(line_forward)
    if forward.Length <= TOL:
        forward = rg.Vector3d.XAxis
    forward.Unitize()

    normal = rg.Vector3d.CrossProduct(forward, across)
    if normal.Length <= TOL:
        normal = rg.Vector3d.ZAxis
    normal.Unitize()

    half_width = width * 0.5
    half_height = height * 0.5
    points = [
        center - across * half_width - normal * half_height,
        center + across * half_width - normal * half_height,
        center + across * half_width + normal * half_height,
        center - across * half_width + normal * half_height,
        center - across * half_width - normal * half_height,
    ]
    return rg.PolylineCurve(rg.Polyline(points))


def _transform_vector(vector, transform):
    copy = rg.Vector3d(vector)
    copy.Transform(transform)
    return copy


def _create_longitudinal_purlin(rail, source_frame, source_point, source_tangent, width, height, rail_step):
    route_parameters = _route_parameters(rail, rail_step)
    if len(route_parameters) < 2:
        return None

    sections = List[rg.Curve]()
    previous_forward = None

    for parameter in route_parameters:
        target_frame, forward = _rail_frame(rail, parameter, previous_forward)
        previous_forward = forward

        transform = rg.Transform.PlaneToPlane(source_frame, target_frame)
        center = rg.Point3d(source_point)
        center.Transform(transform)
        section_tangent = _transform_vector(source_tangent, transform)

        sections.Add(_rectangle_at(center, section_tangent, forward, width, height))

    breps = rg.Brep.CreateFromLoft(
        sections,
        rg.Point3d.Unset,
        rg.Point3d.Unset,
        rg.LoftType.Straight,
        rail.IsClosed,
    )
    if breps is None or len(breps) == 0:
        return None

    brep = breps[0]
    if brep is None or not brep.IsValid:
        return None

    if not brep.IsSolid:
        capped = brep.CapPlanarHoles(TOL)
        if capped is not None and capped.IsValid:
            brep = capped
    return brep if brep.IsValid else None


def main():
    sc.doc.Objects.UnselectAll()
    rail = _pick_curve("1/5: 选择路线曲线/边线")
    if rail is None:
        return

    sc.doc.Objects.UnselectAll()
    section_curve = _pick_curve("2/5: 选择声屏障横断面线")
    if section_curve is None:
        return

    start_parameter = _pick_curve_parameter(section_curve, "3/5: 在声屏障横断面线上选择檩条布置起点")
    if start_parameter is None:
        return

    end_parameter = _pick_curve_parameter(section_curve, "4/5: 在声屏障横断面线上选择檩条布置终点")
    if end_parameter is None:
        return

    section_curve = _sub_curve_between_parameters(section_curve, start_parameter, end_parameter)
    if section_curve is None or not section_curve.IsValid:
        print("横断面起终点无效，无法裁剪布置范围。")
        return

    section_center = _curve_center(section_curve)
    if not section_center.IsValid:
        print("声屏障横断面线无效。")
        return

    ok, source_parameter = rail.ClosestPoint(section_center)
    if not ok:
        print("无法把横断面线定位到路线附近。")
        return

    source_frame, _ = _rail_frame(rail, source_parameter)
    previewer = PurlinPreviewer(rail, section_curve, source_frame)
    rd.DisplayPipeline.CalculateBoundingBox += previewer.OnCalcBBox
    rd.DisplayPipeline.PostDrawObjects += previewer.OnDrawObjects
    sc.doc.Views.Redraw()
    try:
        sc.doc.Objects.UnselectAll()
        options = _get_options_with_preview(previewer)
    finally:
        rd.DisplayPipeline.CalculateBoundingBox -= previewer.OnCalcBBox
        rd.DisplayPipeline.PostDrawObjects -= previewer.OnDrawObjects
        sc.doc.Views.Redraw()

    if options is None:
        return
    section_spacing, width, height, rail_step, include_ends = options

    section_parameters = _parameters_by_length(section_curve, section_spacing, include_ends, 8)
    if not section_parameters:
        print("横断面线太短，无法布置檩条。")
        return

    attributes = Rhino.DocObjects.ObjectAttributes()
    attributes.LayerIndex = sc.doc.Layers.CurrentLayerIndex

    created = 0
    sc.doc.Views.RedrawEnabled = False
    try:
        for parameter in section_parameters:
            point = section_curve.PointAt(parameter)
            tangent = section_curve.TangentAt(parameter)
            if tangent.Length <= TOL:
                continue
            tangent.Unitize()

            purlin = _create_longitudinal_purlin(
                rail,
                source_frame,
                point,
                tangent,
                width,
                height,
                rail_step,
            )
            if purlin is None:
                print("有一根沿线檩条生成失败。")
                continue

            object_id = sc.doc.Objects.AddBrep(purlin, attributes)
            if object_id != System.Guid.Empty:
                created += 1
    finally:
        sc.doc.Views.RedrawEnabled = True
        sc.doc.Views.Redraw()

    print("横断面均布沿线檩条完成：{} 根。".format(created))


if __name__ == "__main__":
    main()

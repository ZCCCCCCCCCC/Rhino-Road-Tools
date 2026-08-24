#! python 3
# -*- coding: utf-8 -*-

"""选面和两条长边，在两长边之间等分生成面内檩条。

流程：
    1. 选择承载檩条的面
    2. 选择第一条长边
    3. 选择第二条长边
    4. 调整根数、宽高和采样参数，预览后生成实体
"""

import math

import Rhino
import Rhino.Display as rd
import Rhino.Geometry as rg
import scriptcontext as sc
import System
from System.Collections.Generic import List


TOL = sc.doc.ModelAbsoluteTolerance
STICKY_PREFIX = "ROAD_TOOLS_FACE_PURLIN_"
DEFAULT_VERSION = 2
DEFAULT_COUNT = 7
DEFAULT_WIDTH = 0.10
DEFAULT_HEIGHT = 0.10
DEFAULT_OFFSET = -0.15
DEFAULT_SAMPLE_STEP = 1.00
DEFAULT_INCLUDE_EDGES = True


def _sticky_value(name, default):
    key = STICKY_PREFIX + name
    if key in sc.sticky:
        try:
            return float(sc.sticky[key])
        except Exception:
            pass
    return default


def _sticky_int(name, default):
    key = STICKY_PREFIX + name
    if key in sc.sticky:
        try:
            return int(sc.sticky[key])
        except Exception:
            pass
    return default


def _sticky_bool(name, default):
    key = STICKY_PREFIX + name
    if key in sc.sticky:
        return bool(sc.sticky[key])
    return default


def _save_sticky(name, value):
    sc.sticky[STICKY_PREFIX + name] = value


def _init_defaults_once():
    version_key = STICKY_PREFIX + "DEFAULT_VERSION"
    if sc.sticky.get(version_key) == DEFAULT_VERSION:
        return

    for name in (
        "COUNT",
        "WIDTH",
        "HEIGHT",
        "OFFSET",
        "SAMPLE_STEP",
        "INCLUDE_EDGES",
    ):
        key = STICKY_PREFIX + name
        if key in sc.sticky:
            del sc.sticky[key]

    sc.sticky[version_key] = DEFAULT_VERSION


def _pick_face():
    go = Rhino.Input.Custom.GetObject()
    go.SetCommandPrompt("1/4: 选择承载檩条的面")
    go.GeometryFilter = Rhino.DocObjects.ObjectType.Surface
    try:
        go.GeometryFilter = go.GeometryFilter | Rhino.DocObjects.ObjectType.PolysrfFilter
    except AttributeError:
        go.GeometryFilter = go.GeometryFilter | Rhino.DocObjects.ObjectType.Brep
    go.SubObjectSelect = True
    go.EnablePreSelect(False, True)
    go.AcceptNothing(False)

    result = go.Get()
    if result != Rhino.Input.GetResult.Object:
        return None

    objref = go.Object(0)
    face = objref.Face()
    if face is not None:
        return face

    brep = objref.Brep()
    if brep is not None and brep.Faces.Count == 1:
        return brep.Faces[0]

    print("请选择一个有效的 Brep 面。")
    return None


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
        print("请选择有效曲线或边线。")
        return None
    return curve.DuplicateCurve()


def _align_edge_directions(first, second):
    first_copy = first.DuplicateCurve()
    second_copy = second.DuplicateCurve()

    same = (
        first_copy.PointAtStart.DistanceTo(second_copy.PointAtStart) +
        first_copy.PointAtEnd.DistanceTo(second_copy.PointAtEnd)
    )
    reversed_distance = (
        first_copy.PointAtStart.DistanceTo(second_copy.PointAtEnd) +
        first_copy.PointAtEnd.DistanceTo(second_copy.PointAtStart)
    )
    if reversed_distance < same:
        second_copy.Reverse()
    return first_copy, second_copy


def _curve_parameter_at_fraction(curve, fraction):
    domain = curve.Domain
    return domain.ParameterAt(max(0.0, min(1.0, fraction)))


def _face_point_near(face, point):
    try:
        ok, u, v = face.ClosestPoint(point)
        if ok:
            return face.PointAt(u, v)
    except Exception:
        pass
    return point


def _face_normal_near(face, point, fallback=None):
    normal = None
    try:
        ok, u, v = face.ClosestPoint(point)
        if ok:
            normal = rg.Vector3d(face.NormalAt(u, v))
    except Exception:
        normal = None

    if normal is None or normal.Length <= TOL:
        normal = rg.Vector3d(fallback) if fallback is not None else rg.Vector3d.ZAxis

    if normal.Length <= TOL:
        normal = rg.Vector3d.ZAxis
    normal.Unitize()

    if rg.Vector3d.Multiply(normal, rg.Vector3d.ZAxis) < 0:
        normal.Reverse()
    return normal


def _fractions_for_count(count, include_edges):
    count = max(1, int(count))
    if include_edges:
        if count == 1:
            return [0.5]
        return [float(index) / float(count - 1) for index in range(count)]
    return [float(index + 1) / float(count + 1) for index in range(count)]


def _sample_count(edge_a, edge_b, sample_step):
    longest = max(edge_a.GetLength(), edge_b.GetLength())
    if longest <= TOL:
        return 0
    return max(2, min(600, int(math.ceil(longest / sample_step)) + 1))


def _rectangle_at(point, forward, edge_to_edge, normal, width, height, offset):
    forward_copy = rg.Vector3d(forward)
    if forward_copy.Length <= TOL:
        forward_copy = rg.Vector3d.XAxis
    forward_copy.Unitize()

    normal_copy = rg.Vector3d(normal)
    if normal_copy.Length <= TOL:
        normal_copy = rg.Vector3d.ZAxis
    normal_copy.Unitize()
    base_point = point + normal_copy * offset

    across = rg.Vector3d.CrossProduct(normal_copy, forward_copy)
    if across.Length <= TOL:
        across = rg.Vector3d(edge_to_edge)
    if across.Length <= TOL:
        across = rg.Vector3d.YAxis
    across.Unitize()

    orient = rg.Vector3d(edge_to_edge)
    if orient.Length > TOL and rg.Vector3d.Multiply(across, orient) < 0:
        across.Reverse()

    half_width = width * 0.5
    points = [
        base_point - across * half_width,
        base_point + across * half_width,
        base_point + across * half_width + normal_copy * height,
        base_point - across * half_width + normal_copy * height,
        base_point - across * half_width,
    ]
    return rg.PolylineCurve(rg.Polyline(points))


def _frames_for_fraction(face, edge_a, edge_b, fraction, sample_step):
    count = _sample_count(edge_a, edge_b, sample_step)
    if count < 2:
        return []

    raw_items = []
    fallback_normal = rg.Vector3d.ZAxis
    try:
        ok, plane = face.TryGetPlane()
        if ok:
            fallback_normal = plane.Normal
    except Exception:
        pass

    for index in range(count):
        t = float(index) / float(count - 1)
        pa = edge_a.PointAt(_curve_parameter_at_fraction(edge_a, t))
        pb = edge_b.PointAt(_curve_parameter_at_fraction(edge_b, t))
        edge_to_edge = pb - pa
        point = pa + edge_to_edge * fraction
        point = _face_point_near(face, point)
        raw_items.append((point, edge_to_edge))

    frames = []
    for index, item in enumerate(raw_items):
        point, edge_to_edge = item
        if index == 0:
            forward = raw_items[1][0] - point
        elif index == len(raw_items) - 1:
            forward = point - raw_items[index - 1][0]
        else:
            forward = raw_items[index + 1][0] - raw_items[index - 1][0]

        if forward.Length <= TOL:
            continue
        forward.Unitize()
        normal = _face_normal_near(face, point, fallback_normal)
        frames.append((point, forward, edge_to_edge, normal))
    return frames


def _center_curve_from_frames(frames):
    if len(frames) < 2:
        return None
    points = [frame[0] for frame in frames]
    return rg.PolylineCurve(rg.Polyline(points))


def _create_purlin_from_frames(frames, width, height, offset):
    if len(frames) < 2:
        return None

    sections = List[rg.Curve]()
    for point, forward, edge_to_edge, normal in frames:
        sections.Add(_rectangle_at(point, forward, edge_to_edge, normal, width, height, offset))

    breps = rg.Brep.CreateFromLoft(
        sections,
        rg.Point3d.Unset,
        rg.Point3d.Unset,
        rg.LoftType.Straight,
        False,
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


class FacePurlinPreviewer:
    def __init__(self, face, edge_a, edge_b):
        self.face = face
        self.edge_a = edge_a
        self.edge_b = edge_b
        self.count = _sticky_int("COUNT", DEFAULT_COUNT)
        self.width = _sticky_value("WIDTH", DEFAULT_WIDTH)
        self.height = _sticky_value("HEIGHT", DEFAULT_HEIGHT)
        self.offset = _sticky_value("OFFSET", DEFAULT_OFFSET)
        self.sample_step = _sticky_value("SAMPLE_STEP", DEFAULT_SAMPLE_STEP)
        self.include_edges = _sticky_bool("INCLUDE_EDGES", DEFAULT_INCLUDE_EDGES)
        self.center_curves = []
        self.section_rectangles = []
        self.bbox = rg.BoundingBox.Empty
        self.Update()

    def Update(self):
        self.center_curves = []
        self.section_rectangles = []
        self.bbox = rg.BoundingBox.Empty

        for fraction in _fractions_for_count(self.count, self.include_edges):
            frames = _frames_for_fraction(
                self.face,
                self.edge_a,
                self.edge_b,
                fraction,
                self.sample_step,
            )
            curve = _center_curve_from_frames(frames)
            if curve is None:
                continue

            self.center_curves.append(curve)
            self.bbox.Union(curve.GetBoundingBox(True))

            for frame in (frames[0], frames[-1]):
                rectangle = _rectangle_at(
                    frame[0],
                    frame[1],
                    frame[2],
                    frame[3],
                    self.width,
                    self.height,
                    self.offset,
                )
                self.section_rectangles.append(rectangle)
                self.bbox.Union(rectangle.GetBoundingBox(True))

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
    go = Rhino.Input.Custom.GetOption()
    go.SetCommandPrompt("4/4: 设置面内檩条参数，黄色线为预览，回车生成")
    go.AcceptNothing(True)

    opt_count = Rhino.Input.Custom.OptionInteger(previewer.count, 1, 200)
    opt_width = Rhino.Input.Custom.OptionDouble(previewer.width, TOL, 1000000.0)
    opt_height = Rhino.Input.Custom.OptionDouble(previewer.height, TOL, 1000000.0)
    opt_offset = Rhino.Input.Custom.OptionDouble(previewer.offset, -1000000.0, 1000000.0)
    opt_sample = Rhino.Input.Custom.OptionDouble(previewer.sample_step, TOL, 1000000.0)
    opt_include_edges = Rhino.Input.Custom.OptionToggle(previewer.include_edges, "No_不含", "Yes_包含")

    while True:
        go.ClearCommandOptions()
        go.AddOptionInteger("Count_檩条根数", opt_count)
        go.AddOptionDouble("Width_檩条宽度", opt_width)
        go.AddOptionDouble("Height_檩条高度", opt_height)
        go.AddOptionDouble("Offset_法向偏移", opt_offset)
        go.AddOptionDouble("Step_路径采样", opt_sample)
        go.AddOptionToggle("Edges_包含两长边", opt_include_edges)

        result = go.Get()
        if result == Rhino.Input.GetResult.Nothing:
            _save_sticky("COUNT", previewer.count)
            _save_sticky("WIDTH", previewer.width)
            _save_sticky("HEIGHT", previewer.height)
            _save_sticky("OFFSET", previewer.offset)
            _save_sticky("SAMPLE_STEP", previewer.sample_step)
            _save_sticky("INCLUDE_EDGES", previewer.include_edges)
            return (
                previewer.count,
                previewer.width,
                previewer.height,
                previewer.offset,
                previewer.sample_step,
                previewer.include_edges,
            )

        if result == Rhino.Input.GetResult.Option:
            previewer.count = opt_count.CurrentValue
            previewer.width = opt_width.CurrentValue
            previewer.height = opt_height.CurrentValue
            previewer.offset = opt_offset.CurrentValue
            previewer.sample_step = opt_sample.CurrentValue
            previewer.include_edges = opt_include_edges.CurrentValue
            previewer.Update()
            sc.doc.Views.Redraw()
            continue

        return None


def main():
    _init_defaults_once()

    sc.doc.Objects.UnselectAll()
    face = _pick_face()
    if face is None:
        return

    sc.doc.Objects.UnselectAll()
    edge_a = _pick_curve("2/4: 选择第一条长边")
    if edge_a is None:
        return

    sc.doc.Objects.UnselectAll()
    edge_b = _pick_curve("3/4: 选择第二条长边")
    if edge_b is None:
        return

    edge_a, edge_b = _align_edge_directions(edge_a, edge_b)

    previewer = FacePurlinPreviewer(face, edge_a, edge_b)
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
    count, width, height, offset, sample_step, include_edges = options

    attributes = Rhino.DocObjects.ObjectAttributes()
    attributes.LayerIndex = sc.doc.Layers.CurrentLayerIndex

    created = 0
    created_ids = []
    sc.doc.Views.RedrawEnabled = False
    try:
        for fraction in _fractions_for_count(count, include_edges):
            frames = _frames_for_fraction(face, edge_a, edge_b, fraction, sample_step)
            purlin = _create_purlin_from_frames(frames, width, height, offset)
            if purlin is None:
                print("有一根面内檩条生成失败。")
                continue

            object_id = sc.doc.Objects.AddBrep(purlin, attributes)
            if object_id != System.Guid.Empty:
                created += 1
                created_ids.append(object_id)

        if created_ids:
            group_index = sc.doc.Groups.Add()
            if group_index >= 0:
                for object_id in created_ids:
                    sc.doc.Groups.AddToGroup(group_index, object_id)
    finally:
        sc.doc.Views.RedrawEnabled = True
        sc.doc.Views.Redraw()

    print("面内檩条创建完成：{} 根。".format(created))


if __name__ == "__main__":
    main()

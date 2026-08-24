#! python 3
# -*- coding: utf-8 -*-

"""
将绘制在 Top (World XY) 平面上的轮廓，转换为路径起点处的竖直截面后，
沿三维路径进行 Roadlike Top Sweep。

坐标映射规则：
    轮廓基点 -> 路径起点
    Top X 轴 -> 路径前进方向的左侧
    Top Y 轴 -> 世界 Z 轴（向上）
"""

import Rhino
import Rhino.Geometry as rg
import rhinoscriptsyntax as rs
import scriptcontext as sc
from System.Collections.Generic import List


TARGET_PARENT_LAYER = "声屏障模型"
EXCLUDED_CHILD_LAYER = "顶"


def get_horizontal_path_direction(rail):
    """返回路径起点附近的水平前进方向。"""
    domain = rail.Domain
    samples = 12

    for index in range(samples + 1):
        parameter = domain.T0 + (domain.Length * index / float(samples))
        tangent = rail.TangentAt(parameter)
        horizontal = rg.Vector3d(tangent.X, tangent.Y, 0.0)
        if horizontal.Length > 1e-9:
            horizontal.Unitize()
            return horizontal

    return None


def validate_top_profiles(profiles, tolerance):
    """确认轮廓没有离开 World XY 平面。"""
    for profile in profiles:
        bbox = profile.GetBoundingBox(True)
        if bbox.Max.Z - bbox.Min.Z > tolerance:
            return False
    return True


def transform_profiles_to_start(profiles, base_point, rail):
    """把 Top 平面轮廓变换至路径起点的竖直截面平面。"""
    path_direction = get_horizontal_path_direction(rail)
    if path_direction is None:
        return None

    left_direction = rg.Vector3d.CrossProduct(rg.Vector3d.ZAxis, path_direction)
    left_direction.Unitize()

    source_plane = rg.Plane(
        base_point,
        rg.Vector3d.XAxis,
        rg.Vector3d.YAxis
    )
    target_plane = rg.Plane(
        rail.PointAtStart,
        left_direction,
        rg.Vector3d.ZAxis
    )
    transform = rg.Transform.PlaneToPlane(source_plane, target_plane)

    transformed = []
    for profile in profiles:
        copy = profile.DuplicateCurve()
        if not copy.Transform(transform):
            return None
        transformed.append(copy)

    return transformed


def sweep_profile(rail, profile, tolerance):
    """以 Roadlike Top 模式扫掠单个截面。"""
    sweep = rg.SweepOneRail()
    sweep.SweepTolerance = tolerance
    sweep.AngleToleranceRadians = sc.doc.ModelAngleToleranceRadians
    sweep.ClosedSweep = rail.IsClosed

    # Roadlike Top 令截面的向上方向始终遵循世界 Z 轴。
    if hasattr(sweep, "SetToRoadlikeTop"):
        sweep.SetToRoadlikeTop()

    sections = List[rg.Curve]()
    sections.Add(profile)
    return sweep.PerformSweep(rail, sections)


def get_target_layers(profile_count):
    """获取声屏障模型下除“顶”以外的下、中、上三个子图层。"""
    parent_index = sc.doc.Layers.FindByFullPath(TARGET_PARENT_LAYER, -1)
    if parent_index < 0:
        return None, "未找到父图层“{}”。".format(TARGET_PARENT_LAYER)

    parent_id = sc.doc.Layers[parent_index].Id
    target_layers = []
    for index in range(sc.doc.Layers.Count):
        layer = sc.doc.Layers[index]
        if layer.IsDeleted or layer.ParentLayerId != parent_id:
            continue
        if layer.Name != EXCLUDED_CHILD_LAYER:
            target_layers.append(layer)

    if len(target_layers) != profile_count:
        return None, "“{}”下除“{}”外应有 {} 个子图层，当前找到 {} 个。".format(
            TARGET_PARENT_LAYER,
            EXCLUDED_CHILD_LAYER,
            profile_count,
            len(target_layers)
        )

    def vertical_order(layer):
        if "下" in layer.Name or "底" in layer.Name:
            return 0
        if "中" in layer.Name:
            return 1
        if "上" in layer.Name:
            return 2
        return 3

    target_layers.sort(key=lambda layer: (vertical_order(layer), layer.Name))
    return target_layers, None


def sort_profiles_bottom_to_top(profiles):
    """轮廓 Top 平面中的 Y 值会映射为世界 Z 值，因此按 Y 从低到高排序。"""
    return sorted(profiles, key=lambda profile: profile.GetBoundingBox(True).Center.Y)


def add_sweep_results(breps_with_layers, tolerance):
    """封盖可封闭的端面并将结果写入文档。"""
    result_ids = []
    for brep, layer_index in breps_with_layers:
        if brep is None:
            continue

        capped = brep.CapPlanarHoles(tolerance)
        result = capped if capped is not None else brep
        attributes = Rhino.DocObjects.ObjectAttributes()
        attributes.LayerIndex = layer_index
        object_id = sc.doc.Objects.AddBrep(result, attributes)
        if object_id:
            result_ids.append(object_id)

    return result_ids


def main():
    tolerance = sc.doc.ModelAbsoluteTolerance

    rail_id = rs.GetObject("1/3: 请选择三维路径曲线", rs.filter.curve, preselect=True)
    if not rail_id:
        return

    rail = rs.coercecurve(rail_id)
    if rail is None:
        return

    profile_ids = rs.GetObjects(
        "2/3: 请选择 Top 平面中的轮廓曲线（每条曲线独立扫掠）",
        rs.filter.curve,
        preselect=True
    )
    if not profile_ids:
        return

    profiles = [rs.coercecurve(profile_id) for profile_id in profile_ids]
    profiles = [profile for profile in profiles if profile is not None]
    if not profiles:
        return

    if not validate_top_profiles(profiles, tolerance):
        print("轮廓必须位于 Top / World XY 平面，不能包含 Z 方向的起伏。")
        return

    profiles = sort_profiles_bottom_to_top(profiles)
    target_layers, layer_error = get_target_layers(len(profiles))
    if target_layers is None:
        print(layer_error)
        return

    base_point = rs.GetPoint("3/3: 请选择轮廓基点（将对齐到路径起点）")
    if not base_point:
        return

    transformed_profiles = transform_profiles_to_start(profiles, base_point, rail)
    if transformed_profiles is None:
        print("无法确定路径的水平前进方向：路径起点附近不能是完全竖直的曲线。")
        return

    all_breps = []
    for profile, layer in zip(transformed_profiles, target_layers):
        try:
            breps = sweep_profile(rail, profile, tolerance)
        except Exception as error:
            print("扫掠失败: {}".format(error))
            return

        if breps:
            for brep in breps:
                all_breps.append((brep, layer.Index))

    if not all_breps:
        print("未生成扫掠结果。请确认轮廓基点位于轮廓上，且路径有效。")
        return

    rs.EnableRedraw(False)
    try:
        result_ids = add_sweep_results(all_breps, tolerance)
    finally:
        rs.EnableRedraw(True)
        sc.doc.Views.Redraw()

    if result_ids:
        rs.SelectObjects(result_ids)
        print("已生成 {} 个竖直轮廓扫掠结果。".format(len(result_ids)))
    else:
        print("扫掠已计算完成，但结果未能写入文档。")


if __name__ == "__main__":
    main()

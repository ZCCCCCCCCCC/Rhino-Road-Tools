#! python 3
# -*- coding: utf-8 -*-

import Rhino
import Rhino.Geometry as rg
import Rhino.Display as rd
import rhinoscriptsyntax as rs
import scriptcontext as sc
import System
from System.Collections.Generic import List
import math
import random

# ================= 核心几何算法 =================

STICKY_PREFIX = "ROAD_TOOLS_CURVE_ARRAY_"


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


def _sticky_bool(name, default):
    key = _sticky_key(name)
    if key in sc.sticky:
        try:
            return bool(sc.sticky[key])
        except Exception:
            pass
    return default


def _save_sticky(name, value):
    sc.sticky[_sticky_key(name)] = value


def offset_3d_curve_horizontal(curve, offset_dist, tolerance=0.1):
    """水平偏移三维曲线"""
    if curve is None or not curve.IsValid:
        return None
    if abs(offset_dist) < 1e-6:
        return curve.Duplicate()
        
    length = curve.GetLength()
    div_count = max(int(length / tolerance), 10)
    params = curve.DivideByCount(div_count, True)
    
    if not params:
        return curve.Duplicate()
        
    pts =[]
    for t in params:
        pt = curve.PointAt(t)
        tangent = curve.TangentAt(t)
        
        z_vec = rg.Vector3d.ZAxis
        normal = rg.Vector3d.CrossProduct(tangent, z_vec)
        
        if normal.Length < 1e-6: 
            normal = rg.Vector3d.XAxis
        else:
            normal.Unitize()
        
        pts.append(pt + normal * offset_dist)
        
    return rg.Curve.CreateInterpolatedCurve(pts, 3)

def get_block_bbox(idef):
    bbox = rg.BoundingBox.Empty
    for obj in idef.GetObjects():
        geom = obj.Geometry
        if geom:
            bbox.Union(geom.GetBoundingBox(True))
    if not bbox.IsValid:
        bbox = rg.BoundingBox(-0.5, -0.5, 0, 0.5, 0.5, 1)
    return bbox


# ================= 命令行预览引擎 =================

class ArrayPreviewer:
    def __init__(self, curves, block_bbox):
        self.curves = list(curves or [])
        self.block_bbox = block_bbox
        self.xforms =[]
        
        # 默认参数；确认生成后会记忆上一次设置。
        self.spacing = _sticky_float("SPACING", 5.0)
        self.offset = _sticky_float("OFFSET", 0.0)
        self.z_offset = _sticky_float("Z_OFFSET", 0.0)
        self.rotation = _sticky_float("ROTATION", 90.0)
        self.scale = _sticky_float("SCALE", 1.0)
        self.random_rotation = _sticky_bool("RANDOM_ROTATION", False)
        self.random_angles = []
        
        # 核心对齐逻辑开关
        self.align_chord = _sticky_bool("ALIGN_CHORD", True)   # True=两点连线(首尾相接)，False=单点切线
        self.align_sloped = _sticky_bool("ALIGN_SLOPED", False)  # True=Z轴顺坡倾斜，False=Z轴绝对垂直向上
        
        self.Update()

    def Update(self):
        self.xforms =[]
        if not self.curves or self.spacing <= 1e-6:
            return

        placements = []
        for curve in self.curves:
            offset_crv = offset_3d_curve_horizontal(curve, self.offset)
            if offset_crv is None or not offset_crv.IsValid:
                continue

            # RhinoCommon returns a System.Double[] here. Convert it before
            # inserting the final endpoint below; System arrays are fixed-size.
            params = list(offset_crv.DivideByLength(self.spacing, True) or [])
            if not params:
                continue

            # DivideByLength(includeEnds=True) guarantees the start point but
            # may omit the end point unless the spacing divides the length.
            dom = offset_crv.Domain
            if abs(params[0] - dom.Min) > 1e-6:
                params.insert(0, dom.Min)
            if abs(params[-1] - dom.Max) > 1e-6:
                params.append(dom.Max)

            pts = [offset_crv.PointAt(t) for t in params]
            tangents = [offset_crv.TangentAt(t) for t in params]

            for i in range(len(pts)):
                pt = pts[i]
            
                # 1. Decide the X-axis forward vector.
                if self.align_chord and i < len(pts) - 1:
                    fwd_vec = pts[i + 1] - pt
                else:
                    fwd_vec = tangents[i]

                # Trees and other upright objects should turn only in plan.
                if not self.align_sloped:
                    fwd_vec.Z = 0

                if not fwd_vec.Unitize():
                    continue

                placements.append((pt, fwd_vec))

        if not placements:
            return

        # Keep one random angle per preview item so changing another option
        # does not make all trees visibly jump around on every refresh.
        if len(self.random_angles) != len(placements):
            self.random_angles = [
                random.uniform(-180.0, 180.0) for _ in placements
            ]

        for i, (pt, fwd_vec) in enumerate(placements):
            # 2. 计算 Y轴 左向量 (始终保持水平)
            z_global = rg.Vector3d.ZAxis
            if abs(fwd_vec.Z) > 0.99: 
                left_vec = rg.Vector3d.YAxis
            else:
                left_vec = rg.Vector3d.CrossProduct(z_global, fwd_vec)
                if not left_vec.Unitize():
                    continue
                
            # 3. 计算 Z轴 向上向量
            if self.align_sloped:
                # 顺坡：Z轴倾斜
                up_vec = rg.Vector3d.CrossProduct(fwd_vec, left_vec)
                if not up_vec.Unitize():
                    continue
            else:
                # 垂直：Z轴朝天
                up_vec = z_global
                
            # 4. 生成平面和矩阵
            plane = rg.Plane(pt, fwd_vec, left_vec)
            plane.Origin = plane.Origin + up_vec * self.z_offset
            
            rotation = self.rotation
            if self.random_rotation:
                rotation += self.random_angles[i]
            if abs(rotation) > 0.01:
                plane.Rotate(math.radians(rotation), plane.ZAxis)
                
            xform_orient = rg.Transform.PlaneToPlane(rg.Plane.WorldXY, plane)
            
            if abs(self.scale - 1.0) > 0.001:
                xform_scale = rg.Transform.Scale(rg.Point3d.Origin, self.scale)
                final_xform = xform_orient * xform_scale
            else:
                final_xform = xform_orient
                
            self.xforms.append(final_xform)

    def OnCalcBBox(self, sender, e):
        for xform in self.xforms:
            box = rg.Box(self.block_bbox)
            box.Transform(xform)
            e.IncludeBoundingBox(box.BoundingBox)

    def OnDrawObjects(self, sender, e):
        col_box = System.Drawing.Color.Cyan
        col_dir = System.Drawing.Color.Red
        for xform in self.xforms:
            box = rg.Box(self.block_bbox)
            box.Transform(xform)
            e.Display.DrawBox(box, col_box, 1)
            
            pt_origin = rg.Point3d.Origin
            pt_origin.Transform(xform)
            pt_forward = rg.Point3d(1.5, 0, 0)
            pt_forward.Transform(xform)
            e.Display.DrawArrow(rg.Line(pt_origin, pt_forward), col_dir, 15.0, 0.0)


def run_command_line_interaction(curves, idef):
    previewer = ArrayPreviewer(curves, get_block_bbox(idef))
    rd.DisplayPipeline.CalculateBoundingBox += previewer.OnCalcBBox
    rd.DisplayPipeline.PostDrawObjects += previewer.OnDrawObjects
    sc.doc.Views.Redraw()
    
    try:
        go = Rhino.Input.Custom.GetOption()
        go.SetCommandPrompt("调整参数 (直接按 Enter 完成生成，按 Esc 取消)")
        go.AcceptNothing(True) 
        
        opt_dir = Rhino.Input.Custom.OptionToggle(previewer.align_chord, "Tangent_单点切向", "Chord_直指下一点")
        opt_up = Rhino.Input.Custom.OptionToggle(previewer.align_sloped, "Vertical_垂直", "Sloped_顺坡")
        
        opt_spacing = Rhino.Input.Custom.OptionDouble(previewer.spacing, 0.1, 1000.0)
        opt_offset = Rhino.Input.Custom.OptionDouble(previewer.offset, -500.0, 500.0)
        opt_z_offset = Rhino.Input.Custom.OptionDouble(previewer.z_offset, -500.0, 500.0)
        opt_rotation = Rhino.Input.Custom.OptionDouble(previewer.rotation, -360.0, 360.0)
        opt_scale = Rhino.Input.Custom.OptionDouble(previewer.scale, 0.01, 100.0)
        opt_random_rotation = Rhino.Input.Custom.OptionToggle(
            previewer.random_rotation, "Off_关闭", "On_开启"
        )
        
        while True:
            go.ClearCommandOptions()
            go.AddOptionToggle("Dir_X轴朝向", opt_dir)
            go.AddOptionToggle("Up_Z轴朝向", opt_up)
            go.AddOptionDouble("Spacing_间距", opt_spacing)
            go.AddOptionDouble("Offset_水平偏移", opt_offset)
            go.AddOptionDouble("Z_Offset_高程", opt_z_offset)
            go.AddOptionDouble("Rotation_图块旋转", opt_rotation)
            go.AddOptionToggle("Random_随机旋转", opt_random_rotation)
            go.AddOptionDouble("Scale_缩放", opt_scale)
            
            res = go.Get()
            if res == Rhino.Input.GetResult.Option:
                previewer.align_chord = opt_dir.CurrentValue
                previewer.align_sloped = opt_up.CurrentValue
                previewer.spacing = opt_spacing.CurrentValue
                previewer.offset = opt_offset.CurrentValue
                previewer.z_offset = opt_z_offset.CurrentValue
                previewer.rotation = opt_rotation.CurrentValue
                previewer.random_rotation = opt_random_rotation.CurrentValue
                previewer.scale = opt_scale.CurrentValue
                previewer.Update()
                sc.doc.Views.Redraw()
                continue
            elif res == Rhino.Input.GetResult.Nothing:
                _save_sticky("ALIGN_CHORD", previewer.align_chord)
                _save_sticky("ALIGN_SLOPED", previewer.align_sloped)
                _save_sticky("SPACING", previewer.spacing)
                _save_sticky("OFFSET", previewer.offset)
                _save_sticky("Z_OFFSET", previewer.z_offset)
                _save_sticky("ROTATION", previewer.rotation)
                _save_sticky("RANDOM_ROTATION", previewer.random_rotation)
                _save_sticky("SCALE", previewer.scale)
                return previewer.xforms
            else:
                return None
    finally:
        rd.DisplayPipeline.CalculateBoundingBox -= previewer.OnCalcBBox
        rd.DisplayPipeline.PostDrawObjects -= previewer.OnDrawObjects
        sc.doc.Views.Redraw()


# ================= 引导曲线拾取（支持拾取边缘） =================

def _guide_curve_filter(rhino_object, geometry, component_index):
    """自定义几何过滤器：只接受 Curve 几何（普通曲线 / Brep 边缘），显式排除面。"""
    # 明确排除 Brep 面子对象
    if component_index.ComponentIndexType == Rhino.Geometry.ComponentIndexType.BrepFace:
        return False
    return isinstance(geometry, rg.Curve)


def get_guide_curve(prompt):
    """选取一条或多条引导路线，并合并相连曲线。"""
    go = Rhino.Input.Custom.GetObject()
    go.SetCommandPrompt(prompt + "（可框选多条曲线/边缘线；按 Enter 完成，Esc 取消）")
    # 只开放 Curve：SubObjectSelect=True 时，Brep 的边缘子对象几何类型也是 Curve，
    # 而面子对象几何类型为 Surface/BrepFace，会被 Curve 过滤器挡掉，因此面点不中。
    go.GeometryFilter = Rhino.DocObjects.ObjectType.Curve
    go.SubObjectSelect = True
    go.SetCustomGeometryFilter(_guide_curve_filter)
    go.EnableClearObjectsOnEntry(False)
    go.EnableUnselectObjectsOnExit(False)
    go.AcceptNothing(False)

    res = go.GetMultiple(1, 0)
    if res != Rhino.Input.GetResult.Object:
        return None

    curves = []
    for index in range(go.ObjectCount):
        curve = go.Object(index).Curve()
        if curve is not None and curve.IsValid:
            curves.append(curve)

    if not curves:
        return None

    # Join connected segments so a broken polyline behaves like one route.
    if len(curves) > 1:
        joined = rg.Curve.JoinCurves(curves, sc.doc.ModelAbsoluteTolerance)
        if joined:
            curves = list(joined)
    return curves


# ================= 主程序入口 =================

def main():
    curves = get_guide_curve("1/3: 请选择引导路线（可框选多条曲线或曲面/实体边缘）")
    if not curves:
        print("引导路线无效，阵列操作已取消。")
        return
    
    obj_ids = rs.GetObjects("2/3: 请选择要阵列的物体", preselect=False)
    if not obj_ids: return
    
    is_temp_block = False
    idef = None
    
    if len(obj_ids) == 1 and rs.ObjectType(obj_ids[0]) == rs.filter.instance:
        inst_obj = rs.coercegeometry(obj_ids[0])
        if inst_obj is not None:
            idef = sc.doc.InstanceDefinitions.FindId(inst_obj.ParentIdefId)
        if idef is None:
            print("未找到所选图块定义，阵列操作已取消。")
            return
    else:
        bbox = rg.BoundingBox.Empty
        geoms = List[rg.GeometryBase]()
        attrs = List[Rhino.DocObjects.ObjectAttributes]()
        for guid in obj_ids:
            robj = sc.doc.Objects.FindId(guid)
            if robj is None or robj.Geometry is None:
                continue
            geoms.Add(robj.Geometry)
            attrs.Add(robj.Attributes)
            bbox.Union(robj.Geometry.GetBoundingBox(True))

        if geoms.Count == 0 or not bbox.IsValid:
            print("未找到可阵列的几何对象，阵列操作已取消。")
            return

        bottom_center = rg.Point3d(bbox.Center.X, bbox.Center.Y, bbox.Min.Z)
        
        # ⚠️ 极为关键的提示 ⚠️
        print("💡 护栏/围挡 -> 请务必捕捉并点击物体【起始端】的底部")
        print("💡 树木/路灯 -> 请点击物体的【正中心】(或直接按回车默认)")
        pt = rs.GetPoint("3/3: 请点击指定【阵列基准点】")
        if not pt: pt = bottom_center
                 
        block_name = "AutoArray_" + str(System.Guid.NewGuid())[:8]
        idef_idx = sc.doc.InstanceDefinitions.Add(block_name, "智能打包临时图块", pt, geoms, attrs)
        if idef_idx < 0:
            print("无法创建临时图块定义，阵列操作已取消。")
            return
        idef = sc.doc.InstanceDefinitions[idef_idx]
        if idef is None:
            print("无法读取临时图块定义，阵列操作已取消。")
            return
        is_temp_block = True
    
    try:
        final_xforms = run_command_line_interaction(curves, idef)
    except Exception as exc:
        print("阵列预览失败：{}".format(exc))
        final_xforms = None
    
    if final_xforms:
        rs.EnableRedraw(False)
        try:
            group_idx = sc.doc.Groups.Add()
            count = 0
            for xform in final_xforms:
                obj_id = sc.doc.Objects.AddInstanceObject(idef.Index, xform)
                if obj_id:
                    sc.doc.Groups.AddToGroup(group_idx, obj_id)
                    count += 1
        finally:
            rs.EnableRedraw(True)
            sc.doc.Views.Redraw()
        print(">> 成功阵列了 {} 个物体并已成组！".format(count))
    else:
        if is_temp_block:
            sc.doc.InstanceDefinitions.Delete(idef.Index)
        print("取消了阵列操作。")

if __name__ == "__main__":
    main()

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

# ================= 核心几何算法 =================

def offset_3d_curve_horizontal(curve, offset_dist, tolerance=0.1):
    """水平偏移三维曲线"""
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
    def __init__(self, curve, block_bbox):
        self.curve = curve
        self.block_bbox = block_bbox
        self.xforms =[]
        
        # 默认参数
        self.spacing = 10.0
        self.offset = 0.0
        self.z_offset = 0.0
        self.rotation = 0.0
        self.scale = 1.0
        
        # 核心对齐逻辑开关
        self.align_chord = True   # True=两点连线(首尾相接)，False=单点切线
        self.align_sloped = True  # True=Z轴顺坡倾斜，False=Z轴绝对垂直向上
        
        self.Update()

    def Update(self):
        self.xforms =[]
        offset_crv = offset_3d_curve_horizontal(self.curve, self.offset)
        params = offset_crv.DivideByLength(self.spacing, True)
        if not params: return
        
        # 提取所有的点和切线
        pts =[offset_crv.PointAt(t) for t in params]
        tangents =[offset_crv.TangentAt(t) for t in params]
            
        for i in range(len(pts)):
            pt = pts[i]
            
            # 1. 决定 X轴 前方向量
            if self.align_chord and i < len(pts) - 1:
                # 【两点连线模式】：方向绝对精准地指向下一个阵列点
                fwd_vec = pts[i+1] - pt
            else:
                # 【单点切向模式】或最后一个点：使用曲线当前切线
                fwd_vec = tangents[i]
                
            # 如果是垂直生长的物体(如树)，去掉Z轴分量，让它只在平面上转向
            if not self.align_sloped:
                fwd_vec.Z = 0
                
            fwd_vec.Unitize()
            
            # 2. 计算 Y轴 左向量 (始终保持水平)
            z_global = rg.Vector3d.ZAxis
            if abs(fwd_vec.Z) > 0.99: 
                left_vec = rg.Vector3d.YAxis
            else:
                left_vec = rg.Vector3d.CrossProduct(z_global, fwd_vec)
                left_vec.Unitize()
                
            # 3. 计算 Z轴 向上向量
            if self.align_sloped:
                # 顺坡：Z轴倾斜
                up_vec = rg.Vector3d.CrossProduct(fwd_vec, left_vec)
                up_vec.Unitize()
            else:
                # 垂直：Z轴朝天
                up_vec = z_global
                
            # 4. 生成平面和矩阵
            plane = rg.Plane(pt, fwd_vec, left_vec)
            plane.Origin = plane.Origin + up_vec * self.z_offset
            
            if abs(self.rotation) > 0.01:
                plane.Rotate(math.radians(self.rotation), plane.ZAxis)
                
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


def run_command_line_interaction(curve, idef):
    previewer = ArrayPreviewer(curve, get_block_bbox(idef))
    rd.DisplayPipeline.CalculateBoundingBox += previewer.OnCalcBBox
    rd.DisplayPipeline.PostDrawObjects += previewer.OnDrawObjects
    
    try:
        go = Rhino.Input.Custom.GetOption()
        go.SetCommandPrompt("调整参数 (直接按 Enter 完成生成，按 Esc 取消)")
        go.AcceptNothing(True) 
        
        opt_dir = Rhino.Input.Custom.OptionToggle(True, "Tangent_单点切向", "Chord_直指下一点")
        opt_up = Rhino.Input.Custom.OptionToggle(True, "Vertical_垂直", "Sloped_顺坡")
        
        opt_spacing = Rhino.Input.Custom.OptionDouble(10.0, 0.1, 1000.0)
        opt_offset = Rhino.Input.Custom.OptionDouble(0.0, -500.0, 500.0)
        opt_z_offset = Rhino.Input.Custom.OptionDouble(0.0, -500.0, 500.0)
        opt_rotation = Rhino.Input.Custom.OptionDouble(0.0, -360.0, 360.0)
        opt_scale = Rhino.Input.Custom.OptionDouble(1.0, 0.01, 100.0)
        
        while True:
            go.ClearCommandOptions()
            go.AddOptionToggle("Dir_X轴朝向", opt_dir)
            go.AddOptionToggle("Up_Z轴朝向", opt_up)
            go.AddOptionDouble("Spacing_间距", opt_spacing)
            go.AddOptionDouble("Offset_水平偏移", opt_offset)
            go.AddOptionDouble("Z_Offset_高程", opt_z_offset)
            go.AddOptionDouble("Rotation_图块旋转", opt_rotation)
            go.AddOptionDouble("Scale_缩放", opt_scale)
            
            res = go.Get()
            if res == Rhino.Input.GetResult.Option:
                previewer.align_chord = opt_dir.CurrentValue
                previewer.align_sloped = opt_up.CurrentValue
                previewer.spacing = opt_spacing.CurrentValue
                previewer.offset = opt_offset.CurrentValue
                previewer.z_offset = opt_z_offset.CurrentValue
                previewer.rotation = opt_rotation.CurrentValue
                previewer.scale = opt_scale.CurrentValue
                previewer.Update()
                sc.doc.Views.Redraw()
                continue
            elif res == Rhino.Input.GetResult.Nothing:
                return previewer.xforms
            else:
                return None
    finally:
        rd.DisplayPipeline.CalculateBoundingBox -= previewer.OnCalcBBox
        rd.DisplayPipeline.PostDrawObjects -= previewer.OnDrawObjects
        sc.doc.Views.Redraw()


# ================= 主程序入口 =================

def main():
    crv_id = rs.GetObject("1/3: 请选择引导路线", rs.filter.curve)
    if not crv_id: return
    curve = rs.coercecurve(crv_id)
    
    obj_ids = rs.GetObjects("2/3: 请选择要阵列的物体", preselect=False)
    if not obj_ids: return
    
    is_temp_block = False
    idef = None
    
    if len(obj_ids) == 1 and rs.ObjectType(obj_ids[0]) == rs.filter.instance:
        inst_obj = rs.coercegeometry(obj_ids[0])
        idef = sc.doc.InstanceDefinitions.FindId(inst_obj.ParentIdefId)
    else:
        bbox = rg.BoundingBox.Empty
        for guid in obj_ids:
            geom = rs.coercegeometry(guid)
            if geom:
                bbox.Union(geom.GetBoundingBox(True))
                
        bottom_center = rg.Point3d(bbox.Center.X, bbox.Center.Y, bbox.Min.Z)
        
        # ⚠️ 极为关键的提示 ⚠️
        print("💡 护栏/围挡 -> 请务必捕捉并点击物体【起始端】的底部")
        print("💡 树木/路灯 -> 请点击物体的【正中心】(或直接按回车默认)")
        pt = rs.GetPoint("3/3: 请点击指定【阵列基准点】")
        if not pt: pt = bottom_center
        
        geoms = List[rg.GeometryBase]()
        attrs = List[Rhino.DocObjects.ObjectAttributes]()
        for guid in obj_ids:
            robj = sc.doc.Objects.FindId(guid)
            if robj:
                geoms.Add(robj.Geometry)
                attrs.Add(robj.Attributes)
                
        block_name = "AutoArray_" + str(System.Guid.NewGuid())[:8]
        idef_idx = sc.doc.InstanceDefinitions.Add(block_name, "智能打包临时图块", pt, geoms, attrs)
        idef = sc.doc.InstanceDefinitions[idef_idx]
        is_temp_block = True
    
    final_xforms = run_command_line_interaction(curve, idef)
    
    if final_xforms:
        rs.EnableRedraw(False)
        group_idx = sc.doc.Groups.Add()
        
        count = 0
        for xform in final_xforms:
            obj_id = sc.doc.Objects.AddInstanceObject(idef.Index, xform)
            if obj_id:
                sc.doc.Groups.AddToGroup(group_idx, obj_id)
                count += 1
                
        rs.EnableRedraw(True)
        sc.doc.Views.Redraw()
        print(">> 成功阵列了 {} 个物体并已成组！".format(count))
    else:
        if is_temp_block:
            sc.doc.InstanceDefinitions.Delete(idef.Index)
        print("取消了阵列操作。")

if __name__ == "__main__":
    main()
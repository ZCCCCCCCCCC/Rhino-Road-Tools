#! python 3
#! python 3
# -*- coding: utf-8 -*-

import Rhino
import Rhino.Geometry as rg
import Rhino.Display as rd
import rhinoscriptsyntax as rs
import scriptcontext as sc
import System
import Eto.Drawing as drawing
import Eto.Forms as forms
import math

# ================= 核心几何算法 =================

def offset_3d_curve_horizontal(curve, offset_dist, tolerance=0.1):
    """水平偏移三维曲线，避免法线扭曲"""
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
        
        # 叉乘求水平法向
        z_vec = rg.Vector3d.ZAxis
        normal = rg.Vector3d.CrossProduct(tangent, z_vec)
        
        if normal.Length < 1e-6: 
            normal = rg.Vector3d.XAxis
        else:
            normal.Unitize()
        
        pts.append(pt + normal * offset_dist)
        
    return rg.Curve.CreateInterpolatedCurve(pts, 3)

def get_block_bbox(idef):
    """获取图块定义的初始边界框（用于生成轻量级预览）"""
    bbox = rg.BoundingBox.Empty
    for obj in idef.GetObjects():
        geom = obj.Geometry
        if geom:
            bbox.Union(geom.GetBoundingBox(True))
    if not bbox.IsValid:
        bbox = rg.BoundingBox(-0.5, -0.5, 0, 0.5, 0.5, 1)
    return bbox


# ================= 交互式 UI 与 预览系统 =================

class BlockArrayDialog(forms.Dialog[bool]):
    def __init__(self, curve, idef):
        forms.Dialog[bool].__init__(self)
        
        self.Title = "三维沿线阵列大师 - 图块放样"
        self.Padding = drawing.Padding(15)
        self.Resizable = False
        
        self.curve = curve
        self.idef = idef
        self.block_bbox = get_block_bbox(idef)
        self.preview_xforms =[] 
        self.is_closed = False
        
        # 绑定显示管道事件，用于高性能线框预览
        rd.DisplayPipeline.CalculateBoundingBox += self.OnCalcBBox
        rd.DisplayPipeline.PostDrawObjects += self.OnDrawObjects
        
        # --- CPython 3 规范的 UI 控件定义方式 ---
        self.num_spacing = forms.NumericStepper()
        self.num_spacing.Value = 10.0
        self.num_spacing.DecimalPlaces = 2
        self.num_spacing.Increment = 1.0
        self.num_spacing.MinValue = 0.1
        
        self.num_offset = forms.NumericStepper()
        self.num_offset.Value = 0.0
        self.num_offset.DecimalPlaces = 2
        self.num_offset.Increment = 0.5
        self.num_offset.MinValue = -500.0
        self.num_offset.MaxValue = 500.0
        
        self.num_z_offset = forms.NumericStepper()
        self.num_z_offset.Value = 0.0
        self.num_z_offset.DecimalPlaces = 2
        self.num_z_offset.Increment = 0.1
        
        self.num_rotation = forms.NumericStepper()
        self.num_rotation.Value = 0.0
        self.num_rotation.DecimalPlaces = 0
        self.num_rotation.Increment = 90.0
        self.num_rotation.MinValue = -360.0
        self.num_rotation.MaxValue = 360.0
        
        self.num_scale = forms.NumericStepper()
        self.num_scale.Value = 1.0
        self.num_scale.DecimalPlaces = 2
        self.num_scale.Increment = 0.1
        self.num_scale.MinValue = 0.01
        
        # 绑定事件
        self.num_spacing.ValueChanged += self.OnParamChanged
        self.num_offset.ValueChanged += self.OnParamChanged
        self.num_z_offset.ValueChanged += self.OnParamChanged
        self.num_rotation.ValueChanged += self.OnParamChanged
        self.num_scale.ValueChanged += self.OnParamChanged
        
        def CreateLabel(text_str):
            lbl = forms.Label()
            lbl.Text = text_str
            return lbl
        
        # --- UI 布局 ---
        layout = forms.DynamicLayout()
        layout.Spacing = drawing.Size(5, 12)
        
        layout.AddRow(CreateLabel(f"当前图块: 【{idef.Name}】"))
        layout.AddRow(CreateLabel("-" * 30))
        layout.AddRow(CreateLabel("阵列间距 (m):"), self.num_spacing)
        layout.AddRow(CreateLabel("水平横移 (m) [正数左/负数右]:"), self.num_offset)
        layout.AddRow(CreateLabel("高程偏移 (m) [Z轴升降]:"), self.num_z_offset)
        layout.AddRow(CreateLabel("图块旋转 (度) [调整朝向]:"), self.num_rotation)
        layout.AddRow(CreateLabel("图块缩放倍数:"), self.num_scale)
        
        btn_ok = forms.Button()
        btn_ok.Text = "确认生成"
        btn_ok.Click += self.OnOKClick
        
        btn_cancel = forms.Button()
        btn_cancel.Text = "取消"
        btn_cancel.Click += self.OnCancelClick
        
        layout.AddRow(CreateLabel(""))
        layout.AddRow(btn_ok, btn_cancel)
        self.Content = layout
        
        self.UpdatePreview()
        
    def OnCalcBBox(self, sender, e):
        if self.is_closed: return
        for xform in self.preview_xforms:
            box = rg.Box(self.block_bbox)
            box.Transform(xform)
            e.IncludeBoundingBox(box.BoundingBox)
            
    def OnDrawObjects(self, sender, e):
        """丝滑预览：用青色线框表示图块的体积，用红线表示朝向前方"""
        if self.is_closed: return
        col_box = System.Drawing.Color.Cyan
        col_dir = System.Drawing.Color.Red
        
        for xform in self.preview_xforms:
            box = rg.Box(self.block_bbox)
            box.Transform(xform)
            e.Display.DrawBox(box, col_box, 1)
            
            # 画一个红色的方向指针（代表图块的X轴前方）
            pt_origin = rg.Point3d.Origin
            pt_origin.Transform(xform)
            pt_forward = rg.Point3d(1.5, 0, 0)
            pt_forward.Transform(xform)
            e.Display.DrawArrow(rg.Line(pt_origin, pt_forward), col_dir, 15.0, 0.0)
            
    def OnParamChanged(self, sender, e):
        self.UpdatePreview()
        
    def UpdatePreview(self):
        if self.is_closed: return
        self.preview_xforms =[] 
        
        spacing = self.num_spacing.Value
        offset = self.num_offset.Value
        z_offset = self.num_z_offset.Value
        rotation = self.num_rotation.Value
        scale = self.num_scale.Value
        
        # 1. 计算偏移路线
        offset_crv = offset_3d_curve_horizontal(self.curve, offset)
        
        # 2. 根据间距计算分割点
        params = offset_crv.DivideByLength(spacing, True)
        if not params:
            sc.doc.Views.Redraw()
            return
            
        # 3. 计算每个点的空间矩阵(Transform)
        for t in params:
            pt = offset_crv.PointAt(t)
            tangent = offset_crv.TangentAt(t)
            
            # 以Z轴为向上参考系计算左侧法向，保证图块垂直立于地面
            z_vec = rg.Vector3d.ZAxis
            if abs(tangent.Z) > 0.99: # 极罕见的垂直路线防错
                left_vec = rg.Vector3d.YAxis
            else:
                left_vec = rg.Vector3d.CrossProduct(z_vec, tangent)
                left_vec.Unitize()
                
            # 构造基准平面：原点=曲线点，X轴=曲线切向，Y轴=左侧法向
            plane = rg.Plane(pt, tangent, left_vec)
            
            # 加入Z轴高程偏移
            plane.Origin = plane.Origin + rg.Vector3d.ZAxis * z_offset
            
            # 围绕Z轴旋转 (用于纠正图块初始建模朝向不对的问题)
            if abs(rotation) > 0.01:
                plane.Rotate(math.radians(rotation), plane.ZAxis)
                
            # 生成定位矩阵
            xform_orient = rg.Transform.PlaneToPlane(rg.Plane.WorldXY, plane)
            
            # 加入缩放矩阵
            if abs(scale - 1.0) > 0.001:
                xform_scale = rg.Transform.Scale(rg.Point3d.Origin, scale)
                final_xform = xform_orient * xform_scale
            else:
                final_xform = xform_orient
                
            self.preview_xforms.append(final_xform)
            
        sc.doc.Views.Redraw()

    def Cleanup(self):
        if self.is_closed: return
        self.is_closed = True
        try: rd.DisplayPipeline.CalculateBoundingBox -= self.OnCalcBBox
        except: pass
        try: rd.DisplayPipeline.PostDrawObjects -= self.OnDrawObjects
        except: pass
        sc.doc.Views.Redraw()
        
    def OnOKClick(self, sender, e):
        self.Cleanup()
        self.Close(True)
        
    def OnCancelClick(self, sender, e):
        self.Cleanup()
        self.Close(False)

    def OnClosed(self, e):
        if not self.is_closed:
            self.Cleanup()
        forms.Dialog[bool].OnClosed(self, e)


# ================= 主程序入口 =================

def main():
    # 1. 提示选择引导路线
    crv_id = rs.GetObject("1/2: 请选择一条道路路线 (作为阵列参考线)", rs.filter.curve)
    if not crv_id: return
    curve = rs.coercecurve(crv_id)
    
    # 2. 提示选择要阵列的图块 (路灯/树/护栏等)
    block_id = rs.GetObject("2/2: 请选择要阵列的【图块 (Block Instance)】", rs.filter.instance)
    if not block_id: return
    
    # 提取图块的底层定义信息 (Instance Definition)
    inst_obj = rs.coercegeometry(block_id)
    if not inst_obj: return
    idef_idx = inst_obj.ParentIdefId
    idef = sc.doc.InstanceDefinitions.FindId(idef_idx)
    
    if not idef:
        print("未找到图块定义！")
        return
    
    # 3. 弹出交互界面
    dialog = BlockArrayDialog(curve, idef)
    result = dialog.ShowModal(Rhino.UI.RhinoEtoApp.MainWindow)
    
    # 4. 生成实体对象并打组
    if result and dialog.preview_xforms:
        rs.EnableRedraw(False)
        group_idx = sc.doc.Groups.Add()
        
        count = 0
        for xform in dialog.preview_xforms:
            # 使用生成的空间矩阵布置图块
            obj_id = sc.doc.Objects.AddInstanceObject(idef.Index, xform)
            if obj_id:
                sc.doc.Groups.AddToGroup(group_idx, obj_id)
                count += 1
                
        rs.EnableRedraw(True)
        sc.doc.Views.Redraw()
        print(f">> 成功沿线阵列了 {count} 个 [{idef.Name}] 并已自动成组！")
    else:
        print("取消了阵列操作。")

if __name__ == "__main__":
    main()
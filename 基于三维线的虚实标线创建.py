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

# ================= 核心几何算法 =================

def offset_3d_curve_horizontal(curve, offset_dist, tolerance=0.1):
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

def create_marking_surface(curve, width):
    left_crv = offset_3d_curve_horizontal(curve, width / 2.0)
    right_crv = offset_3d_curve_horizontal(curve, -width / 2.0)
    
    loft_res = rg.Brep.CreateFromLoft([left_crv, right_crv], rg.Point3d.Unset, rg.Point3d.Unset, rg.LoftType.Normal, False)
    if loft_res and len(loft_res) > 0:
        brep = loft_res[0]
        # 智能法线检测与翻转
        if brep.Faces.Count > 0:
            face = brep.Faces[0]
            u_mid = face.Domain(0).Mid
            v_mid = face.Domain(1).Mid
            normal = face.NormalAt(u_mid, v_mid)
            if normal.Z < 0:
                brep.Flip()
        return brep
    return None

def create_dashed_markings(curve, width, dash_len, gap_len):
    if dash_len + gap_len < 0.1:
        return[]
        
    total_len = curve.GetLength()
    current_len = 0.0
    result_list =[]
    
    while current_len < total_len:
        end_len = current_len + dash_len
        if end_len > total_len:
            end_len = total_len
            
        success1, t0 = curve.LengthParameter(current_len)
        success2, t1 = curve.LengthParameter(end_len)
        
        if success1 and success2 and t0 != t1:
            sub_curve = curve.Trim(t0, t1)
            if sub_curve:
                dash_brep = create_marking_surface(sub_curve, width)
                if dash_brep:
                    result_list.append(dash_brep)
                    
        current_len += (dash_len + gap_len)
        
    return result_list


# ================= 图层管理 =================

def ensure_layer_structure(color_str, type_str):
    parent_name = "标线"
    sub_name = color_str + type_str
    
    p_idx = sc.doc.Layers.FindByFullPath(parent_name, -1)
    if p_idx < 0:
        p_layer = Rhino.DocObjects.Layer()
        p_layer.Name = parent_name
        p_idx = sc.doc.Layers.Add(p_layer)
        
    parent_layer = sc.doc.Layers[p_idx]
    
    full_path = parent_name + "::" + sub_name
    s_idx = sc.doc.Layers.FindByFullPath(full_path, -1)
    if s_idx < 0:
        s_layer = Rhino.DocObjects.Layer()
        s_layer.Name = sub_name
        s_layer.ParentLayerId = parent_layer.Id
        
        if color_str == "白":
            s_layer.Color = System.Drawing.Color.White
        else:
            s_layer.Color = System.Drawing.Color.FromArgb(255, 200, 0)
            
        s_idx = sc.doc.Layers.Add(s_layer)
        
    return sc.doc.Layers[s_idx].Index


# ================= 交互式 UI 与 预览系统 =================

class SingleMarkingDialog(forms.Dialog[bool]):
    def __init__(self, curve):
        forms.Dialog[bool].__init__(self)
        
        self.Title = "单线创建模式 - 道路标线生成器"
        self.Padding = drawing.Padding(15)
        self.Resizable = False
        
        self.curve = curve
        self.preview_breps =[] 
        self.is_closed = False
        self.result_data = {} 
        
        self.col_yellow = System.Drawing.Color.FromArgb(255, 200, 0)
        self.col_white = System.Drawing.Color.White
        self.mat_yellow = rd.DisplayMaterial(self.col_yellow)
        self.mat_white = rd.DisplayMaterial(self.col_white)
        
        rd.DisplayPipeline.CalculateBoundingBox += self.OnCalcBBox
        rd.DisplayPipeline.PostDrawObjects += self.OnDrawObjects
        
        # --- CPython 3 规范的 UI 控件定义方式 ---
        self.drop_color = forms.DropDown()
        self.drop_color.DataStore = ["白色", "黄色"]
        self.drop_color.SelectedIndex = 0
        
        self.drop_type = forms.DropDown()
        self.drop_type.DataStore =["虚线", "实线"]
        self.drop_type.SelectedIndex = 0
        
        self.num_offset = forms.NumericStepper()
        self.num_offset.Value = 0.0
        self.num_offset.DecimalPlaces = 2
        self.num_offset.Increment = 0.1
        self.num_offset.MinValue = -50.0
        self.num_offset.MaxValue = 50.0
        
        self.num_width = forms.NumericStepper()
        self.num_width.Value = 0.15
        self.num_width.DecimalPlaces = 2
        self.num_width.Increment = 0.01
        self.num_width.MinValue = 0.05
        
        self.num_dash = forms.NumericStepper()
        self.num_dash.Value = 6.0
        self.num_dash.DecimalPlaces = 1
        self.num_dash.Increment = 0.5
        self.num_dash.MinValue = 0.1
        
        self.num_gap = forms.NumericStepper()
        self.num_gap.Value = 9.0
        self.num_gap.DecimalPlaces = 1
        self.num_gap.Increment = 0.5
        self.num_gap.MinValue = 0.1
        
        # 绑定事件
        self.drop_color.SelectedIndexChanged += self.OnParamChanged
        self.drop_type.SelectedIndexChanged += self.OnTypeChanged
        self.num_offset.ValueChanged += self.OnParamChanged
        self.num_width.ValueChanged += self.OnParamChanged
        self.num_dash.ValueChanged += self.OnParamChanged
        self.num_gap.ValueChanged += self.OnParamChanged
        
        # --- 专门为 CPython 3 准备的文本转换辅助函数 ---
        def CreateLabel(text_str):
            lbl = forms.Label()
            lbl.Text = text_str
            return lbl
        
        # --- UI 布局 ---
        layout = forms.DynamicLayout()
        layout.Spacing = drawing.Size(5, 12)
        # 现在每个字符串都被显式转换为了 Label 控件，不再报错！
        layout.AddRow(CreateLabel("标线颜色:"), self.drop_color)
        layout.AddRow(CreateLabel("标线类型:"), self.drop_type)
        layout.AddRow(CreateLabel("横向偏移 (m)[正数向左/负数向右]:"), self.num_offset)
        layout.AddRow(CreateLabel("标线宽度 (m):"), self.num_width)
        layout.AddRow(CreateLabel("虚线段-实线长 (m):"), self.num_dash)
        layout.AddRow(CreateLabel("虚线段-空隙长 (m):"), self.num_gap)
        
        btn_ok = forms.Button()
        btn_ok.Text = "创建标线"
        btn_ok.Click += self.OnOKClick
        
        btn_cancel = forms.Button()
        btn_cancel.Text = "取消"
        btn_cancel.Click += self.OnCancelClick
        
        layout.AddRow(CreateLabel("")) # 空白行
        layout.AddRow(btn_ok, btn_cancel)
        self.Content = layout
        
        self.UpdatePreview()
        
    def OnTypeChanged(self, sender, e):
        is_dash = (self.drop_type.SelectedIndex == 0)
        self.num_dash.Enabled = is_dash
        self.num_gap.Enabled = is_dash
        self.UpdatePreview()

    def OnCalcBBox(self, sender, e):
        if self.is_closed: return
        for brep, _ in self.preview_breps:
            e.IncludeBoundingBox(brep.GetBoundingBox(True))
            
    def OnDrawObjects(self, sender, e):
        if self.is_closed: return
        for brep, mat in self.preview_breps:
            e.Display.DrawBrepShaded(brep, mat)
            
    def OnParamChanged(self, sender, e):
        self.UpdatePreview()
        
    def UpdatePreview(self):
        if self.is_closed: return
        self.preview_breps =[] 
        
        is_white = (self.drop_color.SelectedIndex == 0)
        is_dash = (self.drop_type.SelectedIndex == 0)
        
        offset_val = self.num_offset.Value
        width_val = self.num_width.Value
        dash_val = self.num_dash.Value
        gap_val = self.num_gap.Value
        
        current_mat = self.mat_white if is_white else self.mat_yellow
        
        offset_crv = offset_3d_curve_horizontal(self.curve, offset_val)
        
        breps_to_render =[]
        if is_dash:
            breps_to_render = create_dashed_markings(offset_crv, width_val, dash_val, gap_val)
        else:
            b = create_marking_surface(offset_crv, width_val)
            if b: breps_to_render.append(b)
            
        for d in breps_to_render:
            self.preview_breps.append((d, current_mat))
            
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
        self.result_data = {
            "color_str": "白" if self.drop_color.SelectedIndex == 0 else "黄",
            "type_str": "虚线" if self.drop_type.SelectedIndex == 0 else "实线",
            "breps":[b for b, m in self.preview_breps]
        }
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
    while True:
        crv_id = rs.GetObject("请选择一条道路中心线/参考线 (按ESC退出)", rs.filter.curve)
        if not crv_id: 
            print("工具已退出。")
            break
        
        curve = rs.coercecurve(crv_id)
        if not curve: continue
        
        dialog = SingleMarkingDialog(curve)
        result = dialog.ShowModal(Rhino.UI.RhinoEtoApp.MainWindow)
        
        if result and dialog.result_data:
            rs.EnableRedraw(False)
            data = dialog.result_data
            
            layer_index = ensure_layer_structure(data["color_str"], data["type_str"])
            
            attr = Rhino.DocObjects.ObjectAttributes()
            attr.LayerIndex = layer_index
            attr.ColorSource = Rhino.DocObjects.ObjectColorSource.ColorFromLayer
            
            group_index = sc.doc.Groups.Add()
            
            count = 0
            for brep in data["breps"]:
                obj_id = sc.doc.Objects.AddBrep(brep, attr)
                if obj_id:
                    sc.doc.Groups.AddToGroup(group_index, obj_id)
                    count += 1
            
            rs.EnableRedraw(True)
            sc.doc.Views.Redraw()
            print(">> 成功生成了图层[{}] 的标线面，包含 {} 个曲面并已成组。".format(
                data["color_str"] + data["type_str"], count))
        else:
            print("取消了当次创建。")

if __name__ == "__main__":
    main()
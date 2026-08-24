# Rhino 道路设计脚本集

基于 RhinoCommon API 的道路/公路基础设施设计自动化脚本集合。

## 工具列表

| 脚本 | 功能 |
|------|------|
| `road_markings/三维道路标线生成器.rhproj` | 三维道路标线生成（Rhino 项目文件） |
| `road_markings/基于三维线的虚实标线创建.py` | 沿三维路线生成虚/实标线曲面，带 Eto.Forms 参数面板 |
| `road_centerline_extraction/绿化带板块中线提取.py` | 从绿化带板块或闭合上轮廓提取三维中心线 |
| `street_lighting_and_trees/路灯行道树一键放样.py` | 沿道路曲线批量布置路灯和行道树 |
| `curve_array/沿线阵列.py` | 沿三维曲线参数化阵列排布对象 |
| `beam_loft/横梁放样.py` | 从左右立柱对象或图块的连接端自动提取封闭轮廓并 Loft 生成横梁实体 |
| `purlin_along_curve/沿线檩条.py` | 沿曲线或边线生成矩形檩条实体 |
| `section_purlin_array/断面檩条阵列.py` | 选择路线和声屏障横断面，均布多根沿线连续檩条实体 |
| `face_purlin_between_edges/面内檩条.py` | 选择面和两条长边，在长边之间均分生成檩条实体 |
| `object_scaling/RH单独缩放.py` | 按各自包围盒中心独立缩放多个对象 |
| `object_replacement/图块批量简化替换.py` | 将图块批量替换为定向 Box 或指定替代图块 |
| `z_value_labeling/批量标注点Z值.py` | 批量读取点或点云的 Z 坐标，生成可编辑且可导出 DWG 的文字标注 |
| `sound_barrier/声屏障沿线扫掠.py` | 将平面声屏障轮廓沿三维路线扫掠为 Brep |
| `eicad_rhino/` | 独立的道路路线导入与参数化走廊工具仓库 |

## 使用

在 Rhino 中通过 `RunPythonScript` 加载对应 `.py` 文件运行。

## 批量标注点 Z 值

在 Rhino 8 中运行 `z_value_labeling/批量标注点Z值.py`，选择点对象后依次输入文字高度、小数位数、前缀和文字偏移。脚本会在 `Z标高标注` 图层中创建真正的 Rhino 文字对象；重复运行时可以替换该脚本上一次生成的标注。

导出时在 Rhino 中选择生成的文字，运行 `Export`，文件类型选择 DWG。文字会作为 CAD 文字对象导出，而不是 Rhino 的显示用 TextDot。

`rhino_toolbar/道路工具箱.rui` 的“辅助”分页已加入“标注 Z 值”按钮，可直接启动该脚本。

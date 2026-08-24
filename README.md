# Rhino 道路设计工具集

面向 Rhino 8 / Python 3 的道路、桥梁附属设施和道路 BIM 建模脚本集合。
仓库中的脚本主要用于三维道路几何提取、路线导入、道路标线、声屏障/檩条构件生成，以及 Rhino 文档对象的批量处理。

脚本通常通过 Rhino 8 的 `RunPythonScript` 或 ScriptEditor 运行。除 Rhino 自带的 RhinoCommon、`rhinoscriptsyntax` 和 Eto.Forms 外，项目脚本不依赖额外的第三方 Python 包。

## 功能总览

| 目录 | 内容 |
| --- | --- |
| `road_boundary_extraction/` | 从道路对象提取朝上表面的外轮廓 |
| `road_centerline_extraction/` | 从绿化带或道路板块提取三维中线 |
| `road_markings/` | 根据三维路线创建虚线、实线等道路标线 |
| `street_lighting_and_trees/` | 沿道路曲线布置路灯和行道树 |
| `eicad_rhino/` | EICAD / LandXML 路线导入、三维中心线和参数化走廊建模 |
| `beam_loft/`、`sweep/`、`semi_enclosed_roof/` | 横梁、轮廓和顶板等道路构件建模 |
| `purlin_along_curve/`、`section_purlin_array/`、`face_purlin_between_edges/` | 檩条生成与阵列 |
| `curve_array/` | 沿三维曲线阵列对象 |
| `object_selection/`、`object_scaling/`、`object_replacement/` | Rhino 对象选择、缩放和批量替换 |
| `z_value_labeling/` | 批量创建点和点云的 Z 值文字标注 |
| `rhino_toolbar/` | 道路工具箱 `.rui` 工具栏 |

## 工具清单

### 道路几何与路线

| 脚本 | 用途 |
| --- | --- |
| `road_boundary_extraction/道路上表面外轮廓提取.py` | 对道路网格、曲面、多重曲面或实体提取朝上表面的外轮廓，不输出内部孔洞。 |
| `road_centerline_extraction/绿化带板块中线提取.py` | 从细长的绿化带、道路板块或闭合轮廓中提取三维中心线。 |
| `eicad_rhino/eicad_import/rhino_import_eicad_py3.py` | 将 EICAD 的 ICD/JD、ST、SQX 数据导入为三维路线中心线。 |
| `eicad_rhino/eicad_import/rhino_import_eicad_multi_py3.py` | 批量选择并导入多条 EICAD 路线。 |
| `eicad_rhino/landxml_import/rhino_import_landxml_multi_py3.py` | 从 LandXML 中选择并批量导入路线。 |

### 道路标线与附属设施

| 脚本 | 用途 |
| --- | --- |
| `road_markings/基于三维线的虚实标线创建.py` | 沿三维路线创建虚线、实线等标线曲面，带参数面板。 |
| `street_lighting_and_trees/路灯行道树一键放样.py` | 沿道路曲线批量布置路灯和行道树。 |
| `z_value_labeling/批量标注点Z值.py` | 为点或点云创建可编辑的 Rhino 文字标注，并可导出为 DWG 文字。 |

### 构件与檩条

| 脚本 | 用途 |
| --- | --- |
| `beam_loft/横梁放样.py` | 从左右立柱或图块提取连接端轮廓，Loft 并封口生成横梁实体。 |
| `purlin_along_curve/沿线檩条.py` | 沿三维曲线或 Brep 边线生成矩形檩条实体。 |
| `section_purlin_array/断面檩条阵列.py` | 沿路线连续生成声屏障横断面范围内的檩条。 |
| `face_purlin_between_edges/面内檩条.py` | 在一个面内的两条长边之间均分生成檩条实体。 |
| `semi_enclosed_roof/半封闭中空顶板.py` | 根据两条三维边线生成中分线，并放样两侧留缝的顶板。 |
| `sweep/三维曲线竖直轮廓扫掠.py` | 将 Top 平面轮廓映射到竖直截面，并沿三维路径进行 Roadlike Top Sweep。 |

### Rhino 对象工具

| 脚本 | 用途 |
| --- | --- |
| `curve_array/沿线阵列.py` | 沿三维曲线参数化阵列对象。 |
| `object_selection/按材质选择对象.py` | 按解析后的 Rhino 材质选择对象，支持图层和图块继承。 |
| `object_scaling/RH单独缩放.py` | 以各对象自身包围盒中心独立缩放多个对象。 |
| `object_replacement/图块批量简化替换.py` | 将图块批量替换为定向 Box 或指定替代图块，原对象可归档恢复。 |

## EICAD 与参数化走廊

`eicad_rhino/` 现在是本仓库的一部分，包含三组功能：

```text
eicad_rhino/
├── eicad_import/       EICAD ICD/JD、ST、SQX 路线解析和 Rhino 导入
├── landxml_import/     LandXML 路线解析和批量导入
└── corridor_modeling/  参数化断面、控制线和走廊编辑器
```

更完整的输入格式、计算方法、坐标约定、编辑器用法和回归测试说明，见 [`eicad_rhino/README.md`](eicad_rhino/README.md)。

常用验证命令：

```powershell
py -3.10 .\eicad_rhino\eicad_import\validate_samples.py
py -3.10 .\eicad_rhino\corridor_modeling\validate_corridor.py
```

EICAD 路线通常以米为单位。默认输出坐标为 Rhino `X=东、Y=北`；如果项目采用传统测量坐标，可按子目录 README 中的说明设置 `swap_xy`、`coordinate_scale` 和 `origin_offset`。

## 快速使用

1. 使用 Rhino 8 打开目标模型，并确认模型单位与脚本参数一致。
2. 在 Rhino 中运行目标目录下的 `.py` 文件。
3. 按命令行提示选择路线、曲线、曲面、实体或图块，并设置参数。
4. 对带参数面板的工具，确认预览结果后再执行生成。

道路工具箱位于 `rhino_toolbar/道路工具箱.rui`，可在 Rhino 的工具栏管理器中打开；其中的按钮需要按仓库目录位置找到对应脚本。

## 目录与版本控制说明

- `.proofread-dotx/`、`.proofread-work/` 和 `.workbuddy/` 是本地临时工作目录，已被 `.gitignore` 忽略。
- `__pycache__/`、`*.pyc` 和 `build/` 不纳入版本控制。
- Rhino 脚本依赖 Rhino 8 的 Python 3 运行环境，不能直接在普通 CPython 环境中执行；普通 CPython 仅适合运行 `eicad_rhino` 中不依赖 Rhino 的解析和验证代码。

## 当前边界

本仓库提供可复用的几何处理和建模工具，不是完整的道路设计软件。脚本生成的几何结果仍需结合项目坐标、设计参数、模型单位和工程规范进行复核。

# EICAD 三维路线导入 Rhino

本目录提供一套可复用的 Rhino 8 / Python 3 工作流，用于将 EICAD 的平面、桩号和纵断面数据生成三维道路中心线。

它面向道路 BIM 建模中的基准路线生成，不是针对当前测试工程的一次性文件转换工具。

## 目录

```text
eicad_import/       EICAD ICD/JD、SQX、ST 路线解析和 Rhino 导入
landxml_import/     LandXML 路线解析和 Rhino 批量导入
corridor_modeling/  参数化断面、控制线构建和两个 Rhino 编辑器
```

## 输入文件

每条路线应放在一个文件名同名的文件夹中，例如：

```text
Route-A/
  Route-A.ICD 或 Route-A.JD
  Route-A.ST
  Route-A.SQX
```

`auto` 模式优先使用 `ICD`，因为它直接保存完整平面线元序列。脚本同样支持 `JD`，包括普通曲线、复合曲线和 type-4 记录。

`ST` 提供桩号及带标签的控制桩；`SQX` 提供纵断面变坡点和竖曲线数据。

也可直接选择一个 `.ICD` 或 `.JD` 文件。若选中的项目文件夹内同时存放多条路线，Rhino 会弹出路线文件列表；应选择与所需 `SQX`、`ST` 同名的一项。空的可选文件（例如 0 字节 `SQX`）按未提供处理。

首版只处理路线中心线，未使用 `HDX`、`DMX`、`CG`、`EPM`、`KZD`。这些文件属于后续横断面、地面模型、路幅和 BIM 构件建模的扩展范围，而非三维中心线生成的必要输入。

## 计算方法

对任意桩号 `s`，核心模块计算：

```text
平面路线 -> X(s), Y(s), 方位角(s), 曲率(s)
纵断路线 -> Z(s), 纵坡(s)
三维点   -> X(s), Y(s), Z(s)
```

已通过本测试数据 A-E 路线验证的 `ICD` 线元含义如下：

| 代码 | 线元 | 长度 |
| --- | --- | --- |
| 1 | 直线 | 文件中直接给出 |
| 2 | 圆曲线 | 文件中直接给出 |
| 3 | 进缓和曲线 | `A^2 / R` |
| 4 | 出缓和曲线 | `A^2 / R` |
| 5、6 | R1 与 R2 之间的复合缓和曲线 | `A^2 * abs(1/R2 - 1/R1)` |

直线和圆曲线采用解析计算；缓和曲线采用 16 点 Gauss-Legendre 数值积分。JD 记录按“入半径/缓和长度、主圆曲线半径、出缓和曲线至下一记录入半径”的连续曲线链进行重建。

EICAD 可以不设置缓和曲线而直接连接直线、圆曲线或不同半径圆曲线；这类连接保持位置和切线方向连续，但曲率会突变，脚本会按原始线元保留。JD 中主圆半径为 `0` 且缓和长度为 `0` 的记录表示未设置圆曲线的交点，脚本将其作为折点连接相邻直线。

对于 `SQX`，带半径 `R` 的中间记录视为竖曲线变坡点。设前后纵坡为 `g1`、`g2`，竖曲线长度为：

```text
L = abs(R * (g2 - g1))
```

高程按对应抛物线计算；半径为 `0` 时视为变坡点，不生成竖曲线。

`SQX` 的首末桩号应覆盖平面路线范围。若不覆盖，脚本会在 Rhino 命令历史中提示，并按首末纵坡外推高程；外推段不应作为 BIM 设计成果使用。

Rhino 输出为一次 `PolylineCurve`，而不是拟合 NURBS 曲线。它严格经过所有采样点；采样点必定包含平、竖曲线边界及 `ST` 桩号，并按 `chord_tolerance` 控制三维弦高偏差。

## 运行回归测试

在仓库根目录执行：

```powershell
py -3.10 .\eicad_rhino\eicad_import\validate_samples.py
```

测试内容包括：

- ICD 计算终点与 JD 终点坐标的一致性。
- 带标签 ST 桩号与平面线元边界的一致性。
- SQX 竖曲线的连续性。
- 三维采样弦高偏差不大于 `0.002 m`。
- 每条测试路线的 JD 重建结果与 ICD 重建结果的一致性。

## 在 Rhino 8 中导入

在 Rhino 8 的 ScriptEditor 或 `RunPythonScript` 中运行
`eicad_import/rhino_import_eicad_py3.py`，随后选择一条路线所在的文件夹。

也可以在 Rhino Python 环境中调用：

```python
from eicad_rhino.eicad_import.rhino_import_eicad_py3 import import_route

import_route(
    r"C:\project\Route-A",
    horizontal_source="auto",  # "icd"、"jd" 或 "auto"
    max_step=2.0,
    chord_tolerance=0.002,
    coordinate_scale=1.0,
    origin_offset=(0.0, 0.0, 0.0),
    add_key_station_points=True,
    replace_existing=True,
)
```

EICAD 数据通常以米为单位：Rhino 文档同样使用米时保持 `coordinate_scale=1.0`；只有明确导入毫米制 Rhino 文档时才设置为 `1000.0`。`origin_offset` 在缩放后施加，可用于受控的 BIM 局部坐标转换。

若 EICAD 使用传统测量坐标（首列为北向 X、第二列为东向 Y），而 Rhino 项目坐标要求 X 向东、Y 向北，可在调用时设置 `swap_xy=True`。这只影响 Rhino 输出坐标，不改变解析得到的 EICAD 原始路线。

导入后会在 `EICAD::<路线名>::Centerline` 和 `EICAD::<路线名>::Stations` 图层中创建中心线和带标签的关键桩号点。对象会写入数据来源、桩号范围、采样参数和坐标转换信息等 Rhino 用户文本。

当 `replace_existing=True` 时，脚本只删除带有相同 `eicad_route` 元数据的已有导入对象，再写入新的该路线对象。

## LandXML 路线导入

在 Rhino 8 中运行 `landxml_import/rhino_import_landxml_multi_py3.py`，选择
LandXML 文件后输入项目基点 `X,Y,Z`，再选择一条、多条或全部路线。导入器支持
`Line`、`Curve`、`Spiral`、`PVI` 和 `CircCurve`，按拼音/字母排序路线列表，并将
LandXML 的北坐标、东坐标转换为 Rhino 的 `X=东、Y=北`。项目基点会自动取负值作为
Rhino 局部坐标偏移。

## 参数化断面与控制线

路线导入完成后，路幅和结构不再依赖 EICAD 横断面文件，而是在 Rhino 中用独立的参数化断面定义创建。

推荐在 Rhino 8 中运行
`corridor_modeling/rhino_corridor_block_editor_py3.py`：选择已导入的中心线。编辑器会读取中心线的路线来源和坐标转换信息；若该中心线是旧版导入、没有来源路径，则选择一次相应的 `.ICD` 或 `.JD` 文件。

板块编辑器以“从左到右”的可视化道路板块组织断面，顶部实时显示彩色断面轮廓、板块名称和宽度。一个板块包含名称、类型、宽度、横坡、本板块相对高程、构件类型和输出图层；支持新增、复制、删除、上下调整顺序，以及一个走廊中的多个板块模板和桩号区间。起始偏距和起始高程决定第一个左侧边界，因此路线参考线可以位于道路中心、中分带轴、任意边线或其他自定义位置。

板块的横坡按“向右下坡为正”填写。每个板块可独立设置“本板块相对高程”，相对于模板起始高程；相邻板块高程不同的位置自动生成真实的竖直道牙段。例如中分带填写 `+0.15 m`、两侧车行道保持 `0 m`，进入和离开中分带时都会自动生成 `0.15 m` 道牙。板块数据是 JSON 中的唯一编辑来源，保存和构建时会自动展开为已有的断面控制点，再由同一套已验证的核心生成控制线；无需维护第二套几何算法。默认模板包含土路肩、硬路肩、机动车道和中分带，总宽度为 `34.5 m`，仅作可修改的起步示例。

`corridor_modeling/rhino_corridor_editor_py3.py` 保留为高级的“断面控制点”编辑器，适合直接输入不规则点列，或维护已有的点式 JSON。点式模板在板块编辑器中打开后，会转换为可编辑板块；转换前应确保点列已按从左到右排序。

编辑器中的断面控制点按列表顺序计算。每个点的偏距相对“路线参考线”定义，正偏距为行进方向左侧；点可填写相对高程，或填写与前一点之间的横坡。路线参考偏距和高程偏移由桩号区间设置，因此路线可以是道路中心、中分带轴、任意边线或其他自定义控制线。中分带、分离式路基和不对称路幅均作为普通断面点处理，不要求左右对称。

编辑器将定义保存为 UTF-8 JSON，并可按桩号区间绑定不同模板。点击“保存并构建控制线”后会生成：

- `EICAD::<路线>::Corridor::<走廊>::Sections` 中的三维断面线。
- `EICAD::<路线>::Corridor::<走廊>::Breaklines::<图层>` 中的纵向控制线。

对象记录路线、走廊名称、模板、桩号、构件类型和 JSON 来源等用户文本。再次构建同一“路线 + 走廊”时，仅替换该走廊自身对象。首版刻意不生成路面和结构层实体；后续曲面/实体工具应基于这些已核验的控制线生成。

也可直接运行 `corridor_modeling/rhino_build_corridor_py3.py`：选择中心线和已保存的 JSON，跳过编辑器直接重建控制线。

核心计算可在 Rhino 外验证：

```powershell
py -3.10 .\eicad_rhino\corridor_modeling\validate_corridor.py
```

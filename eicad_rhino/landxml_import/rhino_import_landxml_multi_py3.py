#! python 3
# -*- coding: utf-8 -*-

"""Rhino 8 multi-alignment importer for LandXML 1.2 files.

Run this file from Rhino ScriptEditor or RunPythonScript.  It reads the
Alignments in the selected LandXML file, lets you choose one or more routes,
and creates sampled 3D centre lines in the active document.
"""

from pathlib import Path
import importlib
import math
import sys


MODULE_DIRECTORY = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
if str(MODULE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(MODULE_DIRECTORY))

import landxml_alignment
import rhino_import_landxml_py3

importlib.reload(landxml_alignment)
importlib.reload(rhino_import_landxml_py3)
from landxml_alignment import list_alignments, load_alignments, sort_alignment_infos
from rhino_import_landxml_py3 import import_alignment


IMPORT_ALL_LABEL = "[全部导入所有路线]"
# LandXML is written in the project survey coordinate system.  This is the
# default BIM base point presented in the dialog for the current project.
DEFAULT_PROJECT_BASE_POINT = (22980.409, 27845.371, 28.000)


def main() -> None:
    rs = _rhinoscript_syntax()
    source_path = rs.OpenFileName("选择 LandXML 文件", "LandXML (*.xml)|*.xml||")
    if not source_path:
        return
    base_point = _get_base_point(rs)
    if base_point is None:
        return
    try:
        infos = sort_alignment_infos(list_alignments(source_path))
    except Exception as error:
        rs.MessageBox("LandXML 读取失败：{}".format(error), 0, "LandXML 路线导入")
        return

    labels = [info.display_label for info in infos]
    selected_labels = rs.MultiListBox(
        [IMPORT_ALL_LABEL] + labels,
        "可选择一条或多条路线。3D 表示带纵断；2D / Z=0 表示无纵断。",
        "LandXML 路线导入",
    )
    if not selected_labels:
        return
    try:
        routes = load_alignments(source_path)
    except Exception as error:
        rs.MessageBox("LandXML 解析失败：{}".format(error), 0, "LandXML 路线导入")
        return
    if len(routes) != len(infos):
        rs.MessageBox("LandXML 路线列表与解析结果不一致。", 0, "LandXML 路线导入")
        return
    routes_by_index = {
        getattr(route, "landxml_alignment_index"): route for route in routes
    }

    if IMPORT_ALL_LABEL in selected_labels:
        selected_routes = [routes_by_index[info.index] for info in infos]
    else:
        selected_routes = [
            routes_by_index[infos[labels.index(label)].index] for label in selected_labels
        ]
    _import_routes(rs, source_path, selected_routes, _negative_offset(base_point))


def _import_routes(rs, source_path, routes, origin_offset) -> None:
    imported = []
    failures = []
    for route in routes:
        try:
            result = import_alignment(
                route,
                source_path,
                origin_offset=origin_offset,
            )
            imported.append(result["route"])
        except Exception as error:
            message = "{}: {}".format(route.name, error)
            failures.append(message)
            print("LandXML import failed: {}".format(message))

    summary = "已导入 {} 条路线。".format(len(imported))
    if failures:
        summary += "\n失败 {} 条：\n{}".format(len(failures), "\n".join(failures))
    rs.MessageBox(summary, 0, "LandXML 路线导入")


def _get_base_point(rs):
    default_value = "{:.3f},{:.3f},{:.3f}".format(*DEFAULT_PROJECT_BASE_POINT)
    while True:
        value = rs.GetString("输入项目基点 X,Y,Z（LandXML 坐标）", default_value)
        if value is None:
            return None
        try:
            values = [
                float(part)
                for part in value.replace("，", ",").replace(",", " ").split()
            ]
        except ValueError:
            values = []
        if len(values) == 3 and all(math.isfinite(number) for number in values):
            return tuple(values)
        rs.MessageBox("基点必须是三个有效数字，例如：522980.409,327845.371,28.000", 0, "LandXML 路线导入")


def _negative_offset(base_point):
    return tuple(-coordinate for coordinate in base_point)


def _rhinoscript_syntax():
    try:
        import rhinoscriptsyntax as rs
    except ImportError as error:
        raise RuntimeError("This module must run inside Rhino.") from error
    return rs


if __name__ == "__main__":
    main()

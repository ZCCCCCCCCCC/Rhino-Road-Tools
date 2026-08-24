#! python 3
# -*- coding: utf-8 -*-

"""Rhino 8 multi-route EICAD centreline importer.

Run this file from Rhino ScriptEditor or RunPythonScript.  It lists all ICD/JD
route files in the chosen directory, with an option to import every route.
"""

from pathlib import Path
import importlib
import sys


MODULE_DIRECTORY = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
if str(MODULE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(MODULE_DIRECTORY))

import rhino_import_eicad_py3

# Reload the single-route importer so this launcher always uses the local
# saved parser and Rhino import implementation.
importlib.reload(rhino_import_eicad_py3)
from eicad_alignment import discover_route_files
from rhino_import_eicad_py3 import import_route


IMPORT_ALL_LABEL = "[全部导入所有路线]"
SWAP_XY = True


def main() -> None:
    rs = _rhinoscript_syntax()
    route_directory = rs.BrowseForFolder(None, "选择包含 EICAD 路线文件的文件夹")
    if not route_directory:
        return

    candidates = discover_route_files(route_directory, recursive=True)
    if not candidates:
        rs.MessageBox("该文件夹内没有 ICD 或 JD 路线文件。", 0, "EICAD 路线导入")
        return

    root = Path(route_directory)
    labels = [str(candidate.relative_to(root).with_suffix("")) for candidate in candidates]
    selected_labels = rs.MultiListBox(
        [IMPORT_ALL_LABEL] + labels,
        "可选择一条或多条路线",
        "EICAD 路线导入",
    )
    if not selected_labels:
        return

    if IMPORT_ALL_LABEL in selected_labels:
        selected_candidates = candidates
    else:
        selected_candidates = [
            candidates[labels.index(selected_label)] for selected_label in selected_labels
        ]
    _import_routes(rs, selected_candidates)


def _import_routes(rs, candidates) -> None:
    imported = []
    failures = []
    for candidate in candidates:
        try:
            result = import_route(str(candidate), swap_xy=SWAP_XY)
            imported.append(result["route"])
        except Exception as error:
            message = "{}: {}".format(candidate.stem, error)
            failures.append(message)
            print("EICAD import failed: {}".format(message))

    summary = "已导入 {} 条路线。".format(len(imported))
    if failures:
        summary += "\n失败 {} 条：\n{}".format(len(failures), "\n".join(failures))
    rs.MessageBox(summary, 0, "EICAD 路线导入")


def _rhinoscript_syntax():
    try:
        import rhinoscriptsyntax as rs
    except ImportError as error:
        raise RuntimeError("This module must run inside Rhino.") from error
    return rs


if __name__ == "__main__":
    main()

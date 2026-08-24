#! python 3
# -*- coding: utf-8 -*-

"""Batch-label Rhino point objects with their Z elevations.

Run this file in Rhino 8 with RunPythonScript or from the ScriptEditor.
The result is made with Rhino annotation text objects, so the labels remain
editable text and can be exported to DWG. Point-cloud objects are accepted as
well; every point in a selected point cloud receives a label.
"""

import Rhino
import System
import Rhino.Geometry as rg
import rhinoscriptsyntax as rs
import scriptcontext as sc


LAYER_NAME = "Z标高标注"
LABEL_TAG = "RhinoRoadTools.ZValueLabel.v1"
DEFAULT_TEXT_HEIGHT = 1.0
DEFAULT_DECIMALS = 3
DEFAULT_PREFIX = "Z="
FONT_NAME = "Arial"


def _selected_points(object_ids):
    """Return (point, source_id) pairs from point and point-cloud objects."""
    points = []
    skipped = 0

    for object_id in object_ids:
        rhino_object = sc.doc.Objects.FindId(object_id)
        if rhino_object is None:
            skipped += 1
            continue

        geometry = rhino_object.Geometry
        if isinstance(geometry, rg.Point):
            points.append((geometry.Location, object_id))
            continue

        if isinstance(geometry, rg.PointCloud):
            for index in range(geometry.Count):
                item = geometry[index]
                location = getattr(item, "Location", item)
                points.append((rg.Point3d(location), object_id))
            continue

        skipped += 1

    return points, skipped


def _ensure_layer(layer_name):
    """Find or create the output layer and return its index."""
    layer_index = sc.doc.Layers.Find(layer_name, True)
    if layer_index >= 0:
        return layer_index

    layer = Rhino.DocObjects.Layer()
    layer.Name = layer_name
    layer.Color = System.Drawing.Color.FromArgb(0, 0, 0)
    return sc.doc.Layers.Add(layer)


def _remove_previous_labels(layer_index):
    """Delete only labels created by this script on the output layer."""
    old_ids = []
    for rhino_object in sc.doc.Objects:
        if rhino_object.Attributes.LayerIndex != layer_index:
            continue
        if rhino_object.Attributes.GetUserString("ZLabelTag") == LABEL_TAG:
            old_ids.append(rhino_object.Id)

    if old_ids:
        sc.doc.Objects.Delete(old_ids, True)
    return len(old_ids)


def _parse_offset(value):
    """Parse an X,Y offset entered in model units."""
    normalized = value.replace("，", ",").replace(";", ",")
    parts = [part.strip() for part in normalized.split(",")]
    if len(parts) != 2:
        raise ValueError("偏移量应填写为 X,Y，例如 0.5,0.5")
    return float(parts[0]), float(parts[1])


def _format_z(value, decimals, prefix):
    """Format an elevation while avoiding an undesirable negative zero."""
    rounding_limit = 0.5 * (10.0 ** (-decimals))
    if abs(value) < rounding_limit:
        value = 0.0
    number_format = "{:0." + str(decimals) + "f}"
    return prefix + number_format.format(value)


def _default_text_height():
    """Use the current annotation style as a sensible document-unit default."""
    try:
        height = float(sc.doc.DimStyles.Current.TextHeight)
        if height > 0.0:
            return height
    except (AttributeError, TypeError, ValueError):
        pass
    return DEFAULT_TEXT_HEIGHT


def _make_attributes(layer_index, label_index, source_id):
    attributes = Rhino.DocObjects.ObjectAttributes()
    attributes.LayerIndex = layer_index
    attributes.Name = "Z标高_{:04d}".format(label_index)
    attributes.ColorSource = Rhino.DocObjects.ObjectColorSource.ColorFromLayer
    attributes.SetUserString("ZLabelTag", LABEL_TAG)
    attributes.SetUserString("ZLabelSource", str(source_id))
    return attributes


def main():
    selection_filter = rs.filter.point | getattr(rs.filter, "pointcloud", 0)
    object_ids = rs.GetObjects(
        "选择要标注 Z 值的点（也支持点云）",
        selection_filter,
        preselect=True,
    )
    if not object_ids:
        return

    text_height = rs.GetReal(
        "输入标注文字高度（当前文档单位）",
        _default_text_height(),
        0.000001,
    )
    if text_height is None:
        return

    decimals = rs.GetInteger(
        "输入 Z 值小数位数",
        DEFAULT_DECIMALS,
        0,
        8,
    )
    if decimals is None:
        return

    prefix = rs.GetString(
        "输入标注前缀，直接回车使用默认值；输入 - 表示无前缀",
        DEFAULT_PREFIX,
    )
    if prefix is None:
        return
    if prefix == "-":
        prefix = ""

    default_offset = "{:.6f},{:.6f}".format(
        text_height * 0.5,
        text_height * 0.5,
    )
    offset_text = rs.GetString(
        "输入文字相对点的 XY 偏移（当前文档单位，格式 X,Y）",
        default_offset,
    )
    if offset_text is None:
        return
    try:
        offset_x, offset_y = _parse_offset(offset_text)
    except (TypeError, ValueError) as error:
        rs.MessageBox(str(error), 0, "批量标注 Z 值")
        return

    layer_name = rs.GetString("输入输出图层名称", LAYER_NAME)
    if layer_name is None or not layer_name.strip():
        return
    layer_name = layer_name.strip()

    replace_existing = rs.GetString(
        "是否删除该图层中本脚本上一次生成的标注",
        "是",
        ["是", "否"],
    )
    if replace_existing is None:
        return

    points, skipped = _selected_points(object_ids)
    if not points:
        rs.MessageBox("没有找到可标注的点对象。", 0, "批量标注 Z 值")
        return

    layer_index = _ensure_layer(layer_name)
    undo_record = sc.doc.BeginUndoRecord("批量标注点 Z 值")
    result_ids = []
    removed_count = 0
    rs.EnableRedraw(False)
    try:
        if replace_existing == "是":
            removed_count = _remove_previous_labels(layer_index)

        justification = rg.TextJustification.BottomLeft
        for label_index, (point, source_id) in enumerate(points, start=1):
            insertion_point = rg.Point3d(
                point.X + offset_x,
                point.Y + offset_y,
                point.Z,
            )
            plane = rg.Plane(insertion_point, rg.Vector3d.ZAxis)
            text = _format_z(point.Z, decimals, prefix)
            attributes = _make_attributes(layer_index, label_index, source_id)

            text_id = sc.doc.Objects.AddText(
                text,
                plane,
                text_height,
                FONT_NAME,
                False,
                False,
                justification,
                attributes,
            )
            if text_id != System.Guid.Empty:
                result_ids.append(text_id)
    finally:
        rs.EnableRedraw(True)
        sc.doc.EndUndoRecord(undo_record)
        sc.doc.Views.Redraw()

    if not result_ids:
        rs.MessageBox("标注文字创建失败。", 0, "批量标注 Z 值")
        return

    rs.UnselectAllObjects()
    rs.SelectObjects(result_ids)

    summary = "已生成 {} 个 Z 值文字标注，输出图层：{}。".format(
        len(result_ids), layer_name
    )
    if removed_count:
        summary += "\n已替换上一次生成的 {} 个标注。".format(removed_count)
    if skipped:
        summary += "\n有 {} 个对象未能读取，已跳过。".format(skipped)
    summary += "\n\n这些是 Rhino 文字对象，可直接选择后 Export 为 DWG。"
    rs.MessageBox(summary, 0, "批量标注 Z 值")


if __name__ == "__main__":
    main()

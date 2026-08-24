#! python 3
# -*- coding: utf-8 -*-

"""Batch replace block instances with lightweight replacement objects.

The default replacement is an oriented bounding box matching each source
block definition. Originals are archived on a hidden layer so the operation is
reversible. A block instance can also be selected as the replacement template.
"""

import System
import Rhino
import Rhino.Geometry as rg
import rhinoscriptsyntax as rs
import scriptcontext as sc
from System.Collections.Generic import List


SIMPLIFIED_LAYER = "RoadTools_Simplified"
ARCHIVE_LAYER = "D5代理模型"
BOX_BLOCK_PREFIX = "RoadTools_Simplified_"
USER_KEY = "ROAD_TOOLS_SIMPLIFIED"
SOURCE_KEY = "ROAD_TOOLS_SOURCE_INSTANCE"


def get_block_definition(instance_object):
    if instance_object is None:
        return None

    # Rhino 8 InstanceObject exposes the definition directly. This is the
    # reliable path for block references selected from the document.
    try:
        definition = instance_object.InstanceDefinition
        if definition is not None:
            return definition
    except Exception:
        pass

    # Compatibility fallback for older RhinoCommon wrappers.
    try:
        return sc.doc.InstanceDefinitions.FindId(instance_object.ParentIdefId)
    except Exception:
        return None


def get_block_bbox(idef):
    bbox = rg.BoundingBox.Empty
    if idef is not None:
        for obj in idef.GetObjects():
            geometry = obj.Geometry
            if geometry is not None:
                bbox.Union(geometry.GetBoundingBox(True))
            else:
                bbox.Union(obj.GetBoundingBox(True))

    if not bbox.IsValid:
        bbox = rg.BoundingBox(-0.5, -0.5, 0.0, 0.5, 0.5, 1.0)
    return bbox


def is_valid_object_id(object_id):
    return object_id is not None and object_id != System.Guid.Empty


def ensure_simplified_layer():
    layer = sc.doc.Layers.FindName(SIMPLIFIED_LAYER)
    if layer is not None:
        return layer.Index

    layer_id = rs.AddLayer(
        SIMPLIFIED_LAYER,
        System.Drawing.Color.FromArgb(120, 170, 220),
    )
    if not layer_id:
        return -1

    layer = sc.doc.Layers.FindName(SIMPLIFIED_LAYER)
    return layer.Index if layer is not None else -1


def ensure_archive_layer():
    layer = sc.doc.Layers.FindName(ARCHIVE_LAYER)
    if layer is None:
        layer_id = rs.AddLayer(
            ARCHIVE_LAYER,
            System.Drawing.Color.FromArgb(150, 150, 150),
        )
        if not layer_id:
            return -1
        layer = sc.doc.Layers.FindName(ARCHIVE_LAYER)

    if layer is None:
        return -1

    # The source instances stay in the document but are hidden as a group.
    layer.IsVisible = False
    return layer.Index


def replacement_attributes(source_object, layer_index, name):
    attributes = source_object.Attributes.Duplicate()
    attributes.LayerIndex = layer_index
    attributes.Visible = True
    attributes.Name = name
    attributes.SetUserString(USER_KEY, "1")
    attributes.SetUserString(SOURCE_KEY, str(source_object.Id))
    return attributes


def find_instance_definition(name):
    for index in range(sc.doc.InstanceDefinitions.Count):
        idef = sc.doc.InstanceDefinitions[index]
        if idef is not None and idef.Name == name:
            return idef
    return None


def create_or_get_box_definition(name):
    existing = find_instance_definition(name)
    if existing is not None:
        return existing, None

    unit_box = rg.Box(rg.BoundingBox(0.0, 0.0, 0.0, 1.0, 1.0, 1.0))
    box_brep = unit_box.ToBrep()
    if box_brep is None or not box_brep.IsValid:
        return None, "单位 Box 几何无效"

    geometries = List[rg.GeometryBase]()
    attributes = List[Rhino.DocObjects.ObjectAttributes]()
    geometries.Add(box_brep)
    attributes.Add(Rhino.DocObjects.ObjectAttributes())

    try:
        idef_index = sc.doc.InstanceDefinitions.Add(
            name,
            "RoadTools 生成的简化方盒",
            rg.Point3d.Origin,
            geometries,
            attributes,
        )
    except Exception as exc:
        return None, "创建方盒图块定义失败：{}".format(exc)

    if idef_index < 0:
        return None, "创建方盒图块定义失败，Rhino 没有返回有效索引"
    return sc.doc.InstanceDefinitions[idef_index], None


def box_instance_transform(source_object):
    source_idef = get_block_definition(source_object)
    if source_idef is None:
        return None, "找不到源图块定义"

    try:
        local_bbox = get_block_bbox(source_idef)
        size_x = max(local_bbox.Max.X - local_bbox.Min.X, 1e-9)
        size_y = max(local_bbox.Max.Y - local_bbox.Min.Y, 1e-9)
        size_z = max(local_bbox.Max.Z - local_bbox.Min.Z, 1e-9)

        # The shared block definition is a unit box. Map it to the source
        # definition bbox, then apply the source instance transform.
        local_translation = rg.Transform.Translation(
            rg.Vector3d(local_bbox.Min)
        )
        local_scale = rg.Transform.Scale(
            rg.Plane.WorldXY,
            size_x,
            size_y,
            size_z,
        )
        return (
            source_object.InstanceXform
            * local_translation
            * local_scale,
            None,
        )
    except Exception as exc:
        return None, "生成方盒实例变换时出错：{}".format(exc)


def add_box_replacement(source_object, box_idef, layer_index, index):
    if box_idef is None:
        return None, "方盒图块定义无效"

    xform, error = box_instance_transform(source_object)
    if error:
        return None, error

    attributes = replacement_attributes(
        source_object,
        layer_index,
        "SimplifiedBox_{:04d}".format(index),
    )
    try:
        replacement_id = sc.doc.Objects.AddInstanceObject(
            box_idef.Index,
            xform,
            attributes,
        )
    except Exception as exc:
        # Some Rhino builds lack the attributes overload.
        try:
            replacement_id = sc.doc.Objects.AddInstanceObject(
                box_idef.Index,
                xform,
            )
            if is_valid_object_id(replacement_id):
                sc.doc.Objects.ModifyAttributes(
                    replacement_id,
                    attributes,
                    True,
                )
        except Exception as retry_exc:
            return None, "添加方盒图块失败：{}；重试也失败：{}".format(
                exc,
                retry_exc,
            )

    if not is_valid_object_id(replacement_id):
        return None, "方盒图块创建失败，Rhino 没有返回有效对象 ID"
    return replacement_id, None


def add_block_replacement(source_object, template_idef, layer_index, index):
    if template_idef is None:
        return None, "替代图块定义无效"

    attributes = replacement_attributes(
        source_object,
        layer_index,
        "SimplifiedBlock_{:04d}".format(index),
    )
    try:
        replacement_id = sc.doc.Objects.AddInstanceObject(
            template_idef.Index,
            source_object.InstanceXform,
            attributes,
        )
    except TypeError:
        # Fallback for Rhino builds without the attributes overload.
        replacement_id = sc.doc.Objects.AddInstanceObject(
            template_idef.Index,
            source_object.InstanceXform,
        )
        if replacement_id:
            replacement_object = sc.doc.Objects.FindId(replacement_id)
            if replacement_object is not None:
                sc.doc.Objects.ModifyAttributes(
                    replacement_id,
                    attributes,
                    True,
                )

    if not is_valid_object_id(replacement_id):
        return None, "替代图块创建失败"
    return replacement_id, None


def archive_sources(source_ids, layer_index):
    for source_id in source_ids:
        source_object = sc.doc.Objects.FindId(source_id)
        if source_object is None:
            continue

        attributes = source_object.Attributes.Duplicate()
        attributes.LayerIndex = layer_index
        attributes.Visible = True
        attributes.SetUserString(
            "ROAD_TOOLS_ARCHIVED",
            "D5代理模型",
        )
        sc.doc.Objects.ModifyAttributes(source_id, attributes, True)


def main():
    source_ids = rs.GetObjects(
        "选择要批量替换的图块引例",
        rs.filter.instance,
        preselect=True,
    )
    if not source_ids:
        return

    mode = rs.GetString(
        "选择替换方式",
        "定向Box",
        ["定向Box", "替代图块"],
    )
    if mode is None:
        return

    box_idef = None
    template_idef = None
    replacement_name = None
    if mode == "定向Box":
        input_name = rs.GetString(
            "输入方盒图块名称（自动加前缀 RoadTools_Simplified_）",
            "Box",
        )
        if input_name is None:
            return
        input_name = input_name.strip()
        if not input_name:
            rs.MessageBox(
                "图块名称不能为空。",
                0,
                "图块批量简化替换",
            )
            return

        replacement_name = (
            input_name
            if input_name.startswith(BOX_BLOCK_PREFIX)
            else BOX_BLOCK_PREFIX + input_name
        )
        box_idef, error = create_or_get_box_definition(replacement_name)
        if error:
            rs.MessageBox(
                error,
                0,
                "图块批量简化替换",
            )
            return
    else:
        template_id = rs.GetObject(
            "选择一个作为替代模板的图块引例",
            rs.filter.instance,
            preselect=False,
        )
        if not template_id:
            return
        if template_id in source_ids:
            rs.MessageBox(
                "替代模板不能同时属于待替换图块。",
                0,
                "图块批量简化替换",
            )
            return

        template_object = sc.doc.Objects.FindId(template_id)
        template_idef = get_block_definition(template_object)
        if template_idef is None:
            rs.MessageBox(
                "无法读取替代图块定义。",
                0,
                "图块批量简化替换",
            )
            return

    layer_index = ensure_simplified_layer()
    if layer_index < 0:
        rs.MessageBox(
            "无法创建或读取 RoadTools_Simplified 图层。",
            0,
            "图块批量简化替换",
        )
        return

    archive_layer_index = ensure_archive_layer()
    if archive_layer_index < 0:
        rs.MessageBox(
            "无法创建或读取 D5代理模型 图层。",
            0,
            "图块批量简化替换",
        )
        return

    replacement_ids = []
    failures = []
    rs.EnableRedraw(False)
    try:
        for index, source_id in enumerate(source_ids, start=1):
            source_object = sc.doc.Objects.FindId(source_id)
            if source_object is None:
                failures.append("{}：源对象不存在".format(source_id))
                continue

            if mode == "定向Box":
                replacement_id, error = add_box_replacement(
                    source_object,
                    box_idef,
                    layer_index,
                    index,
                )
            else:
                replacement_id, error = add_block_replacement(
                    source_object,
                    template_idef,
                    layer_index,
                    index,
                )

            if replacement_id:
                replacement_ids.append(replacement_id)
            else:
                failures.append(
                    "{}：{}".format(
                        rs.ObjectName(source_id) or source_id,
                        error or "替换失败",
                    )
                )
    finally:
        rs.EnableRedraw(True)

    if not replacement_ids:
        sc.doc.Views.Redraw()
        details = "\n\n" + "\n".join(failures) if failures else ""
        rs.MessageBox(
            "没有成功创建替代对象。{}".format(details),
            0,
            "图块批量简化替换",
        )
        return

    group_index = sc.doc.Groups.Add()
    for replacement_id in replacement_ids:
        sc.doc.Groups.AddToGroup(group_index, replacement_id)

    archive_sources(source_ids, archive_layer_index)
    rs.UnselectAllObjects()
    rs.SelectObjects(replacement_ids)
    sc.doc.Views.Redraw()

    summary = "已生成 {} 个替代对象，{}，并已成组。".format(
        len(replacement_ids),
        "原图块已归档到隐藏图层 D5代理模型",
    )
    if replacement_name:
        summary += "\n替代图块：{}".format(replacement_name)
    if failures:
        summary += "\n\n以下对象未成功替换：\n{}".format(
            "\n".join(failures)
        )
    rs.MessageBox(summary, 0, "图块批量简化替换")


if __name__ == "__main__":
    main()

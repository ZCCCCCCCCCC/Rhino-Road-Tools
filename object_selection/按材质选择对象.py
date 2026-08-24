#! python 3
# -*- coding: utf-8 -*-

"""Select document objects that use the same resolved Rhino material.

Supports object, layer, and block-parent material inheritance.  Imported models
often contain duplicate material table entries with one shared material name;
when this is detected, the script also selects the same-named duplicates.
"""

import System
import Rhino
import rhinoscriptsyntax as rs
import scriptcontext as sc


def _guid_text(value):
    """Return a usable Guid value as text, or an empty string."""
    if value is None or value == System.Guid.Empty:
        return ""
    return str(value)


def _material_info(object_id):
    """Resolve the material Rhino actually uses to render an object."""
    rhino_object = sc.doc.Objects.FindId(object_id)
    if rhino_object is None:
        return None

    # GetMaterial resolves MaterialFromObject, MaterialFromLayer and
    # MaterialFromParent. It is more reliable than reading MaterialIndex alone.
    material = rhino_object.GetMaterial(True)
    if material is None:
        return None

    rdk_id = _guid_text(getattr(material, "RDKMaterialID", None))
    material_id = _guid_text(getattr(material, "Id", None))
    name = (getattr(material, "Name", "") or "").strip().casefold()
    is_default = bool(getattr(material, "IsDefaultMaterial", False))

    return {
        "rdk_id": rdk_id,
        "material_id": material_id,
        "name": name,
        "is_default": is_default,
    }


def _same_exact_material(sample, candidate):
    """Compare stable material identities before considering a name fallback."""
    if sample["rdk_id"] and sample["rdk_id"] == candidate["rdk_id"]:
        return True
    if sample["material_id"] and sample["material_id"] == candidate["material_id"]:
        return True
    return False


def _same_named_imported_material(sample, candidate):
    """Match duplicate imported material records, but never the generic default."""
    return (
        bool(sample["name"])
        and not sample["is_default"]
        and not candidate["is_default"]
        and sample["name"] == candidate["name"]
    )


def main():
    sample_id = rs.GetObject("选择一个带目标材质的对象", preselect=True)
    if not sample_id:
        return

    sample = _material_info(sample_id)
    if sample is None:
        rs.MessageBox("无法读取所选对象的实际材质。", 0, "按材质选择")
        return

    object_infos = []
    for object_id in rs.AllObjects() or []:
        info = _material_info(object_id)
        if info is not None:
            object_infos.append((object_id, info))

    exact_matches = [
        object_id
        for object_id, info in object_infos
        if _same_exact_material(sample, info)
    ]

    # CAD imports can create several MaterialTable records for the same named
    # material. Include them only when they expand the exact result.
    named_matches = [
        object_id
        for object_id, info in object_infos
        if _same_named_imported_material(sample, info)
    ]
    if len(named_matches) > len(exact_matches):
        matches = named_matches
        match_mode = "同名材质（已合并导入产生的重复材质）"
    else:
        matches = exact_matches
        match_mode = "同一实际材质"

    rs.UnselectAllObjects()
    selected_count = rs.SelectObjects(matches) or 0
    sc.doc.Views.Redraw()

    rs.MessageBox(
        "匹配方式：{}\n找到 {} 个对象，已选中 {} 个。".format(
            match_mode, len(matches), selected_count
        ),
        0,
        "按材质选择",
    )


if __name__ == "__main__":
    main()

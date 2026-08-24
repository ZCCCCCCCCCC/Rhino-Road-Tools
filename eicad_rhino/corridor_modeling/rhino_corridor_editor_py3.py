#! python 3
# -*- coding: utf-8 -*-

"""Eto editor for version-one parametric road corridor definitions in Rhino 8."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple
import importlib
import sys


MODULE_DIRECTORY = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
if str(MODULE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(MODULE_DIRECTORY))

import corridor_model
import rhino_build_corridor_py3

importlib.reload(corridor_model)
importlib.reload(rhino_build_corridor_py3)
from corridor_model import (
    CoordinateTransform,
    CorridorDefinition,
    CorridorError,
    default_definition,
    definition_from_dict,
    definition_to_dict,
    load_definition,
    save_definition,
)
from eicad_alignment import load_route
from rhino_build_corridor_py3 import build_corridor


class CorridorEditor:
    """An Eto dialog backed by the version-one corridor JSON representation."""

    def __init__(
        self,
        rs,
        route_path: str,
        route,
        transform: CoordinateTransform,
        definition: Optional[CorridorDefinition] = None,
        definition_path: Optional[str] = None,
    ) -> None:
        import Eto.Drawing as drawing
        import Eto.Forms as forms

        self.rs = rs
        self.forms = forms
        self.drawing = drawing
        self.route_path = route_path
        self.route = route
        self.transform = transform
        self.definition_path = definition_path
        self.data = definition_to_dict(definition or default_definition(route))

        self.dialog = forms.Dialog()
        self.dialog.Title = "道路断面编辑器"
        self.dialog.ClientSize = drawing.Size(1180, 760)
        self.dialog.MinimumSize = drawing.Size(960, 620)
        self.dialog.Padding = drawing.Padding(10)
        self.dialog.Resizable = True

        self.name_box = _eto(forms.TextBox, Text=str(self.data["name"]))
        self.interval_box = _eto(forms.TextBox, Text=_format_number(self.data["sample_interval"]))
        self.description_box = _eto(
            forms.TextArea, Text=str(self.data.get("description", "")), Wrap=True
        )
        self.path_label = _eto(forms.Label, Text=self._path_display())

        self.template_list = forms.ListBox()
        self.template_list.SelectedIndexChanged += self._on_template_selected
        self.template_name_box = _eto(forms.TextBox)
        self.template_description_box = _eto(forms.TextArea, Wrap=True)

        self.point_list = forms.ListBox()
        self.point_list.SelectedIndexChanged += self._on_point_selected
        self.point_name_box = _eto(forms.TextBox)
        self.point_offset_box = _eto(forms.TextBox)
        self.point_elevation_box = _eto(forms.TextBox)
        self.point_slope_box = _eto(forms.TextBox)
        self.point_feature_box = _eto(forms.TextBox)
        self.point_layer_box = _eto(forms.TextBox)

        self.range_list = forms.ListBox()
        self.range_list.SelectedIndexChanged += self._on_range_selected
        self.range_start_box = _eto(forms.TextBox)
        self.range_end_box = _eto(forms.TextBox)
        self.range_template_box = forms.ComboBox()
        self.range_alignment_offset_box = _eto(forms.TextBox, Text="0")
        self.range_elevation_offset_box = _eto(forms.TextBox, Text="0")
        self.range_interval_box = _eto(forms.TextBox)

        self._build_layout()
        self._refresh_templates()
        self._refresh_ranges()

    def show(self) -> None:
        import Rhino

        self.dialog.ShowModal(Rhino.UI.RhinoEtoApp.MainWindow)

    def _build_layout(self) -> None:
        forms = self.forms
        drawing = self.drawing
        layout = _eto(forms.DynamicLayout, Spacing=drawing.Size(8, 8))
        layout.AddSeparateRow(
            _eto(forms.Label, Text="走廊名称"),
            self.name_box,
            _eto(forms.Label, Text="采样间距 (m)"),
            self.interval_box,
            None,
        )
        layout.AddRow(_eto(forms.Label, Text="说明"), self.description_box)
        layout.AddRow(_eto(forms.Label, Text="JSON 文件"), self.path_label)

        tabs = forms.TabControl()
        tabs.Pages.Add(self._template_page())
        tabs.Pages.Add(self._range_page())
        layout.Add(tabs, yscale=True)

        load_button = _eto(forms.Button, Text="打开 JSON")
        load_button.Click += self._load_json
        save_button = _eto(forms.Button, Text="保存 JSON")
        save_button.Click += self._save_json
        build_button = _eto(forms.Button, Text="保存并构建控制线")
        build_button.Click += self._save_and_build
        close_button = _eto(forms.Button, Text="关闭")
        close_button.Click += lambda sender, event: self.dialog.Close()
        layout.AddSeparateRow(None, load_button, save_button, build_button, close_button)
        self.dialog.Content = layout

    def _template_page(self):
        forms = self.forms
        drawing = self.drawing
        page = _eto(forms.TabPage, Text="模板与断面点")
        layout = _eto(forms.DynamicLayout, Padding=drawing.Padding(8), Spacing=drawing.Size(8, 8))

        template_buttons = _eto(forms.DynamicLayout, Spacing=drawing.Size(4, 4))
        add_template = _eto(forms.Button, Text="新增模板")
        add_template.Click += self._add_template
        update_template = _eto(forms.Button, Text="更新模板")
        update_template.Click += self._update_template
        delete_template = _eto(forms.Button, Text="删除模板")
        delete_template.Click += self._delete_template
        template_buttons.AddRow(add_template, update_template, delete_template)

        template_editor = _eto(forms.DynamicLayout, Spacing=drawing.Size(4, 4))
        template_editor.AddRow(_eto(forms.Label, Text="模板名称"), self.template_name_box)
        template_editor.AddRow(_eto(forms.Label, Text="模板说明"), self.template_description_box)

        left = _eto(forms.DynamicLayout, Spacing=drawing.Size(6, 6))
        left.AddRow(_eto(forms.Label, Text="断面模板"))
        left.Add(self.template_list, yscale=True)
        left.Add(template_buttons)
        left.Add(template_editor)

        point_buttons = _eto(forms.DynamicLayout, Spacing=drawing.Size(4, 4))
        add_point = _eto(forms.Button, Text="新增点")
        add_point.Click += self._add_point
        update_point = _eto(forms.Button, Text="更新点")
        update_point.Click += self._update_point
        remove_point = _eto(forms.Button, Text="删除点")
        remove_point.Click += self._delete_point
        up_point = _eto(forms.Button, Text="上移")
        up_point.Click += lambda sender, event: self._move_point(-1)
        down_point = _eto(forms.Button, Text="下移")
        down_point.Click += lambda sender, event: self._move_point(1)
        point_buttons.AddRow(add_point, update_point, remove_point, up_point, down_point)

        point_editor = _eto(forms.DynamicLayout, Spacing=drawing.Size(4, 4))
        point_editor.AddRow(_eto(forms.Label, Text="点名称"), self.point_name_box)
        point_editor.AddRow(_eto(forms.Label, Text="相对偏距 (m，正值在左)"), self.point_offset_box)
        point_editor.AddRow(_eto(forms.Label, Text="相对高程 (m，留空则用横坡)"), self.point_elevation_box)
        point_editor.AddRow(_eto(forms.Label, Text="与前点横坡 (留空则用高程)"), self.point_slope_box)
        point_editor.AddRow(_eto(forms.Label, Text="构件类型"), self.point_feature_box)
        point_editor.AddRow(_eto(forms.Label, Text="输出图层"), self.point_layer_box)

        right = _eto(forms.DynamicLayout, Spacing=drawing.Size(6, 6))
        right.AddRow(_eto(forms.Label, Text="断面控制点（列表顺序决定横坡计算顺序）"))
        right.Add(self.point_list, yscale=True)
        right.Add(point_buttons)
        right.Add(point_editor)
        layout.AddRow(left, right)
        page.Content = layout
        return page

    def _range_page(self):
        forms = self.forms
        drawing = self.drawing
        page = _eto(forms.TabPage, Text="桩号区间")
        layout = _eto(forms.DynamicLayout, Padding=drawing.Padding(8), Spacing=drawing.Size(8, 8))

        buttons = _eto(forms.DynamicLayout, Spacing=drawing.Size(4, 4))
        add_range = _eto(forms.Button, Text="新增区间")
        add_range.Click += self._add_range
        update_range = _eto(forms.Button, Text="更新区间")
        update_range.Click += self._update_range
        delete_range = _eto(forms.Button, Text="删除区间")
        delete_range.Click += self._delete_range
        buttons.AddRow(add_range, update_range, delete_range)

        editor = _eto(forms.DynamicLayout, Spacing=drawing.Size(4, 4))
        editor.AddRow(_eto(forms.Label, Text="起点桩号"), self.range_start_box)
        editor.AddRow(_eto(forms.Label, Text="终点桩号"), self.range_end_box)
        editor.AddRow(_eto(forms.Label, Text="断面模板"), self.range_template_box)
        editor.AddRow(_eto(forms.Label, Text="路线参考偏距 (m，正值在左)"), self.range_alignment_offset_box)
        editor.AddRow(_eto(forms.Label, Text="路线参考高程偏移 (m)"), self.range_elevation_offset_box)
        editor.AddRow(_eto(forms.Label, Text="本区间采样间距 (m，留空使用全局值)"), self.range_interval_box)

        list_layout = _eto(forms.DynamicLayout, Spacing=drawing.Size(6, 6))
        list_layout.AddRow(_eto(forms.Label, Text="断面模板适用区间"))
        list_layout.Add(self.range_list, yscale=True)
        list_layout.Add(buttons)
        layout.AddRow(list_layout, editor)
        page.Content = layout
        return page

    def _templates(self) -> List[Dict[str, object]]:
        return self.data["templates"]

    def _ranges(self) -> List[Dict[str, object]]:
        return self.data["ranges"]

    def _template_names(self) -> List[str]:
        return [str(template["name"]) for template in self._templates()]

    def _current_template_index(self) -> int:
        return int(self.template_list.SelectedIndex)

    def _current_point_index(self) -> int:
        return int(self.point_list.SelectedIndex)

    def _current_range_index(self) -> int:
        return int(self.range_list.SelectedIndex)

    def _current_template(self) -> Optional[Dict[str, object]]:
        index = self._current_template_index()
        return self._templates()[index] if 0 <= index < len(self._templates()) else None

    def _on_template_selected(self, sender, event) -> None:
        template = self._current_template()
        if template is None:
            return
        self.template_name_box.Text = str(template["name"])
        self.template_description_box.Text = str(template.get("description", ""))
        self._refresh_points()

    def _on_point_selected(self, sender, event) -> None:
        template = self._current_template()
        index = self._current_point_index()
        if template is None or not 0 <= index < len(template["points"]):
            return
        point = template["points"][index]
        self.point_name_box.Text = str(point["name"])
        self.point_offset_box.Text = _format_number(point["offset"])
        self.point_elevation_box.Text = _format_optional_number(point.get("elevation"))
        self.point_slope_box.Text = _format_optional_number(point.get("cross_slope"))
        self.point_feature_box.Text = str(point.get("feature", "surface"))
        self.point_layer_box.Text = str(point.get("layer", "Pavement"))

    def _on_range_selected(self, sender, event) -> None:
        index = self._current_range_index()
        if not 0 <= index < len(self._ranges()):
            return
        section_range = self._ranges()[index]
        self.range_start_box.Text = _format_number(section_range["station_start"])
        self.range_end_box.Text = _format_number(section_range["station_end"])
        self.range_alignment_offset_box.Text = _format_number(section_range.get("alignment_offset", 0.0))
        self.range_elevation_offset_box.Text = _format_number(section_range.get("elevation_offset", 0.0))
        self.range_interval_box.Text = _format_optional_number(section_range.get("sample_interval"))
        self._set_combo_value(self.range_template_box, str(section_range["template"]))

    def _refresh_templates(self, selected_name: Optional[str] = None) -> None:
        names = self._template_names()
        self.template_list.DataStore = names
        self.range_template_box.DataStore = names
        if not names:
            return
        selected_name = selected_name or names[0]
        self.template_list.SelectedIndex = names.index(selected_name) if selected_name in names else 0
        self._refresh_points()

    def _refresh_points(self, selected_index: int = 0) -> None:
        template = self._current_template()
        if template is None:
            self.point_list.DataStore = []
            return
        points = template["points"]
        self.point_list.DataStore = [
            "{:02d}  {} | d={} | z={} | i={}".format(
                index + 1,
                point.get("name", ""),
                _format_number(point.get("offset", 0.0)),
                _format_optional_number(point.get("elevation")),
                _format_optional_number(point.get("cross_slope")),
            )
            for index, point in enumerate(points)
        ]
        if points:
            self.point_list.SelectedIndex = min(max(selected_index, 0), len(points) - 1)

    def _refresh_ranges(self, selected_index: int = 0) -> None:
        self.range_list.DataStore = [
            "{:.3f} .. {:.3f} | {} | d={:.3f} | z={:.3f}".format(
                float(section_range["station_start"]),
                float(section_range["station_end"]),
                section_range["template"],
                float(section_range.get("alignment_offset", 0.0)),
                float(section_range.get("elevation_offset", 0.0)),
            )
            for section_range in self._ranges()
        ]
        if self._ranges():
            self.range_list.SelectedIndex = min(max(selected_index, 0), len(self._ranges()) - 1)

    def _add_template(self, sender, event) -> None:
        name = self._unused_template_name()
        self._templates().append(
            {
                "name": name,
                "description": "",
                "points": [
                    _point("Left edge", 8.0, elevation=0.0),
                    _point("Datum", 0.0, elevation=0.0, feature="datum", layer="Reference"),
                    _point("Right edge", -8.0, elevation=0.0),
                ],
            }
        )
        self._refresh_templates(name)
        self._refresh_ranges()

    def _update_template(self, sender, event) -> None:
        try:
            template = self._require_current_template()
            old_name = str(template["name"])
            new_name = self._required_text(self.template_name_box.Text, "模板名称")
            if new_name != old_name and new_name in self._template_names():
                raise CorridorError("模板名称已存在：{}".format(new_name))
            template["name"] = new_name
            template["description"] = self.template_description_box.Text or ""
            if new_name != old_name:
                for section_range in self._ranges():
                    if section_range["template"] == old_name:
                        section_range["template"] = new_name
            self._refresh_templates(new_name)
            self._refresh_ranges()
        except CorridorError as error:
            self._error(str(error))

    def _delete_template(self, sender, event) -> None:
        try:
            template = self._require_current_template()
            name = str(template["name"])
            if any(section_range["template"] == name for section_range in self._ranges()):
                raise CorridorError("模板正在被桩号区间引用，不能删除。")
            self._templates().pop(self._current_template_index())
            self._refresh_templates()
        except CorridorError as error:
            self._error(str(error))

    def _add_point(self, sender, event) -> None:
        try:
            template = self._require_current_template()
            template["points"].append(_point("Point {}".format(len(template["points"]) + 1), 0.0, elevation=0.0))
            self._refresh_points(len(template["points"]) - 1)
        except CorridorError as error:
            self._error(str(error))

    def _update_point(self, sender, event) -> None:
        try:
            template = self._require_current_template()
            index = self._current_point_index()
            if not 0 <= index < len(template["points"]):
                raise CorridorError("请先选择一个断面点。")
            elevation = self._optional_number(self.point_elevation_box.Text, "相对高程")
            slope = self._optional_number(self.point_slope_box.Text, "横坡")
            if elevation is not None and slope is not None:
                raise CorridorError("相对高程与横坡只能填写其中一个。")
            if index == 0 and elevation is None:
                raise CorridorError("第一个断面点必须填写相对高程。")
            if index > 0 and elevation is None and slope is None:
                raise CorridorError("断面点必须填写相对高程或横坡。")
            template["points"][index] = _point(
                self._required_text(self.point_name_box.Text, "点名称"),
                self._number(self.point_offset_box.Text, "相对偏距"),
                elevation=elevation,
                cross_slope=slope,
                feature=self._required_text(self.point_feature_box.Text, "构件类型"),
                layer=self._required_text(self.point_layer_box.Text, "输出图层"),
            )
            self._refresh_points(index)
        except CorridorError as error:
            self._error(str(error))

    def _delete_point(self, sender, event) -> None:
        try:
            template = self._require_current_template()
            if len(template["points"]) <= 2:
                raise CorridorError("每个模板至少保留两个断面点。")
            index = self._current_point_index()
            if not 0 <= index < len(template["points"]):
                raise CorridorError("请先选择一个断面点。")
            template["points"].pop(index)
            self._refresh_points(index - 1)
        except CorridorError as error:
            self._error(str(error))

    def _move_point(self, direction: int) -> None:
        try:
            template = self._require_current_template()
            index = self._current_point_index()
            target = index + direction
            if not 0 <= index < len(template["points"]) or not 0 <= target < len(template["points"]):
                return
            template["points"][index], template["points"][target] = (
                template["points"][target],
                template["points"][index],
            )
            self._refresh_points(target)
        except CorridorError as error:
            self._error(str(error))

    def _add_range(self, sender, event) -> None:
        names = self._template_names()
        if not names:
            self._error("请先创建一个模板。")
            return
        self._ranges().append(
            {
                "station_start": self.route.station_start,
                "station_end": self.route.station_end,
                "template": names[0],
                "alignment_offset": 0.0,
                "elevation_offset": 0.0,
                "sample_interval": None,
            }
        )
        self._refresh_ranges(len(self._ranges()) - 1)

    def _update_range(self, sender, event) -> None:
        try:
            index = self._current_range_index()
            if not 0 <= index < len(self._ranges()):
                raise CorridorError("请先选择一个桩号区间。")
            template_name = self._combo_value(self.range_template_box)
            if template_name not in self._template_names():
                raise CorridorError("请选择有效的断面模板。")
            self._ranges()[index] = {
                "station_start": self._number(self.range_start_box.Text, "起点桩号"),
                "station_end": self._number(self.range_end_box.Text, "终点桩号"),
                "template": template_name,
                "alignment_offset": self._number(self.range_alignment_offset_box.Text, "路线参考偏距"),
                "elevation_offset": self._number(self.range_elevation_offset_box.Text, "路线参考高程偏移"),
                "sample_interval": self._optional_number(self.range_interval_box.Text, "本区间采样间距"),
            }
            self._refresh_ranges(index)
        except CorridorError as error:
            self._error(str(error))

    def _delete_range(self, sender, event) -> None:
        index = self._current_range_index()
        if not 0 <= index < len(self._ranges()):
            self._error("请先选择一个桩号区间。")
            return
        self._ranges().pop(index)
        self._refresh_ranges(index - 1)

    def _load_json(self, sender, event) -> None:
        path = self.rs.OpenFileName("打开走廊定义 JSON", "Corridor JSON (*.json)|*.json||")
        if not path:
            return
        try:
            definition = load_definition(path)
            self.data = definition_to_dict(definition)
            self.definition_path = path
            self.name_box.Text = str(self.data["name"])
            self.interval_box.Text = _format_number(self.data["sample_interval"])
            self.description_box.Text = str(self.data.get("description", ""))
            self.path_label.Text = self._path_display()
            self._refresh_templates()
            self._refresh_ranges()
        except CorridorError as error:
            self._error(str(error))

    def _save_json(self, sender, event) -> None:
        self._save_definition(show_success=True)

    def _save_and_build(self, sender, event) -> None:
        if not self._save_definition(show_success=False):
            return
        try:
            result = build_corridor(
                self.route_path,
                self.definition_path,
                coordinate_scale=self.transform.coordinate_scale,
                swap_xy=self.transform.swap_xy,
                origin_offset=self.transform.origin_offset,
                replace_existing=True,
            )
            self.rs.MessageBox(
                "已生成 {} 条断面线和 {} 条纵向控制线。".format(
                    result["section_count"], result["breakline_count"]
                ),
                0,
                "道路断面编辑器",
            )
        except Exception as error:
            self._error(str(error))

    def _save_definition(self, show_success: bool) -> bool:
        try:
            self._sync_general_fields()
            definition = definition_from_dict(self.data)
            if not self.definition_path:
                self.definition_path = self.rs.SaveFileName(
                    "保存走廊定义 JSON",
                    "Corridor JSON (*.json)|*.json||",
                    filename="{}.json".format(_safe_filename(definition.name)),
                )
            if not self.definition_path:
                return False
            save_definition(definition, self.definition_path)
            self.path_label.Text = self._path_display()
            if show_success:
                self.rs.MessageBox("走廊定义已保存。", 0, "道路断面编辑器")
            return True
        except CorridorError as error:
            self._error(str(error))
            return False

    def _sync_general_fields(self) -> None:
        self.data["name"] = self._required_text(self.name_box.Text, "走廊名称")
        self.data["sample_interval"] = self._number(self.interval_box.Text, "采样间距")
        self.data["description"] = self.description_box.Text or ""

    def _require_current_template(self) -> Dict[str, object]:
        template = self._current_template()
        if template is None:
            raise CorridorError("请先选择一个模板。")
        return template

    def _unused_template_name(self) -> str:
        used = set(self._template_names())
        index = 1
        while "Template {}".format(index) in used:
            index += 1
        return "Template {}".format(index)

    def _path_display(self) -> str:
        return self.definition_path or "尚未保存"

    def _set_combo_value(self, combo, value: str) -> None:
        names = self._template_names()
        combo.SelectedIndex = names.index(value) if value in names else -1

    def _combo_value(self, combo) -> str:
        index = int(combo.SelectedIndex)
        names = self._template_names()
        return names[index] if 0 <= index < len(names) else ""

    def _required_text(self, value: Optional[str], label: str) -> str:
        result = (value or "").strip()
        if not result:
            raise CorridorError("{}不能为空。".format(label))
        return result

    def _number(self, value: Optional[str], label: str) -> float:
        try:
            return float((value or "").strip())
        except ValueError as error:
            raise CorridorError("{}必须是数字。".format(label)) from error

    def _optional_number(self, value: Optional[str], label: str) -> Optional[float]:
        if not (value or "").strip():
            return None
        return self._number(value, label)

    def _error(self, message: str) -> None:
        self.rs.MessageBox(message, 0, "道路断面编辑器")


def main() -> None:
    rs = _rhinoscript_syntax()
    centreline_id = rs.GetObject("Select an imported EICAD centreline", rs.filter.curve, preselect=True)
    if not centreline_id:
        return
    route_path = _route_path_for_centreline(rs, centreline_id)
    if not route_path:
        return

    route = load_route(route_path)
    centreline_route = rs.GetUserText(centreline_id, "eicad_route")
    if centreline_route and centreline_route != route.name:
        rs.MessageBox("所选中心线与 EICAD 路线文件名称不一致。", 0, "道路断面编辑器")
        return

    transform = CoordinateTransform(
        coordinate_scale=_number_user_text(rs.GetUserText(centreline_id, "eicad_coordinate_scale"), 1.0),
        swap_xy=_bool_user_text(rs.GetUserText(centreline_id, "eicad_swap_xy"), True),
        origin_offset=_origin_user_text(rs.GetUserText(centreline_id, "eicad_origin_offset")),
    )
    CorridorEditor(rs, route_path, route, transform).show()


def _point(
    name: str,
    offset: float,
    elevation: Optional[float] = None,
    cross_slope: Optional[float] = None,
    feature: str = "surface",
    layer: str = "Pavement",
) -> Dict[str, object]:
    return {
        "name": name,
        "offset": offset,
        "elevation": elevation,
        "cross_slope": cross_slope,
        "feature": feature,
        "layer": layer,
    }


def _eto(factory, **properties):
    """Create an Eto control without PythonNet constructor keyword binding."""

    control = factory()
    for name, value in properties.items():
        setattr(control, name, value)
    return control


def _format_number(value: object) -> str:
    return "{:.6g}".format(float(value))


def _format_optional_number(value: object) -> str:
    return "" if value is None else _format_number(value)


def _safe_filename(value: str) -> str:
    return "".join("_" if character in '<>:"/\\|?*' else character for character in value).strip() or "corridor"


def _route_path_for_centreline(rs, centreline_id) -> Optional[str]:
    route_path = rs.GetUserText(centreline_id, "eicad_source_path")
    if route_path and Path(route_path).exists():
        return route_path
    route_path = rs.OpenFileName(
        "Select the EICAD ICD or JD file for this centreline",
        "EICAD route files (*.ICD;*.JD)|*.ICD;*.JD||",
    )
    if route_path:
        route_path = str(Path(route_path).resolve())
        rs.SetUserText(centreline_id, "eicad_source_path", route_path)
    return route_path


def _number_user_text(value: Optional[str], default: float) -> float:
    try:
        return float(value) if value is not None else default
    except ValueError:
        return default


def _bool_user_text(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes")


def _origin_user_text(value: Optional[str]) -> Tuple[float, float, float]:
    if not value:
        return 0.0, 0.0, 0.0
    try:
        items = tuple(float(item) for item in value.split(","))
    except ValueError:
        return 0.0, 0.0, 0.0
    return items if len(items) == 3 else (0.0, 0.0, 0.0)


def _rhinoscript_syntax():
    try:
        import rhinoscriptsyntax as rs
    except ImportError as error:
        raise RuntimeError("This module must run inside Rhino.") from error
    return rs


if __name__ == "__main__":
    main()

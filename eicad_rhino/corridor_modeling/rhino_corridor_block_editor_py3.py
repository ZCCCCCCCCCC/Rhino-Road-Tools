#! python 3
# -*- coding: utf-8 -*-

"""Visual block-based cross-section editor for Rhino 8 / Eto.

Blocks are stored in the corridor JSON and expanded into the existing section
control points, so the tested corridor builder can generate breaklines without
a separate geometry path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple
import importlib
import math
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
    CorridorError,
    SectionBlock,
    default_block_definition,
    definition_from_dict,
    definition_to_dict,
    load_definition,
    points_from_blocks,
    save_definition,
)
from eicad_alignment import load_route
from rhino_build_corridor_py3 import build_corridor


BLOCK_TYPES = (
    "土路肩",
    "硬路肩",
    "机动车道",
    "非机动车道",
    "人行道",
    "分隔带",
    "路缘石",
    "排水沟",
    "绿化带",
    "自定义",
)


class CorridorBlockEditor:
    """Eto editor for visual block templates and station ranges."""

    def __init__(self, rs, route_path: str, route, transform: CoordinateTransform) -> None:
        import Eto.Drawing as drawing
        import Eto.Forms as forms

        self.rs = rs
        self.forms = forms
        self.drawing = drawing
        self.route_path = route_path
        self.route = route
        self.transform = transform
        self.definition_path: Optional[str] = None
        self._preview_error = ""
        self.data = definition_to_dict(default_block_definition(route))

        self.dialog = forms.Dialog()
        self.dialog.Title = "道路板块方案编辑器"
        self.dialog.ClientSize = drawing.Size(1140, 780)
        self.dialog.MinimumSize = drawing.Size(900, 620)
        self.dialog.Padding = drawing.Padding(10)
        self.dialog.Resizable = True

        self.name_box = _eto(forms.TextBox, Text=str(self.data["name"]))
        self.interval_box = _eto(forms.TextBox, Text=_format_number(self.data["sample_interval"]))
        self.path_label = _eto(forms.Label, Text=self._path_display())

        self.template_list = forms.ListBox()
        self.template_list.SelectedIndexChanged += self._on_template_selected
        self.template_name_box = _eto(forms.TextBox)
        self.template_description_box = _eto(forms.TextArea, Wrap=True)
        self.start_offset_box = _eto(forms.TextBox)
        self.start_elevation_box = _eto(forms.TextBox)
        self.total_width_label = _eto(forms.Label)

        self.preview = forms.Drawable()
        self.preview.Height = 200
        self.preview.BackgroundColor = drawing.Colors.Black
        self.preview.Paint += self._paint_preview

        self.block_list = forms.ListBox()
        self.block_list.Height = 120
        self.block_list.SelectedIndexChanged += self._on_block_selected
        self.block_name_box = _eto(forms.TextBox)
        self.block_type_box = forms.ComboBox()
        self.block_type_box.DataStore = list(BLOCK_TYPES)
        self.block_width_box = _eto(forms.TextBox)
        self.block_slope_box = _eto(forms.TextBox)
        self.block_elevation_box = _eto(forms.TextBox)
        self.block_feature_box = _eto(forms.TextBox)
        self.block_layer_box = _eto(forms.TextBox)

        self.range_list = forms.ListBox()
        self.range_list.Height = 140
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
            _eto(forms.Label, Text="方案名称"),
            self.name_box,
            _eto(forms.Label, Text="采样间距 (m)"),
            self.interval_box,
            None,
        )
        layout.AddRow(_eto(forms.Label, Text="JSON 文件"), self.path_label)

        tabs = forms.TabControl()
        tabs.Pages.Add(self._blocks_page())
        tabs.Pages.Add(self._ranges_page())
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

    def _blocks_page(self):
        forms = self.forms
        drawing = self.drawing
        page = _eto(forms.TabPage, Text="板块方案")
        layout = _eto(forms.DynamicLayout, Padding=drawing.Padding(8), Spacing=drawing.Size(8, 8))

        template_controls = _eto(forms.DynamicLayout, Spacing=drawing.Size(4, 4))
        template_buttons = []
        for label, handler in (
            ("新增模板", self._add_template),
            ("更新模板", self._update_template),
            ("删除模板", self._delete_template),
        ):
            button = _eto(forms.Button, Text=label)
            button.Click += handler
            template_buttons.append(button)
        template_controls.AddRow(*template_buttons)

        template_editor = _eto(forms.DynamicLayout, Spacing=drawing.Size(4, 4))
        template_editor.AddRow(_eto(forms.Label, Text="模板名称"), self.template_name_box)
        template_editor.AddRow(_eto(forms.Label, Text="起始偏距 (m，左侧为正)"), self.start_offset_box)
        template_editor.AddRow(_eto(forms.Label, Text="起始高程 (m，相对路线)"), self.start_elevation_box)
        template_editor.AddRow(_eto(forms.Label, Text="模板说明"), self.template_description_box)
        template_editor.AddRow(_eto(forms.Label, Text="总宽度"), self.total_width_label)

        template_layout = _eto(forms.DynamicLayout, Spacing=drawing.Size(6, 6))
        template_layout.AddRow(_eto(forms.Label, Text="板块模板"))
        self.template_list.Height = 120
        template_layout.Add(self.template_list, xscale=True, yscale=True)
        template_layout.Add(template_controls)
        template_layout.Add(template_editor)

        block_buttons = _eto(forms.DynamicLayout, Spacing=drawing.Size(4, 4))
        block_button_row = []
        for label, handler in (
            ("新增", self._add_block),
            ("复制", self._copy_block),
            ("更新", self._update_block),
            ("删除", self._delete_block),
        ):
            button = _eto(forms.Button, Text=label)
            button.Click += handler
            block_button_row.append(button)
        up_button = _eto(forms.Button, Text="上移")
        up_button.Click += lambda sender, event: self._move_block(-1)
        down_button = _eto(forms.Button, Text="下移")
        down_button.Click += lambda sender, event: self._move_block(1)
        block_button_row.extend((up_button, down_button))
        block_buttons.AddRow(*block_button_row)

        block_editor = _eto(forms.DynamicLayout, Spacing=drawing.Size(4, 4))
        block_editor.AddRow(_eto(forms.Label, Text="板块名称"), self.block_name_box)
        block_editor.AddRow(_eto(forms.Label, Text="板块类型"), self.block_type_box)
        block_editor.AddRow(_eto(forms.Label, Text="宽度 (m)"), self.block_width_box)
        block_editor.AddRow(_eto(forms.Label, Text="横坡（向右下坡为正）"), self.block_slope_box)
        block_editor.AddRow(_eto(forms.Label, Text="本板块相对高程 (m)"), self.block_elevation_box)
        block_editor.AddRow(_eto(forms.Label, Text="构件类型"), self.block_feature_box)
        block_editor.AddRow(_eto(forms.Label, Text="输出图层"), self.block_layer_box)

        block_layout = _eto(forms.DynamicLayout, Spacing=drawing.Size(6, 6))
        block_layout.AddRow(_eto(forms.Label, Text="板块顺序（从左到右）"))
        block_layout.Add(self.block_list, xscale=True, yscale=True)
        block_layout.Add(block_buttons)
        block_layout.Add(block_editor)

        editor_layout = _eto(forms.DynamicLayout, Spacing=drawing.Size(10, 6))
        editor_layout.AddRow(template_layout, block_layout)
        layout.Add(self.preview, xscale=True, yscale=False)
        layout.AddRow(editor_layout)
        page.Content = layout
        return page

    def _ranges_page(self):
        forms = self.forms
        drawing = self.drawing
        page = _eto(forms.TabPage, Text="桩号区间")
        layout = _eto(forms.DynamicLayout, Padding=drawing.Padding(8), Spacing=drawing.Size(8, 8))

        buttons = _eto(forms.DynamicLayout, Spacing=drawing.Size(4, 4))
        for label, handler in (
            ("新增区间", self._add_range),
            ("更新区间", self._update_range),
            ("删除区间", self._delete_range),
        ):
            button = _eto(forms.Button, Text=label)
            button.Click += handler
            buttons.AddRow(button)

        editor = _eto(forms.DynamicLayout, Spacing=drawing.Size(4, 4))
        editor.AddRow(_eto(forms.Label, Text="起点桩号"), self.range_start_box)
        editor.AddRow(_eto(forms.Label, Text="终点桩号"), self.range_end_box)
        editor.AddRow(_eto(forms.Label, Text="板块模板"), self.range_template_box)
        editor.AddRow(_eto(forms.Label, Text="路线参考偏距 (m)"), self.range_alignment_offset_box)
        editor.AddRow(_eto(forms.Label, Text="路线参考高程偏移 (m)"), self.range_elevation_offset_box)
        editor.AddRow(_eto(forms.Label, Text="本区间采样间距 (m，留空使用全局值)"), self.range_interval_box)

        ranges_layout = _eto(forms.DynamicLayout, Spacing=drawing.Size(6, 6))
        ranges_layout.AddRow(_eto(forms.Label, Text="模板适用区间"))
        ranges_layout.Add(self.range_list, yscale=True)
        ranges_layout.Add(buttons)
        layout.AddRow(ranges_layout, editor)
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

    def _current_template(self) -> Optional[Dict[str, object]]:
        index = self._current_template_index()
        return self._templates()[index] if 0 <= index < len(self._templates()) else None

    def _current_block_index(self) -> int:
        return int(self.block_list.SelectedIndex)

    def _current_range_index(self) -> int:
        return int(self.range_list.SelectedIndex)

    def _on_template_selected(self, sender, event) -> None:
        template = self._current_template()
        if template is None:
            return
        self._ensure_template_blocks(template)
        self.template_name_box.Text = str(template["name"])
        self.template_description_box.Text = str(template.get("description", ""))
        self.start_offset_box.Text = _format_number(template.get("block_start_offset", 0.0))
        self.start_elevation_box.Text = _format_number(template.get("block_start_elevation", 0.0))
        self._refresh_blocks()
        self._update_total_width()
        self.preview.Invalidate()

    def _on_block_selected(self, sender, event) -> None:
        template = self._current_template()
        index = self._current_block_index()
        if template is None or not 0 <= index < len(template.get("blocks", [])):
            return
        block = template["blocks"][index]
        self.block_name_box.Text = str(block["name"])
        self._set_combo(self.block_type_box, str(block.get("block_type", "自定义")), list(BLOCK_TYPES))
        self.block_width_box.Text = _format_number(block["width"])
        self.block_slope_box.Text = _format_number(block.get("cross_slope", 0.0))
        self.block_elevation_box.Text = _format_number(
            block.get("relative_elevation", block.get("left_curb_height", 0.0))
        )
        self.block_feature_box.Text = str(block.get("feature", "surface"))
        self.block_layer_box.Text = str(block.get("layer", "Pavement"))

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
        self._set_combo(self.range_template_box, str(section_range["template"]), self._template_names())

    def _refresh_templates(self, selected_name: Optional[str] = None) -> None:
        names = self._template_names()
        self.template_list.DataStore = names
        self.range_template_box.DataStore = names
        if names:
            selected_name = selected_name or names[0]
            self.template_list.SelectedIndex = names.index(selected_name) if selected_name in names else 0

    def _refresh_blocks(self, selected_index: int = 0) -> None:
        template = self._current_template()
        if template is None:
            self.block_list.DataStore = []
            return
        blocks = template.get("blocks", [])
        self.block_list.DataStore = [
            "{:02d}  {} | {} | {:.3f} m | 相对高程={:.3f}".format(
                index + 1,
                block.get("name", ""),
                block.get("block_type", ""),
                float(block.get("width", 0.0)),
                float(block.get("relative_elevation", block.get("left_curb_height", 0.0))),
            )
            for index, block in enumerate(blocks)
        ]
        if blocks:
            self.block_list.SelectedIndex = min(max(selected_index, 0), len(blocks) - 1)

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
        template = _new_block_template(name)
        self._templates().append(template)
        self._refresh_templates(name)
        self._refresh_ranges()

    def _update_template(self, sender, event) -> None:
        try:
            template = self._require_template()
            old_name = str(template["name"])
            new_name = self._required_text(self.template_name_box.Text, "模板名称")
            if new_name != old_name and new_name in self._template_names():
                raise CorridorError("模板名称已存在：{}".format(new_name))
            template["name"] = new_name
            template["description"] = self.template_description_box.Text or ""
            template["block_start_offset"] = self._number(self.start_offset_box.Text, "起始偏距")
            template["block_start_elevation"] = self._number(self.start_elevation_box.Text, "起始高程")
            self._regenerate_points(template)
            if new_name != old_name:
                for section_range in self._ranges():
                    if section_range["template"] == old_name:
                        section_range["template"] = new_name
            self._refresh_templates(new_name)
            self._refresh_ranges()
            self.preview.Invalidate()
        except CorridorError as error:
            self._error(str(error))

    def _delete_template(self, sender, event) -> None:
        try:
            template = self._require_template()
            name = str(template["name"])
            if any(section_range["template"] == name for section_range in self._ranges()):
                raise CorridorError("模板正在被桩号区间引用，不能删除。")
            self._templates().pop(self._current_template_index())
            self._refresh_templates()
        except CorridorError as error:
            self._error(str(error))

    def _add_block(self, sender, event) -> None:
        try:
            template = self._require_template()
            template["blocks"].append(_block("新板块", "自定义", 3.0, 0.0))
            self._regenerate_points(template)
            self._refresh_blocks(len(template["blocks"]) - 1)
            self._update_total_width()
            self.preview.Invalidate()
        except CorridorError as error:
            self._error(str(error))

    def _copy_block(self, sender, event) -> None:
        try:
            template = self._require_template()
            index = self._current_block_index()
            blocks = template["blocks"]
            if not 0 <= index < len(blocks):
                raise CorridorError("请先选择一个板块。")
            copied = dict(blocks[index])
            copied["name"] = "{} 副本".format(copied["name"])
            blocks.insert(index + 1, copied)
            self._regenerate_points(template)
            self._refresh_blocks(index + 1)
            self._update_total_width()
            self.preview.Invalidate()
        except CorridorError as error:
            self._error(str(error))

    def _update_block(self, sender, event) -> None:
        try:
            template = self._require_template()
            index = self._current_block_index()
            blocks = template["blocks"]
            if not 0 <= index < len(blocks):
                raise CorridorError("请先选择一个板块。")
            width = self._number(self.block_width_box.Text, "宽度")
            if width < 0.0:
                raise CorridorError("宽度不能为负数。")
            blocks[index] = _block(
                self._required_text(self.block_name_box.Text, "板块名称"),
                self._combo_value(self.block_type_box, list(BLOCK_TYPES), "自定义"),
                width,
                self._number(self.block_slope_box.Text, "横坡"),
                self._number(self.block_elevation_box.Text, "本板块相对高程"),
                self._required_text(self.block_feature_box.Text, "构件类型"),
                self._required_text(self.block_layer_box.Text, "输出图层"),
            )
            self._regenerate_points(template)
            self._refresh_blocks(index)
            self._update_total_width()
            self.preview.Invalidate()
        except CorridorError as error:
            self._error(str(error))

    def _delete_block(self, sender, event) -> None:
        try:
            template = self._require_template()
            blocks = template["blocks"]
            if len(blocks) <= 1:
                raise CorridorError("每个板块模板至少保留一个板块。")
            index = self._current_block_index()
            if not 0 <= index < len(blocks):
                raise CorridorError("请先选择一个板块。")
            blocks.pop(index)
            self._regenerate_points(template)
            self._refresh_blocks(index - 1)
            self._update_total_width()
            self.preview.Invalidate()
        except CorridorError as error:
            self._error(str(error))

    def _move_block(self, direction: int) -> None:
        try:
            template = self._require_template()
            index = self._current_block_index()
            target = index + direction
            blocks = template["blocks"]
            if not 0 <= index < len(blocks) or not 0 <= target < len(blocks):
                return
            blocks[index], blocks[target] = blocks[target], blocks[index]
            self._regenerate_points(template)
            self._refresh_blocks(target)
            self.preview.Invalidate()
        except CorridorError as error:
            self._error(str(error))

    def _add_range(self, sender, event) -> None:
        names = self._template_names()
        if not names:
            self._error("请先创建一个板块模板。")
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
            template_name = self._combo_value(self.range_template_box, self._template_names(), "")
            if not template_name:
                raise CorridorError("请选择有效的板块模板。")
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
            self.data = definition_to_dict(load_definition(path))
            for template in self._templates():
                self._ensure_template_blocks(template)
            self.definition_path = path
            self.name_box.Text = str(self.data["name"])
            self.interval_box.Text = _format_number(self.data["sample_interval"])
            self.path_label.Text = self._path_display()
            self._refresh_templates()
            self._refresh_ranges()
            self.preview.Invalidate()
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
                "道路板块方案编辑器",
            )
        except Exception as error:
            self._error(str(error))

    def _save_definition(self, show_success: bool) -> bool:
        try:
            self._sync_active_template()
            self.data["name"] = self._required_text(self.name_box.Text, "方案名称")
            self.data["sample_interval"] = self._number(self.interval_box.Text, "采样间距")
            definition = definition_from_dict(self.data)
            if not self.definition_path:
                self.definition_path = self.rs.SaveFileName(
                    "保存板块方案 JSON",
                    "Corridor JSON (*.json)|*.json||",
                    filename="{}.json".format(_safe_filename(definition.name)),
                )
            if not self.definition_path:
                return False
            save_definition(definition, self.definition_path)
            self.data = definition_to_dict(definition)
            self.path_label.Text = self._path_display()
            if show_success:
                self.rs.MessageBox("板块方案已保存。", 0, "道路板块方案编辑器")
            return True
        except CorridorError as error:
            self._error(str(error))
            return False

    def _sync_active_template(self) -> None:
        template = self._current_template()
        if template is None:
            return
        template["name"] = self._required_text(self.template_name_box.Text, "模板名称")
        template["description"] = self.template_description_box.Text or ""
        template["block_start_offset"] = self._number(self.start_offset_box.Text, "起始偏距")
        template["block_start_elevation"] = self._number(self.start_elevation_box.Text, "起始高程")
        self._regenerate_points(template)

    def _ensure_template_blocks(self, template: Dict[str, object]) -> None:
        if template.get("blocks"):
            return
        points = template.get("points")
        if not isinstance(points, list) or len(points) < 2:
            raise CorridorError("模板 {} 无法转换为板块。".format(template.get("name", "")))
        elevations = []
        for index, point in enumerate(points):
            elevation = point.get("elevation")
            if elevation is None:
                if index == 0 or point.get("cross_slope") is None:
                    raise CorridorError("模板 {} 缺少可转换高程。".format(template.get("name", "")))
                elevation = elevations[-1] + float(point["cross_slope"]) * (
                    float(point["offset"]) - float(points[index - 1]["offset"])
                )
            elevations.append(float(elevation))
        blocks = []
        for index in range(1, len(points)):
            previous = points[index - 1]
            current = points[index]
            width = float(previous["offset"]) - float(current["offset"])
            if width < -1.0e-9:
                raise CorridorError("模板点必须按从左到右顺序排列后才能转换。")
            delta_offset = float(current["offset"]) - float(previous["offset"])
            elevation_change = elevations[index] - elevations[index - 1]
            slope = elevation_change / delta_offset if abs(delta_offset) > 1.0e-9 else 0.0
            vertical_delta = elevation_change if abs(delta_offset) <= 1.0e-9 else 0.0
            blocks.append(
                _block(
                    "板块 {}".format(index),
                    "自定义",
                    max(width, 0.0),
                    slope,
                    vertical_delta,
                    str(current.get("feature", "surface")),
                    str(current.get("layer", "Pavement")),
                )
            )
        template["blocks"] = blocks
        template["block_start_offset"] = float(points[0]["offset"])
        template["block_start_elevation"] = elevations[0]
        self._regenerate_points(template)

    def _regenerate_points(self, template: Dict[str, object]) -> None:
        blocks = tuple(
            SectionBlock(
                name=str(block["name"]),
                block_type=str(block["block_type"]),
                width=float(block["width"]),
                cross_slope=float(block.get("cross_slope", 0.0)),
                relative_elevation=float(block.get("relative_elevation", block.get("left_curb_height", 0.0))),
                feature=str(block.get("feature", "surface")),
                layer=str(block.get("layer", "Pavement")),
            )
            for block in template["blocks"]
        )
        points = points_from_blocks(
            blocks,
            float(template.get("block_start_offset", 0.0)),
            float(template.get("block_start_elevation", 0.0)),
        )
        template["points"] = [
            {
                "name": point.name,
                "offset": point.offset,
                "elevation": point.elevation,
                "cross_slope": point.cross_slope,
                "feature": point.feature,
                "layer": point.layer,
            }
            for point in points
        ]

    def _paint_preview(self, sender, event) -> None:
        try:
            template = self._current_template()
            graphics = event.Graphics
            graphics.Clear(self.drawing.Colors.Black)
            if template is None:
                return
            self._ensure_template_blocks(template)
            blocks = template["blocks"]
            if not blocks:
                return
            offsets, elevations = _preview_profile(template)
            width = max(1, sender.Width)
            height = max(1, sender.Height)
            left_margin, right_margin, top_margin, bottom_margin = 40.0, 32.0, 48.0, 38.0
            min_offset, max_offset = min(offsets), max(offsets)
            min_elevation, max_elevation = min(elevations), max(elevations)
            offset_range = max(1.0, max_offset - min_offset)
            elevation_range = max(0.25, max_elevation - min_elevation)
            scale_x = max(1.0, (width - left_margin - right_margin) / offset_range)
            scale_y = max(1.0, (height - top_margin - bottom_margin) / elevation_range)

            def map_point(offset, elevation):
                return (
                    left_margin + (max_offset - offset) * scale_x,
                    height - bottom_margin - (elevation - min_elevation) * scale_y,
                )

            for index in range(len(offsets) - 1):
                x0, y0 = map_point(offsets[index], elevations[index])
                x1, y1 = map_point(offsets[index + 1], elevations[index + 1])
                graphics.DrawLine(self.drawing.Colors.White, x0, y0, x1, y1)
            for offset, elevation in zip(offsets, elevations):
                x, y = map_point(offset, elevation)
                graphics.DrawLine(self.drawing.Colors.Yellow, x, height - bottom_margin, x, y)
            block_edges = _block_preview_edges(template)
            for block, start, end in block_edges:
                x0, y0 = map_point(*start)
                x1, y1 = map_point(*end)
                color = _block_color(self.drawing, str(block.get("block_type", "")))
                graphics.DrawLine(color, x0, y0, x1, y1)
            graphics.DrawLine(
                self.drawing.Colors.White,
                left_margin,
                height - bottom_margin,
                width - right_margin,
                height - bottom_margin,
            )
            try:
                font = self.drawing.SystemFonts.Default()
                for block, start, end in block_edges:
                    x0, _ = map_point(*start)
                    x1, _ = map_point(*end)
                    if x1 - x0 > 38.0:
                        color = _block_color(self.drawing, str(block.get("block_type", "")))
                        graphics.DrawText(
                            font,
                            color,
                            (x0 + x1) * 0.5 - 18.0,
                            top_margin * 0.35,
                            str(block.get("name", "")),
                        )
                        graphics.DrawText(
                            font,
                            self.drawing.Colors.White,
                            (x0 + x1) * 0.5 - 14.0,
                            height - bottom_margin + 8.0,
                            "{:.2f}".format(float(block.get("width", 0.0))),
                        )
                graphics.DrawText(
                    font,
                    self.drawing.Colors.DeepSkyBlue,
                    left_margin,
                    10.0,
                    "板块断面预览：总宽 {:.3f} m".format(max_offset - min_offset),
                )
            except Exception:
                # Labels are supplementary; the profile remains visible even
                # when a platform font cannot be resolved.
                pass
            self._preview_error = ""
        except Exception as error:
            message = "道路板块预览错误：{}".format(error)
            if message != self._preview_error:
                self._preview_error = message
                try:
                    import Rhino

                    Rhino.RhinoApp.WriteLine(message)
                except Exception:
                    pass
            # Preview must never prevent users from editing the JSON definition.
            return

    def _update_total_width(self) -> None:
        template = self._current_template()
        if template is None:
            return
        total_width = sum(float(block.get("width", 0.0)) for block in template.get("blocks", []))
        self.total_width_label.Text = "{:.3f} m".format(total_width)

    def _require_template(self) -> Dict[str, object]:
        template = self._current_template()
        if template is None:
            raise CorridorError("请先选择一个板块模板。")
        self._ensure_template_blocks(template)
        return template

    def _unused_template_name(self) -> str:
        names = set(self._template_names())
        index = 1
        while "板块模板 {}".format(index) in names:
            index += 1
        return "板块模板 {}".format(index)

    def _set_combo(self, combo, value: str, items: List[str]) -> None:
        combo.SelectedIndex = items.index(value) if value in items else -1

    def _combo_value(self, combo, items: List[str], default: str) -> str:
        index = int(combo.SelectedIndex)
        return items[index] if 0 <= index < len(items) else default

    def _required_text(self, value: Optional[str], label: str) -> str:
        result = (value or "").strip()
        if not result:
            raise CorridorError("{}不能为空。".format(label))
        return result

    def _number(self, value: Optional[str], label: str) -> float:
        try:
            result = float((value or "").strip())
        except ValueError as error:
            raise CorridorError("{}必须是数字。".format(label)) from error
        if not math.isfinite(result):
            raise CorridorError("{}必须是有限数字。".format(label))
        return result

    def _optional_number(self, value: Optional[str], label: str) -> Optional[float]:
        return None if not (value or "").strip() else self._number(value, label)

    def _path_display(self) -> str:
        return self.definition_path or "尚未保存"

    def _error(self, message: str) -> None:
        self.rs.MessageBox(message, 0, "道路板块方案编辑器")


def main() -> None:
    rs = _rhinoscript_syntax()
    centreline_id = rs.GetObject("选择已导入的 EICAD 路线中心线", rs.filter.curve, preselect=True)
    if not centreline_id:
        return
    route_path = _route_path_for_centreline(rs, centreline_id)
    if not route_path:
        return
    route = load_route(route_path)
    centreline_route = rs.GetUserText(centreline_id, "eicad_route")
    if centreline_route and centreline_route != route.name:
        rs.MessageBox("所选中心线与 EICAD 路线文件名称不一致。", 0, "道路板块方案编辑器")
        return
    transform = CoordinateTransform(
        coordinate_scale=_number_user_text(rs.GetUserText(centreline_id, "eicad_coordinate_scale"), 1.0),
        swap_xy=_bool_user_text(rs.GetUserText(centreline_id, "eicad_swap_xy"), True),
        origin_offset=_origin_user_text(rs.GetUserText(centreline_id, "eicad_origin_offset")),
    )
    CorridorBlockEditor(rs, route_path, route, transform).show()


def _new_block_template(name: str) -> Dict[str, object]:
    template = {
        "name": name,
        "description": "",
        "block_start_offset": 5.0,
        "block_start_elevation": 0.0,
        "blocks": [_block("道路板块", "机动车道", 10.0, 0.0, feature="lane")],
    }
    _regenerate_template_points_static(template)
    return template


def _block(
    name: str,
    block_type: str,
    width: float,
    cross_slope: float,
    relative_elevation: float = 0.0,
    feature: str = "surface",
    layer: str = "Pavement",
) -> Dict[str, object]:
    return {
        "name": name,
        "block_type": block_type,
        "width": width,
        "cross_slope": cross_slope,
        "relative_elevation": relative_elevation,
        "feature": feature,
        "layer": layer,
    }


def _regenerate_template_points_static(template: Dict[str, object]) -> None:
    blocks = tuple(
        SectionBlock(
            str(block["name"]),
            str(block["block_type"]),
            float(block["width"]),
            float(block.get("cross_slope", 0.0)),
            float(block.get("relative_elevation", block.get("left_curb_height", 0.0))),
            str(block.get("feature", "surface")),
            str(block.get("layer", "Pavement")),
        )
        for block in template["blocks"]
    )
    points = points_from_blocks(
        blocks,
        float(template.get("block_start_offset", 0.0)),
        float(template.get("block_start_elevation", 0.0)),
    )
    template["points"] = [
        {
            "name": point.name,
            "offset": point.offset,
            "elevation": point.elevation,
            "cross_slope": point.cross_slope,
            "feature": point.feature,
            "layer": point.layer,
        }
        for point in points
    ]


def _preview_profile(template: Dict[str, object]) -> Tuple[List[float], List[float]]:
    offset = float(template.get("block_start_offset", 0.0))
    elevation = float(template.get("block_start_elevation", 0.0))
    offsets, elevations = [offset], [elevation]
    for block in template.get("blocks", []):
        block_start_elevation = float(template.get("block_start_elevation", 0.0)) + float(
            block.get("relative_elevation", block.get("left_curb_height", 0.0))
        )
        if abs(block_start_elevation - elevation) > 1.0e-12:
            elevation = block_start_elevation
            offsets.append(offset)
            elevations.append(elevation)
        next_offset = offset - float(block.get("width", 0.0))
        elevation += float(block.get("cross_slope", 0.0)) * (next_offset - offset)
        offsets.append(next_offset)
        elevations.append(elevation)
        offset = next_offset
    return offsets, elevations


def _block_preview_edges(template: Dict[str, object]):
    """Return the sloped surface edge of each block, excluding curb risers."""

    offset = float(template.get("block_start_offset", 0.0))
    elevation = float(template.get("block_start_elevation", 0.0))
    edges = []
    for block in template.get("blocks", []):
        elevation = float(template.get("block_start_elevation", 0.0)) + float(
            block.get("relative_elevation", block.get("left_curb_height", 0.0))
        )
        start = offset, elevation
        next_offset = offset - float(block.get("width", 0.0))
        elevation += float(block.get("cross_slope", 0.0)) * (next_offset - offset)
        end = next_offset, elevation
        edges.append((block, start, end))
        offset = next_offset
    return edges


def _block_color(drawing, block_type: str):
    names = {
        "机动车道": "DeepSkyBlue",
        "非机动车道": "DodgerBlue",
        "人行道": "LightGray",
        "分隔带": "LimeGreen",
        "绿化带": "ForestGreen",
        "硬路肩": "Orange",
        "土路肩": "SandyBrown",
        "路缘石": "White",
        "排水沟": "MediumPurple",
    }
    return getattr(drawing.Colors, names.get(block_type, "Yellow"))


def _eto(factory, **properties):
    """Create Eto controls without PythonNet constructor keyword binding."""

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
        "为该中心线选择 EICAD ICD 或 JD 文件",
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

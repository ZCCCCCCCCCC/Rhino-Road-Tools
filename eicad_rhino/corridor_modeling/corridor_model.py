"""Parametric road corridor definitions and station-based 3D evaluation.

This module intentionally has no Rhino dependency.  It consumes a RouteModel
from eicad_alignment.py and turns editable section templates into 3D section
vertices and longitudinal breakline runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import json
import math
import sys


MODULE_DIRECTORY = Path(__file__).resolve().parent
EICAD_IMPORT_DIRECTORY = MODULE_DIRECTORY.parent / "eicad_import"
if str(EICAD_IMPORT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(EICAD_IMPORT_DIRECTORY))

from eicad_alignment import EPSILON, RouteModel


class CorridorError(ValueError):
    """Raised when a corridor definition cannot be evaluated safely."""


@dataclass(frozen=True)
class SectionPoint:
    """A named control point in a typical section.

    ``offset`` is in metres from the template datum; positive is left of the
    increasing station direction.  Set either ``elevation`` relative to the
    datum or ``cross_slope`` from the previous point.  A cross slope is rise
    over offset, so -0.02 is a 2 percent fall as offset increases.
    """

    name: str
    offset: float
    elevation: Optional[float] = None
    cross_slope: Optional[float] = None
    feature: str = "surface"
    layer: str = "Pavement"


@dataclass(frozen=True)
class SectionBlock:
    """One visual cross-section block extending from left to right.

    The block begins at the preceding boundary and extends toward decreasing
    offset.  Its cross slope follows the same sign convention as SectionPoint:
    elevation change equals ``cross_slope * offset_change``.  Its relative
    elevation is measured from the template datum.  The difference between
    adjacent blocks creates true vertical curb steps automatically.
    """

    name: str
    block_type: str
    width: float
    cross_slope: float = 0.0
    relative_elevation: float = 0.0
    feature: str = "surface"
    layer: str = "Pavement"


@dataclass(frozen=True)
class SectionTemplate:
    """A free-form set of cross-section control points.

    No left/right symmetry is assumed.  A median, separated carriageway or a
    route offset from the road centre are represented by ordinary points.
    """

    name: str
    points: Tuple[SectionPoint, ...]
    description: str = ""
    blocks: Tuple[SectionBlock, ...] = ()
    block_start_offset: float = 0.0
    block_start_elevation: float = 0.0


@dataclass(frozen=True)
class TemplateRange:
    """The station interval and datum placement for one section template."""

    station_start: float
    station_end: float
    template: str
    alignment_offset: float = 0.0
    elevation_offset: float = 0.0
    sample_interval: Optional[float] = None


@dataclass(frozen=True)
class CorridorDefinition:
    """A complete editable corridor configuration."""

    name: str
    sample_interval: float
    templates: Mapping[str, SectionTemplate]
    ranges: Tuple[TemplateRange, ...]
    description: str = ""
    version: int = 1


@dataclass(frozen=True)
class CoordinateTransform:
    """Map EICAD plan coordinates to the Rhino document coordinate system."""

    coordinate_scale: float = 1.0
    swap_xy: bool = True
    origin_offset: Tuple[float, float, float] = (0.0, 0.0, 0.0)

    def point(self, x: float, y: float, z: float) -> Tuple[float, float, float]:
        if self.coordinate_scale <= 0.0:
            raise CorridorError("coordinate_scale must be positive.")
        if len(self.origin_offset) != 3:
            raise CorridorError("origin_offset must contain X, Y and Z.")
        if self.swap_xy:
            x, y = y, x
        return (
            x * self.coordinate_scale + self.origin_offset[0],
            y * self.coordinate_scale + self.origin_offset[1],
            z * self.coordinate_scale + self.origin_offset[2],
        )

    def tangent(self, eicad_heading: float) -> Tuple[float, float]:
        tangent_x = math.cos(eicad_heading)
        tangent_y = math.sin(eicad_heading)
        if self.swap_xy:
            tangent_x, tangent_y = tangent_y, tangent_x
        return tangent_x, tangent_y


@dataclass(frozen=True)
class EvaluatedVertex:
    """A three-dimensional template control point at one station."""

    station: float
    template: str
    name: str
    feature: str
    layer: str
    offset: float
    elevation_offset: float
    x: float
    y: float
    z: float

    @property
    def key(self) -> Tuple[str, str, str]:
        return self.name, self.feature, self.layer


@dataclass(frozen=True)
class EvaluatedSection:
    """All template vertices evaluated at a route station."""

    station: float
    range_index: int
    template: str
    vertices: Tuple[EvaluatedVertex, ...]


@dataclass(frozen=True)
class BreaklineRun:
    """A same-feature longitudinal line over one template station range."""

    name: str
    feature: str
    layer: str
    range_index: int
    vertices: Tuple[EvaluatedVertex, ...]


@dataclass(frozen=True)
class CorridorGeometry:
    """Calculated sections and breaklines ready for a Rhino adapter."""

    definition: CorridorDefinition
    sections: Tuple[EvaluatedSection, ...]
    breaklines: Tuple[BreaklineRun, ...]


def validate_definition(definition: CorridorDefinition, route: Optional[RouteModel] = None) -> None:
    """Validate templates and ranges before writing any Rhino geometry."""

    if definition.version != 1:
        raise CorridorError("Unsupported corridor definition version {}.".format(definition.version))
    if not definition.name.strip():
        raise CorridorError("Corridor name cannot be empty.")
    if definition.sample_interval <= EPSILON:
        raise CorridorError("sample_interval must be positive.")
    if not definition.templates:
        raise CorridorError("At least one section template is required.")
    if not definition.ranges:
        raise CorridorError("At least one template range is required.")

    for template_name, template in definition.templates.items():
        if template_name != template.name:
            raise CorridorError("Template mapping key does not match template name: {}.".format(template_name))
        _validate_template(template)

    previous_end: Optional[float] = None
    for index, section_range in enumerate(definition.ranges):
        if section_range.template not in definition.templates:
            raise CorridorError(
                "Range {} references unknown template {}.".format(index + 1, section_range.template)
            )
        if section_range.station_end < section_range.station_start - EPSILON:
            raise CorridorError("Range {} ends before it starts.".format(index + 1))
        if section_range.sample_interval is not None and section_range.sample_interval <= EPSILON:
            raise CorridorError("Range {} has a non-positive sample interval.".format(index + 1))
        if previous_end is not None and section_range.station_start < previous_end - EPSILON:
            raise CorridorError("Template ranges overlap before range {}.".format(index + 1))
        previous_end = section_range.station_end

        if route:
            if section_range.station_start < route.station_start - EPSILON:
                raise CorridorError("Range {} starts before the route.".format(index + 1))
            if section_range.station_end > route.station_end + EPSILON:
                raise CorridorError("Range {} ends after the route.".format(index + 1))


def evaluate_corridor(
    route: RouteModel,
    definition: CorridorDefinition,
    transform: CoordinateTransform = CoordinateTransform(),
) -> CorridorGeometry:
    """Evaluate all corridor control sections and longitudinal breaklines."""

    validate_definition(definition, route)
    sections: List[EvaluatedSection] = []
    breaklines: List[BreaklineRun] = []

    for range_index, section_range in enumerate(definition.ranges):
        template = definition.templates[section_range.template]
        range_sections = [
            evaluate_section(route, template, section_range, range_index, station, transform)
            for station in _stations_for_range(section_range, definition.sample_interval)
        ]
        sections.extend(range_sections)
        breaklines.extend(_breaklines_for_range(range_sections, range_index))

    return CorridorGeometry(definition, tuple(sections), tuple(breaklines))


def evaluate_section(
    route: RouteModel,
    template: SectionTemplate,
    section_range: TemplateRange,
    range_index: int,
    station: float,
    transform: CoordinateTransform = CoordinateTransform(),
) -> EvaluatedSection:
    """Evaluate one cross section in the Rhino coordinate system."""

    if station < section_range.station_start - EPSILON or station > section_range.station_end + EPSILON:
        raise CorridorError("Station is outside its template range.")
    route_point = route.point_at(station)
    base_x, base_y, base_z = transform.point(route_point.x, route_point.y, route_point.z)
    tangent_x, tangent_y = transform.tangent(route.horizontal.heading_at(station))
    tangent_length = math.hypot(tangent_x, tangent_y)
    if tangent_length <= EPSILON:
        raise CorridorError("Route tangent is undefined at station {:.3f}.".format(station))
    tangent_x /= tangent_length
    tangent_y /= tangent_length
    left_x, left_y = -tangent_y, tangent_x

    points = _template_points(template)
    elevations = _point_elevations(points)
    vertices = []
    for point, elevation in zip(points, elevations):
        total_offset = section_range.alignment_offset + point.offset
        scaled_offset = total_offset * transform.coordinate_scale
        vertices.append(
            EvaluatedVertex(
                station=station,
                template=template.name,
                name=point.name,
                feature=point.feature,
                layer=point.layer,
                offset=total_offset,
                elevation_offset=section_range.elevation_offset + elevation,
                x=base_x + left_x * scaled_offset,
                y=base_y + left_y * scaled_offset,
                z=base_z + (section_range.elevation_offset + elevation) * transform.coordinate_scale,
            )
        )
    return EvaluatedSection(station, range_index, template.name, tuple(vertices))


def definition_from_dict(data: Mapping[str, object]) -> CorridorDefinition:
    """Create a validated corridor definition from version-one JSON data."""

    try:
        template_items = data["templates"]
        range_items = data["ranges"]
    except KeyError as error:
        raise CorridorError("Corridor JSON needs templates and ranges.") from error
    if not isinstance(template_items, list) or not isinstance(range_items, list):
        raise CorridorError("Corridor templates and ranges must be lists.")

    templates: Dict[str, SectionTemplate] = {}
    for item in template_items:
        if not isinstance(item, Mapping):
            raise CorridorError("Each template must be an object.")
        name = _required_string(item, "name")
        if name in templates:
            raise CorridorError("Duplicate template name {}.".format(name))
        raw_blocks = item.get("blocks")
        if raw_blocks is not None and not isinstance(raw_blocks, list):
            raise CorridorError("Template {} blocks must be a list.".format(name))
        blocks = tuple(_block_from_dict(block, name) for block in raw_blocks or [])
        block_start_offset = _optional_number(item.get("block_start_offset"), "block_start_offset")
        block_start_elevation = _optional_number(item.get("block_start_elevation"), "block_start_elevation")
        raw_points = item.get("points")
        # Visual blocks are the editable source of truth.  ``points`` remains
        # in the JSON for compatibility with the existing control-line path.
        if blocks:
            points = points_from_blocks(
                blocks,
                block_start_offset or 0.0,
                block_start_elevation or 0.0,
            )
        elif isinstance(raw_points, list):
            points = tuple(_point_from_dict(point, name) for point in raw_points)
        else:
            raise CorridorError("Template {} needs a points or blocks list.".format(name))
        templates[name] = SectionTemplate(
            name,
            points,
            str(item.get("description", "")),
            blocks,
            block_start_offset or 0.0,
            block_start_elevation or 0.0,
        )

    ranges = tuple(_range_from_dict(item) for item in range_items)
    definition = CorridorDefinition(
        name=_required_string(data, "name"),
        sample_interval=_number(data.get("sample_interval"), "sample_interval"),
        templates=templates,
        ranges=ranges,
        description=str(data.get("description", "")),
        version=int(data.get("version", 1)),
    )
    validate_definition(definition)
    return definition


def definition_to_dict(definition: CorridorDefinition) -> Dict[str, object]:
    """Return a stable JSON-ready representation of a corridor definition."""

    validate_definition(definition)
    return {
        "version": definition.version,
        "name": definition.name,
        "description": definition.description,
        "sample_interval": definition.sample_interval,
        "templates": [
            _template_to_dict(template)
            for template in definition.templates.values()
        ],
        "ranges": [
            {
                "station_start": section_range.station_start,
                "station_end": section_range.station_end,
                "template": section_range.template,
                "alignment_offset": section_range.alignment_offset,
                "elevation_offset": section_range.elevation_offset,
                "sample_interval": section_range.sample_interval,
            }
            for section_range in definition.ranges
        ],
    }


def _template_to_dict(template: SectionTemplate) -> Dict[str, object]:
    points = _template_points(template)
    result: Dict[str, object] = {
        "name": template.name,
        "description": template.description,
        "points": [
            {
                "name": point.name,
                "offset": point.offset,
                "elevation": point.elevation,
                "cross_slope": point.cross_slope,
                "feature": point.feature,
                "layer": point.layer,
            }
            for point in points
        ],
    }
    if template.blocks:
        result.update(
            {
                "block_start_offset": template.block_start_offset,
                "block_start_elevation": template.block_start_elevation,
                "blocks": [
                    {
                        "name": block.name,
                        "block_type": block.block_type,
                        "width": block.width,
                        "cross_slope": block.cross_slope,
                        "relative_elevation": block.relative_elevation,
                        "feature": block.feature,
                        "layer": block.layer,
                    }
                    for block in template.blocks
                ],
            }
        )
    return result


def points_from_blocks(
    blocks: Sequence[SectionBlock],
    start_offset: float,
    start_elevation: float,
) -> Tuple[SectionPoint, ...]:
    """Expand visual blocks into explicit boundary control points."""

    if not blocks:
        raise CorridorError("A block template needs at least one block.")
    first_block = blocks[0]
    points = [
        SectionPoint(
            "断面左边界",
            start_offset,
            elevation=start_elevation,
            feature="edge",
            layer=first_block.layer,
        )
    ]
    offset = start_offset
    elevation = start_elevation
    for block in blocks:
        block_start_elevation = start_elevation + block.relative_elevation
        if abs(block_start_elevation - elevation) > EPSILON:
            elevation = block_start_elevation
            points.append(
                SectionPoint(
                    "{}左边界".format(block.name),
                    offset,
                    elevation=elevation,
                    feature="curb",
                    layer=block.layer,
                )
            )
        next_offset = offset - block.width
        elevation += block.cross_slope * (next_offset - offset)
        points.append(
            SectionPoint(
                "{}右边界".format(block.name),
                next_offset,
                elevation=elevation,
                feature=block.feature,
                layer=block.layer,
            )
        )
        offset = next_offset
    return tuple(points)


def _template_points(template: SectionTemplate) -> Tuple[SectionPoint, ...]:
    """Return the effective control points, deriving them for block templates."""

    if template.blocks:
        return points_from_blocks(
            template.blocks,
            template.block_start_offset,
            template.block_start_elevation,
        )
    return template.points


def load_definition(path: Path | str) -> CorridorDefinition:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise CorridorError("Cannot read corridor JSON: {}".format(path)) from error
    except json.JSONDecodeError as error:
        raise CorridorError("Invalid corridor JSON {}: {}".format(path, error.msg)) from error
    if not isinstance(data, Mapping):
        raise CorridorError("Corridor JSON root must be an object.")
    return definition_from_dict(data)


def save_definition(definition: CorridorDefinition, path: Path | str) -> None:
    path = Path(path)
    path.write_text(
        json.dumps(definition_to_dict(definition), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def default_definition(route: RouteModel, name: Optional[str] = None) -> CorridorDefinition:
    """Return a deliberately simple editable starter template for a route."""

    template_name = "Default"
    template = SectionTemplate(
        template_name,
        (
            SectionPoint("Left edge", 8.0, elevation=0.0, feature="edge", layer="Pavement"),
            SectionPoint("Datum", 0.0, elevation=0.0, feature="datum", layer="Reference"),
            SectionPoint("Right edge", -8.0, elevation=0.0, feature="edge", layer="Pavement"),
        ),
        "Starter template. Replace these points in the corridor editor.",
    )
    return CorridorDefinition(
        name=name or "{} corridor".format(route.name),
        sample_interval=10.0,
        templates={template_name: template},
        ranges=(TemplateRange(route.station_start, route.station_end, template_name),),
    )


def default_block_definition(route: RouteModel, name: Optional[str] = None) -> CorridorDefinition:
    """Return a visual block starter template for the block editor."""

    template_name = "板块模板"
    blocks = (
        SectionBlock("左侧土路肩", "土路肩", 0.75, 0.0, feature="shoulder"),
        SectionBlock("左侧硬路肩", "硬路肩", 2.5, 0.0, feature="shoulder"),
        SectionBlock("左侧机动车道", "机动车道", 12.5, 0.0, feature="lane"),
        SectionBlock("中央分隔带", "分隔带", 3.0, 0.0, feature="median", layer="Median"),
        SectionBlock("右侧机动车道", "机动车道", 12.5, 0.0, feature="lane"),
        SectionBlock("右侧硬路肩", "硬路肩", 2.5, 0.0, feature="shoulder"),
        SectionBlock("右侧土路肩", "土路肩", 0.75, 0.0, feature="shoulder"),
    )
    start_offset = 17.25
    start_elevation = 0.0
    template = SectionTemplate(
        template_name,
        points_from_blocks(blocks, start_offset, start_elevation),
        "可视化板块起步模板，可在板块编辑器中修改。",
        blocks,
        start_offset,
        start_elevation,
    )
    return CorridorDefinition(
        name=name or "{} 走廊".format(route.name),
        sample_interval=10.0,
        templates={template_name: template},
        ranges=(TemplateRange(route.station_start, route.station_end, template_name),),
    )


def _validate_template(template: SectionTemplate) -> None:
    if not template.name.strip():
        raise CorridorError("Template name cannot be empty.")
    if template.blocks:
        _validate_blocks(template)
    points = _template_points(template)
    if len(points) < 2:
        raise CorridorError("Template {} needs at least two points.".format(template.name))
    seen_names = set()
    for index, point in enumerate(points):
        if not point.name.strip():
            raise CorridorError("Template {} has an unnamed point.".format(template.name))
        if point.name in seen_names:
            raise CorridorError("Template {} repeats point name {}.".format(template.name, point.name))
        seen_names.add(point.name)
        if not math.isfinite(point.offset):
            raise CorridorError("Template {} has a non-finite point offset.".format(template.name))
        if point.elevation is not None and point.cross_slope is not None:
            raise CorridorError(
                "Template {} point {} cannot define elevation and cross_slope together.".format(
                    template.name, point.name
                )
            )
        if point.elevation is not None and not math.isfinite(point.elevation):
            raise CorridorError("Template {} has a non-finite point elevation.".format(template.name))
        if point.cross_slope is not None and not math.isfinite(point.cross_slope):
            raise CorridorError("Template {} has a non-finite point cross_slope.".format(template.name))
        if index == 0 and point.elevation is None:
            raise CorridorError(
                "Template {} first point {} needs an elevation.".format(template.name, point.name)
            )
        if index > 0 and point.elevation is None and point.cross_slope is None:
            raise CorridorError(
                "Template {} point {} needs elevation or cross_slope.".format(template.name, point.name)
            )


def _validate_blocks(template: SectionTemplate) -> None:
    if not math.isfinite(template.block_start_offset) or not math.isfinite(template.block_start_elevation):
        raise CorridorError("Template {} has a non-finite block datum.".format(template.name))
    names = set()
    for block in template.blocks:
        if not block.name.strip():
            raise CorridorError("Template {} has an unnamed block.".format(template.name))
        if block.name in names:
            raise CorridorError("Template {} repeats block name {}.".format(template.name, block.name))
        names.add(block.name)
        if not block.block_type.strip() or not block.feature.strip() or not block.layer.strip():
            raise CorridorError("Template {} block {} has an empty category.".format(template.name, block.name))
        if not math.isfinite(block.width) or block.width < 0.0:
            raise CorridorError("Template {} block {} has an invalid width.".format(template.name, block.name))
        if (
            not math.isfinite(block.cross_slope)
            or not math.isfinite(block.relative_elevation)
        ):
            raise CorridorError("Template {} block {} has an invalid grade.".format(template.name, block.name))


def _point_elevations(points: Sequence[SectionPoint]) -> List[float]:
    elevations: List[float] = []
    previous_point: Optional[SectionPoint] = None
    for point in points:
        if point.elevation is not None:
            elevation = point.elevation
        else:
            assert previous_point is not None and point.cross_slope is not None
            elevation = elevations[-1] + point.cross_slope * (point.offset - previous_point.offset)
        elevations.append(elevation)
        previous_point = point
    return elevations


def _stations_for_range(section_range: TemplateRange, default_interval: float) -> Iterable[float]:
    interval = section_range.sample_interval or default_interval
    station = section_range.station_start
    yield station
    station += interval
    while station < section_range.station_end - EPSILON:
        yield station
        station += interval
    if section_range.station_end > section_range.station_start + EPSILON:
        yield section_range.station_end


def _breaklines_for_range(
    sections: Sequence[EvaluatedSection], range_index: int
) -> List[BreaklineRun]:
    vertices_by_key: Dict[Tuple[str, str, str], List[EvaluatedVertex]] = {}
    for section in sections:
        for vertex in section.vertices:
            vertices_by_key.setdefault(vertex.key, []).append(vertex)
    return [
        BreaklineRun(key[0], key[1], key[2], range_index, tuple(vertices))
        for key, vertices in vertices_by_key.items()
        if len(vertices) >= 2
    ]


def _point_from_dict(value: object, template_name: str) -> SectionPoint:
    if not isinstance(value, Mapping):
        raise CorridorError("Template {} contains a non-object point.".format(template_name))
    elevation = _optional_number(value.get("elevation"), "elevation")
    cross_slope = _optional_number(value.get("cross_slope"), "cross_slope")
    return SectionPoint(
        name=_required_string(value, "name"),
        offset=_number(value.get("offset"), "offset"),
        elevation=elevation,
        cross_slope=cross_slope,
        feature=str(value.get("feature", "surface")),
        layer=str(value.get("layer", "Pavement")),
    )


def _block_from_dict(value: object, template_name: str) -> SectionBlock:
    if not isinstance(value, Mapping):
        raise CorridorError("Template {} contains a non-object block.".format(template_name))
    return SectionBlock(
        name=_required_string(value, "name"),
        block_type=_required_string(value, "block_type"),
        width=_number(value.get("width"), "width"),
        cross_slope=_optional_number(value.get("cross_slope"), "cross_slope") or 0.0,
        relative_elevation=(
            _optional_number(value.get("relative_elevation"), "relative_elevation")
            if value.get("relative_elevation") is not None
            else _optional_number(value.get("left_curb_height"), "left_curb_height")
        )
        or 0.0,
        feature=str(value.get("feature", "surface")),
        layer=str(value.get("layer", "Pavement")),
    )


def _range_from_dict(value: object) -> TemplateRange:
    if not isinstance(value, Mapping):
        raise CorridorError("Each range must be an object.")
    return TemplateRange(
        station_start=_number(value.get("station_start"), "station_start"),
        station_end=_number(value.get("station_end"), "station_end"),
        template=_required_string(value, "template"),
        alignment_offset=_optional_number(value.get("alignment_offset"), "alignment_offset") or 0.0,
        elevation_offset=_optional_number(value.get("elevation_offset"), "elevation_offset") or 0.0,
        sample_interval=_optional_number(value.get("sample_interval"), "sample_interval"),
    )


def _required_string(value: Mapping[str, object], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise CorridorError("{} must be a non-empty string.".format(key))
    return result


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CorridorError("{} must be a number.".format(name))
    result = float(value)
    if not math.isfinite(result):
        raise CorridorError("{} must be finite.".format(name))
    return result


def _optional_number(value: object, name: str) -> Optional[float]:
    if value is None:
        return None
    return _number(value, name)

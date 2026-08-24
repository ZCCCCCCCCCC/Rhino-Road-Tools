"""Read LandXML 1.2 road alignments as station-evaluated 3D centre lines.

The parsing and sampling types are shared with ``eicad_alignment``.  This
module deliberately has no Rhino dependency so it can be validated from a
regular CPython interpreter before importing into a Rhino document.

LandXML coordinate text is conventionally ordered northing, easting.  Points
are normalised here to X=easting, Y=northing, which is Rhino's usual map
coordinate orientation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple
import ctypes
import math
import sys
import xml.etree.ElementTree as ElementTree


MODULE_DIRECTORY = Path(__file__).resolve().parent
EICAD_IMPORT_DIRECTORY = MODULE_DIRECTORY.parent / "eicad_import"
if str(EICAD_IMPORT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(EICAD_IMPORT_DIRECTORY))

from eicad_alignment import (
    EPSILON,
    HorizontalAlignment,
    HorizontalSegment,
    Point2D,
    RouteModel,
    VerticalAlignment,
    VerticalCurve,
    VerticalPVI,
    _integrate_clothoid,
)


class LandXMLParseError(ValueError):
    """Raised when a LandXML alignment is incomplete or inconsistent."""


@dataclass(frozen=True)
class LandXMLAlignmentInfo:
    """A lightweight entry shown by the Rhino alignment selection dialog."""

    index: int
    name: str
    station_start: float
    length: float
    geometry_count: int
    has_profile: bool

    @property
    def station_end(self) -> float:
        return self.station_start + self.length

    @property
    def display_label(self) -> str:
        profile_label = "3D" if self.has_profile else "2D / Z=0"
        return "{0}  [K{1:.3f}-K{2:.3f}; {3}; {4} elements]".format(
            self.name,
            self.station_start,
            self.station_end,
            profile_label,
            self.geometry_count,
        )


def list_alignments(path: Path | str) -> List[LandXMLAlignmentInfo]:
    """Return all usable ``Alignment`` records in a LandXML document."""

    root = _load_root(path)
    result = []
    for index, element in enumerate(_descendants_named(root, "Alignment")):
        geometry = _first_child(element, "CoordGeom")
        elements = _geometry_elements(geometry) if geometry is not None else []
        if not elements:
            continue
        name = element.get("name") or "Alignment {0}".format(index + 1)
        station_start = _float_attribute(element, "staStart", 0.0)
        length = _float_attribute(element, "length", _geometry_length(elements))
        result.append(
            LandXMLAlignmentInfo(
                index=index,
                name=name,
                station_start=station_start,
                length=length,
                geometry_count=len(elements),
                has_profile=_first_descendant(element, "ProfAlign") is not None,
            )
        )
    if not result:
        raise LandXMLParseError("No Alignment/CoordGeom data was found in {}.".format(path))
    return result


def sort_alignment_infos(
    infos: Sequence[LandXMLAlignmentInfo],
) -> List[LandXMLAlignmentInfo]:
    """Sort alignment names alphabetically and Chinese names by pinyin.

    Rhino 8 runs on Windows, where the built-in ``zh-CN`` sort key provides
    pinyin collation without requiring a Python package in Rhino's embedded
    interpreter.  Other platforms retain a stable case-insensitive fallback.
    """

    return sorted(
        infos,
        key=lambda info: (_name_sort_key(info.name), info.name.casefold(), info.index),
    )


def load_alignments(path: Path | str) -> List[RouteModel]:
    """Load every horizontal alignment in a LandXML document.

    The returned route order and indexes match :func:`list_alignments`, so a
    dialog selection can be resolved without depending on alignment names
    being unique.
    """

    source_path = Path(path)
    root = _load_root(source_path)
    routes = []
    for index, element in enumerate(_descendants_named(root, "Alignment")):
        geometry = _first_child(element, "CoordGeom")
        geometry_elements = _geometry_elements(geometry) if geometry is not None else []
        if not geometry_elements:
            continue
        routes.append(_parse_alignment(element, source_path, index))
    if not routes:
        raise LandXMLParseError("No Alignment/CoordGeom data was found in {}.".format(path))
    return routes


def load_alignment(path: Path | str, alignment_index: int) -> RouteModel:
    """Load one alignment by its zero-based document index."""

    source_path = Path(path)
    root = _load_root(source_path)
    alignments = list(_descendants_named(root, "Alignment"))
    if alignment_index < 0 or alignment_index >= len(alignments):
        raise LandXMLParseError(
            "Alignment index {} is outside the document range.".format(alignment_index)
        )
    return _parse_alignment(alignments[alignment_index], source_path, alignment_index)


def route_report(route: RouteModel) -> str:
    """Return a compact human-readable report for a parsed LandXML route."""

    start = route.point_at(route.station_start)
    end = route.point_at(route.station_end)
    return (
        "{name}: source=LandXML; station={s0:.3f}..{s1:.3f}; "
        "elements={count}; start=({x0:.3f},{y0:.3f},{z0:.3f}); "
        "end=({x1:.3f},{y1:.3f},{z1:.3f})"
    ).format(
        name=route.name,
        s0=route.station_start,
        s1=route.station_end,
        count=len(route.horizontal.segments),
        x0=start.x,
        y0=start.y,
        z0=start.z,
        x1=end.x,
        y1=end.y,
        z1=end.z,
    )


def _parse_alignment(element, source_path: Path, document_index: int) -> RouteModel:
    geometry = _first_child(element, "CoordGeom")
    if geometry is None:
        raise LandXMLParseError("Alignment has no CoordGeom element.")
    elements = _geometry_elements(geometry)
    if not elements:
        raise LandXMLParseError("Alignment has no Line, Curve or Spiral element.")

    station_start = _float_attribute(element, "staStart", 0.0)
    name = element.get("name") or "Alignment {0}".format(document_index + 1)
    horizontal = _parse_horizontal(elements, station_start, name)
    declared_length = _float_attribute(element, "length", horizontal.station_end - station_start)
    station_error = abs(horizontal.station_end - (station_start + declared_length))
    if station_error > 0.01:
        raise LandXMLParseError(
            "Alignment {!r} geometry length differs from its declared length by {:.6f} m.".format(
                name, station_error
            )
        )

    profile = _first_descendant(element, "ProfAlign")
    vertical = _parse_vertical(profile, name) if profile is not None else None
    route = RouteModel(horizontal=horizontal, vertical=vertical, name=name)
    # Keep the original document identity available to callers without making
    # Rhino depend on a custom RouteModel subclass.
    route.landxml_source_path = str(source_path.resolve())
    route.landxml_alignment_index = document_index
    return route


def _parse_horizontal(elements: Sequence[object], station_start: float, route_name: str):
    segments = []
    current_station = station_start
    for element_index, element in enumerate(elements, start=1):
        kind = _local_name(element.tag)
        start = _point_child(element, "Start")
        end = _point_child(element, "End")
        length = _float_attribute(element, "length", start.distance_to(end))
        if length <= EPSILON:
            raise LandXMLParseError(
                "Alignment {!r} {} element {} has non-positive length.".format(
                    route_name, kind, element_index
                )
            )
        if kind == "Line":
            heading = _heading(start, end)
            segment = HorizontalSegment(
                code=1,
                station_start=current_station,
                length=length,
                start=start,
                heading_start=heading,
                curvature_start=0.0,
                curvature_end=0.0,
                description="LandXML line",
            )
        elif kind == "Curve":
            segment = _curve_segment(element, current_station, start, end, length, route_name)
        elif kind == "Spiral":
            segment = _spiral_segment(element, current_station, start, end, length, route_name)
        else:
            raise LandXMLParseError(
                "Alignment {!r} contains unsupported CoordGeom element {}.".format(route_name, kind)
            )
        _check_endpoint(segment, end, route_name, kind, element_index)
        segments.append(segment)
        current_station = segment.station_end

    return HorizontalAlignment(
        station_start=station_start,
        start=segments[0].start,
        heading_start=segments[0].heading_start,
        segments=segments,
        source="LandXML",
    )


def _curve_segment(element, station_start: float, start: Point2D, end: Point2D, length: float, route_name: str):
    center = _point_child(element, "Center")
    radius = _float_attribute(element, "radius", start.distance_to(center))
    if radius <= EPSILON:
        raise LandXMLParseError("Alignment {!r} has a curve with zero radius.".format(route_name))
    rotation = _rotation_sign(element.get("rot"), route_name)
    radial_x = start.x - center.x
    radial_y = start.y - center.y
    heading = math.atan2(rotation * radial_x, -rotation * radial_y)
    curvature = rotation / radius
    return HorizontalSegment(
        code=2,
        station_start=station_start,
        length=length,
        start=start,
        heading_start=heading,
        curvature_start=curvature,
        curvature_end=curvature,
        description="LandXML circular arc",
    )


def _spiral_segment(element, station_start: float, start: Point2D, end: Point2D, length: float, route_name: str):
    radius_start = _radius_attribute(element, "radiusStart")
    radius_end = _radius_attribute(element, "radiusEnd")
    rotation = _rotation_sign(element.get("rot"), route_name)
    curvature_start = rotation / radius_start if radius_start is not None else 0.0
    curvature_end = rotation / radius_end if radius_end is not None else 0.0
    local_x, local_y = _integrate_clothoid(
        0.0,
        curvature_start,
        (curvature_end - curvature_start) / length,
        length,
    )
    if math.hypot(local_x, local_y) <= EPSILON:
        raise LandXMLParseError("Alignment {!r} has a degenerate spiral.".format(route_name))
    heading = _heading(start, end) - math.atan2(local_y, local_x)
    return HorizontalSegment(
        code=3 if abs(curvature_start) <= EPSILON else 4,
        station_start=station_start,
        length=length,
        start=start,
        heading_start=heading,
        curvature_start=curvature_start,
        curvature_end=curvature_end,
        description="LandXML clothoid spiral",
    )


def _parse_vertical(profile, route_name: str) -> Optional[VerticalAlignment]:
    records = []
    for element in list(profile):
        kind = _local_name(element.tag)
        if kind not in ("PVI", "CircCurve"):
            continue
        values = _numbers(element.text)
        if len(values) < 2:
            raise LandXMLParseError(
                "Alignment {!r} has a {} vertical element without station and elevation.".format(
                    route_name, kind
                )
            )
        records.append((kind, values[0], values[1], _float_attribute(element, "length", 0.0)))
    if len(records) < 2:
        return None
    for earlier, later in zip(records, records[1:]):
        if later[1] <= earlier[1] + EPSILON:
            raise LandXMLParseError(
                "Alignment {!r} profile stations must be strictly increasing.".format(route_name)
            )

    pvis = [VerticalPVI(station, elevation) for _, station, elevation, _ in records]
    curves = []
    for index, (kind, station, elevation, length) in enumerate(records):
        if kind != "CircCurve":
            continue
        if index == 0 or index == len(records) - 1 or length <= EPSILON:
            raise LandXMLParseError(
                "Alignment {!r} has a CircCurve without adjacent tangent grades.".format(route_name)
            )
        before = pvis[index - 1]
        current = pvis[index]
        after = pvis[index + 1]
        grade_in = (current.elevation - before.elevation) / (current.station - before.station)
        grade_out = (after.elevation - current.elevation) / (after.station - current.station)
        curves.append(
            VerticalCurve(
                pvi=current,
                grade_in=grade_in,
                grade_out=grade_out,
                station_bvc=current.station - length * 0.5,
                station_evc=current.station + length * 0.5,
            )
        )
    return VerticalAlignment(pvis=pvis, curves=curves)


def _check_endpoint(segment, expected_end: Point2D, route_name: str, kind: str, element_index: int) -> None:
    error = segment.end.distance_to(expected_end)
    if error > 0.01:
        raise LandXMLParseError(
            "Alignment {!r} {} element {} endpoint error is {:.6f} m.".format(
                route_name, kind, element_index, error
            )
        )


def _load_root(path: Path | str):
    path = Path(path)
    if not path.is_file():
        raise LandXMLParseError("LandXML file does not exist: {}.".format(path))
    try:
        return ElementTree.parse(str(path)).getroot()
    except (ElementTree.ParseError, OSError) as error:
        raise LandXMLParseError("Could not read LandXML {}: {}".format(path, error)) from error


def _geometry_elements(geometry) -> List[object]:
    return [element for element in list(geometry) if _local_name(element.tag) in ("Line", "Curve", "Spiral")]


def _geometry_length(elements: Sequence[object]) -> float:
    return sum(_float_attribute(element, "length", 0.0) for element in elements)


def _point_child(element, name: str) -> Point2D:
    child = _first_child(element, name)
    if child is None:
        raise LandXMLParseError("{} element has no {} point.".format(_local_name(element.tag), name))
    values = _numbers(child.text)
    if len(values) < 2:
        raise LandXMLParseError("{} point is not a northing/easting pair.".format(name))
    return Point2D(values[1], values[0])


def _radius_attribute(element, name: str) -> Optional[float]:
    raw_value = element.get(name)
    if raw_value is None or raw_value.strip().upper() in ("INF", "INFINITY"):
        return None
    try:
        value = float(raw_value)
    except ValueError as error:
        raise LandXMLParseError("{} is not a valid {} value.".format(raw_value, name)) from error
    if value <= EPSILON:
        raise LandXMLParseError("{} must be positive.".format(name))
    return value


def _float_attribute(element, name: str, default: float) -> float:
    raw_value = element.get(name)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except ValueError as error:
        raise LandXMLParseError("{} is not a valid {} value.".format(raw_value, name)) from error


def _rotation_sign(rotation: Optional[str], route_name: str) -> float:
    value = (rotation or "").strip().lower()
    if value == "cw":
        return -1.0
    if value == "ccw":
        return 1.0
    raise LandXMLParseError("Alignment {!r} has an unsupported rotation {!r}.".format(route_name, rotation))


def _heading(start: Point2D, end: Point2D) -> float:
    if start.distance_to(end) <= EPSILON:
        raise LandXMLParseError("Geometry element start and end points are coincident.")
    return math.atan2(end.y - start.y, end.x - start.x)


def _numbers(text: Optional[str]) -> List[float]:
    if not text:
        return []
    try:
        return [float(value) for value in text.replace(",", " ").split()]
    except ValueError as error:
        raise LandXMLParseError("Invalid numeric coordinate text {!r}.".format(text.strip())) from error


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first_child(element, name: str):
    for child in list(element):
        if _local_name(child.tag) == name:
            return child
    return None


def _first_descendant(element, name: str):
    for child in element.iter():
        if child is not element and _local_name(child.tag) == name:
            return child
    return None


def _descendants_named(element, name: str):
    for child in element.iter():
        if child is not element and _local_name(child.tag) == name:
            yield child


def _name_sort_key(value: str):
    if sys.platform != "win32":
        return value.casefold()
    try:
        return _windows_zh_cn_sort_key(value.casefold())
    except (AttributeError, OSError):
        return value.casefold()


def _windows_zh_cn_sort_key(value: str) -> bytes:
    """Return the Windows zh-CN linguistic sort key for one route name."""

    lcmapping_sortkey = 0x00000400
    api = ctypes.windll.kernel32.LCMapStringEx
    api.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_ulong,
        ctypes.c_wchar_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]
    api.restype = ctypes.c_int
    size = api("zh-CN", lcmapping_sortkey, value, len(value), None, 0, None, None, 0)
    if not size:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_string_buffer(size)
    written = api("zh-CN", lcmapping_sortkey, value, len(value), buffer, size, None, None, 0)
    if not written:
        raise ctypes.WinError(ctypes.get_last_error())
    return buffer.raw

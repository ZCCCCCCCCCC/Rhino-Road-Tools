"""Read EICAD alignment files and evaluate a 3D road centreline by station.

The module intentionally contains no Rhino imports.  It can therefore be
validated with normal CPython before a route is added to a Rhino document.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple
import math
import re


EPSILON = 1.0e-9
INFINITE_RADIUS = 1.0e9

# 16-point Gauss-Legendre integration on [-1, 1].  It is used only for
# clothoids, where heading is quadratic in station and circular arc formulae
# are not applicable.
GAUSS_NODES = (
    -0.9894009349916499,
    -0.9445750230732326,
    -0.8656312023878318,
    -0.7554044083550030,
    -0.6178762444026438,
    -0.4580167776572274,
    -0.2816035507792589,
    -0.0950125098376374,
    0.0950125098376374,
    0.2816035507792589,
    0.4580167776572274,
    0.6178762444026438,
    0.7554044083550030,
    0.8656312023878318,
    0.9445750230732326,
    0.9894009349916499,
)
GAUSS_WEIGHTS = (
    0.0271524594117541,
    0.0622535239386479,
    0.0951585116824928,
    0.1246289712555339,
    0.1495959888165767,
    0.1691565193950025,
    0.1826034150449236,
    0.1894506104550685,
    0.1894506104550685,
    0.1826034150449236,
    0.1691565193950025,
    0.1495959888165767,
    0.1246289712555339,
    0.0951585116824928,
    0.0622535239386479,
    0.0271524594117541,
)


class EICADParseError(ValueError):
    """Raised when an EICAD file is incomplete or internally inconsistent."""


@dataclass(frozen=True)
class Point2D:
    x: float
    y: float

    def distance_to(self, other: "Point2D") -> float:
        return math.hypot(other.x - self.x, other.y - self.y)

    def translated(self, heading: float, distance: float) -> "Point2D":
        return Point2D(
            self.x + math.cos(heading) * distance,
            self.y + math.sin(heading) * distance,
        )


@dataclass(frozen=True)
class StationRecord:
    station: float
    label: str = ""


@dataclass(frozen=True)
class RoutePoint:
    station: float
    x: float
    y: float
    z: float


@dataclass
class HorizontalSegment:
    """A constant or linearly varying curvature segment."""

    code: int
    station_start: float
    length: float
    start: Point2D
    heading_start: float
    curvature_start: float
    curvature_end: float
    description: str = ""

    @property
    def station_end(self) -> float:
        return self.station_start + self.length

    @property
    def curvature_rate(self) -> float:
        if self.length <= EPSILON:
            return 0.0
        return (self.curvature_end - self.curvature_start) / self.length

    def _checked_local_station(self, local_station: float) -> float:
        if local_station < -EPSILON or local_station > self.length + EPSILON:
            raise ValueError("Station is outside the horizontal segment.")
        return min(self.length, max(0.0, local_station))

    def heading_at(self, local_station: float) -> float:
        local_station = self._checked_local_station(local_station)
        return (
            self.heading_start
            + self.curvature_start * local_station
            + 0.5 * self.curvature_rate * local_station * local_station
        )

    def point_at(self, local_station: float) -> Point2D:
        local_station = self._checked_local_station(local_station)
        if local_station <= EPSILON:
            return self.start

        curvature_rate = self.curvature_rate
        if abs(curvature_rate) <= EPSILON:
            if abs(self.curvature_start) <= EPSILON:
                return self.start.translated(self.heading_start, local_station)
            return _circular_point(
                self.start,
                self.heading_start,
                self.curvature_start,
                local_station,
            )

        dx, dy = _integrate_clothoid(
            self.heading_start,
            self.curvature_start,
            curvature_rate,
            local_station,
        )
        return Point2D(self.start.x + dx, self.start.y + dy)

    @property
    def end(self) -> Point2D:
        return self.point_at(self.length)

    @property
    def heading_end(self) -> float:
        return self.heading_at(self.length)


@dataclass
class HorizontalAlignment:
    station_start: float
    start: Point2D
    heading_start: float
    segments: List[HorizontalSegment] = field(default_factory=list)
    source: str = "ICD"

    @property
    def station_end(self) -> float:
        if not self.segments:
            return self.station_start
        return self.segments[-1].station_end

    @property
    def end(self) -> Point2D:
        if not self.segments:
            return self.start
        return self.segments[-1].end

    @property
    def _segment_starts(self) -> List[float]:
        return [segment.station_start for segment in self.segments]

    def segment_at(self, station: float) -> HorizontalSegment:
        if not self.segments:
            raise ValueError("The horizontal alignment has no elements.")
        if station < self.station_start - EPSILON or station > self.station_end + EPSILON:
            raise ValueError(
                "Station {:.6f} is outside {:.6f}..{:.6f}.".format(
                    station, self.station_start, self.station_end
                )
            )
        index = bisect_right(self._segment_starts, station + EPSILON) - 1
        index = min(max(index, 0), len(self.segments) - 1)
        return self.segments[index]

    def point_at(self, station: float) -> Point2D:
        segment = self.segment_at(station)
        return segment.point_at(station - segment.station_start)

    def heading_at(self, station: float) -> float:
        segment = self.segment_at(station)
        return segment.heading_at(station - segment.station_start)

    def curvature_at(self, station: float) -> float:
        segment = self.segment_at(station)
        local_station = station - segment.station_start
        return segment.curvature_start + segment.curvature_rate * local_station

    def boundary_stations(self) -> List[float]:
        values = [self.station_start]
        values.extend(segment.station_end for segment in self.segments)
        return values

    @classmethod
    def from_icd(cls, path: Path | str) -> "HorizontalAlignment":
        path = Path(path)
        lines = [
            line
            for line in _nonempty_lines(_read_text(path))
            if not line.lstrip().startswith("//")
        ]
        if len(lines) < 4:
            raise EICADParseError("ICD file has no start point and elements: {}".format(path))

        station_start = _numbers(lines[0])[0]
        start_values = _numbers(lines[1])
        if len(start_values) < 3:
            raise EICADParseError("ICD start point must contain X, Y and heading.")

        alignment = cls(
            station_start=station_start,
            start=Point2D(start_values[0], start_values[1]),
            heading_start=start_values[2],
            source="ICD",
        )

        current_station = station_start
        current_point = alignment.start
        current_heading = alignment.heading_start
        previous_curvature = 0.0

        for line_number, line in enumerate(lines[2:], start=3):
            values = _numbers(line)
            if not values:
                continue
            code = int(values[0])
            if code == 0:
                break

            length, curvature_start, curvature_end, description = _icd_geometry(code, values)
            if length <= EPSILON:
                raise EICADParseError(
                    "ICD line {} has a non-positive element length.".format(line_number)
                )
            # EICAD permits direct tangent-to-circle and circle-to-circle
            # connections when no transition curve is specified.  These are
            # G1-continuous but intentionally have a curvature jump.

            # Some EICAD pedestrian/slow-traffic centreline exports use a
            # polyline form: "1, length, heading".  Its heading is a deliberate
            # corner at a zero-curvature element boundary.
            if code == 1 and len(values) >= 3:
                current_heading = values[2]

            segment = HorizontalSegment(
                code=code,
                station_start=current_station,
                length=length,
                start=current_point,
                heading_start=current_heading,
                curvature_start=curvature_start,
                curvature_end=curvature_end,
                description=description,
            )
            alignment.segments.append(segment)
            current_station = segment.station_end
            current_point = segment.end
            current_heading = segment.heading_end
            previous_curvature = curvature_end

        if not alignment.segments:
            raise EICADParseError("ICD file does not contain a route element: {}".format(path))
        return alignment

    @classmethod
    def from_jd(cls, path: Path | str) -> "HorizontalAlignment":
        """Build a horizontal alignment from EICAD's JD representation.

        A JD record stores its inbound radius/transition, its main circular
        radius, and the outbound transition toward the next record's inbound
        radius.  This permits direct reconstruction of ordinary, compound and
        reverse curve chains without relying on an ICD companion file.
        """

        records = _parse_jd_records(Path(path))
        if not records.pis:
            raise EICADParseError("JD file contains no route control record.")

        groups = _build_jd_groups(records)
        alignment = cls(
            station_start=records.station_start,
            start=records.start,
            heading_start=groups[0].heading_start,
            source="JD",
        )
        current_station = records.station_start
        current_point = records.start
        previous_curvature = 0.0

        for index, group in enumerate(groups):
            connector_length = current_point.distance_to(group.start)
            if connector_length > 0.001:
                # EICAD may connect a tangent directly to a circular or
                # transition element.  It is position/tangent continuous,
                # even though the curvature intentionally jumps.
                connector = HorizontalSegment(
                    code=1,
                    station_start=current_station,
                    length=connector_length,
                    start=current_point,
                    heading_start=_heading(current_point, group.start),
                    curvature_start=0.0,
                    curvature_end=0.0,
                    description="tangent from JD",
                )
                alignment.segments.append(connector)
                current_station = connector.station_end
                current_point = connector.end

            if _jd_is_unrounded_pi(group.record):
                # EICAD writes a zero main radius for an unrounded PI.  The
                # connector above already reaches the vertex; the following
                # connector begins the new tangent from the same point.
                previous_curvature = 0.0
                continue

            curve_segments = _jd_curve_segments(
                start=current_point,
                heading_start=group.heading_start,
                station_start=current_station,
                curvature_start=group.curvature_start,
                curvature_main=group.curvature_main,
                curvature_end=group.curvature_end,
                transition_in=group.transition_in,
                arc_length=group.arc_length,
                transition_out=group.transition_out,
            )
            alignment.segments.extend(curve_segments)
            current_station = curve_segments[-1].station_end
            current_point = curve_segments[-1].end
            previous_curvature = group.curvature_end

        final_length = current_point.distance_to(records.end)
        if final_length > 1.0e-6:
            final_line = HorizontalSegment(
                code=1,
                station_start=current_station,
                length=final_length,
                start=current_point,
                heading_start=_heading(current_point, records.end),
                curvature_start=0.0,
                curvature_end=0.0,
                description="final tangent from JD",
            )
            alignment.segments.append(final_line)
        if not alignment.segments:
            raise EICADParseError("JD file does not describe a route element.")
        return alignment


@dataclass(frozen=True)
class VerticalPVI:
    station: float
    elevation: float
    radius: Optional[float] = None


@dataclass(frozen=True)
class VerticalCurve:
    pvi: VerticalPVI
    grade_in: float
    grade_out: float
    station_bvc: float
    station_evc: float

    @property
    def length(self) -> float:
        return self.station_evc - self.station_bvc

    def elevation_at(self, station: float) -> float:
        local_station = station - self.station_bvc
        z_bvc = self.pvi.elevation - self.grade_in * (self.length * 0.5)
        return z_bvc + self.grade_in * local_station + (
            (self.grade_out - self.grade_in) * local_station * local_station / (2.0 * self.length)
        )

    def grade_at(self, station: float) -> float:
        local_station = station - self.station_bvc
        return self.grade_in + (self.grade_out - self.grade_in) * local_station / self.length


@dataclass
class VerticalAlignment:
    pvis: List[VerticalPVI]
    curves: List[VerticalCurve]

    @classmethod
    def from_sqx(cls, path: Path | str) -> "VerticalAlignment":
        path = Path(path)
        pvis: List[VerticalPVI] = []
        for line_number, line in enumerate(_nonempty_lines(_read_text(path)), start=1):
            values = _numbers(line)
            if len(values) < 2:
                raise EICADParseError("SQX line {} needs station and elevation.".format(line_number))
            radius = values[2] if len(values) >= 3 else None
            pvis.append(VerticalPVI(values[0], values[1], radius))

        if len(pvis) < 2:
            raise EICADParseError("SQX needs at least two grade points.")
        if any(later.station <= earlier.station for earlier, later in zip(pvis, pvis[1:])):
            raise EICADParseError("SQX stations must be strictly increasing.")

        grades = [
            (later.elevation - earlier.elevation) / (later.station - earlier.station)
            for earlier, later in zip(pvis, pvis[1:])
        ]
        curves: List[VerticalCurve] = []
        for index in range(1, len(pvis) - 1):
            pvi = pvis[index]
            if pvi.radius is None or abs(pvi.radius) <= EPSILON:
                continue
            grade_in = grades[index - 1]
            grade_out = grades[index]
            length = abs(pvi.radius * (grade_out - grade_in))
            if length <= EPSILON:
                continue
            curve = VerticalCurve(
                pvi=pvi,
                grade_in=grade_in,
                grade_out=grade_out,
                station_bvc=pvi.station - length * 0.5,
                station_evc=pvi.station + length * 0.5,
            )
            curves.append(curve)

        for earlier, later in zip(curves, curves[1:]):
            if later.station_bvc < earlier.station_evc - EPSILON:
                raise EICADParseError("SQX vertical curves overlap.")
        return cls(pvis=pvis, curves=curves)

    def _base_grade_at(self, station: float) -> float:
        if station <= self.pvis[0].station:
            return _grade_between(self.pvis[0], self.pvis[1])
        if station >= self.pvis[-1].station:
            return _grade_between(self.pvis[-2], self.pvis[-1])
        starts = [pvi.station for pvi in self.pvis]
        index = bisect_right(starts, station) - 1
        return _grade_between(self.pvis[index], self.pvis[index + 1])

    def _base_elevation_at(self, station: float) -> float:
        if station <= self.pvis[0].station:
            pvi = self.pvis[0]
            return pvi.elevation + self._base_grade_at(station) * (station - pvi.station)
        if station >= self.pvis[-1].station:
            pvi = self.pvis[-1]
            return pvi.elevation + self._base_grade_at(station) * (station - pvi.station)
        starts = [pvi.station for pvi in self.pvis]
        index = bisect_right(starts, station) - 1
        pvi = self.pvis[index]
        return pvi.elevation + self._base_grade_at(station) * (station - pvi.station)

    def elevation_at(self, station: float) -> float:
        for curve in self.curves:
            if curve.station_bvc - EPSILON <= station <= curve.station_evc + EPSILON:
                return curve.elevation_at(station)
        return self._base_elevation_at(station)

    def grade_at(self, station: float) -> float:
        for curve in self.curves:
            if curve.station_bvc - EPSILON <= station <= curve.station_evc + EPSILON:
                return curve.grade_at(station)
        return self._base_grade_at(station)

    def curvature_at(self, station: float) -> float:
        """Return vertical-plane curvature magnitude at a station."""
        for curve in self.curves:
            if curve.station_bvc - EPSILON <= station <= curve.station_evc + EPSILON:
                return abs(curve.grade_out - curve.grade_in) / curve.length
        return 0.0

    def boundary_stations(self) -> List[float]:
        values = [pvi.station for pvi in self.pvis]
        for curve in self.curves:
            values.extend((curve.station_bvc, curve.station_evc))
        return values


@dataclass
class RouteModel:
    horizontal: HorizontalAlignment
    vertical: Optional[VerticalAlignment] = None
    stations: List[StationRecord] = field(default_factory=list)
    name: str = "route"

    @property
    def station_start(self) -> float:
        return self.horizontal.station_start

    @property
    def station_end(self) -> float:
        return self.horizontal.station_end

    def point_at(self, station: float) -> RoutePoint:
        point = self.horizontal.point_at(station)
        elevation = self.vertical.elevation_at(station) if self.vertical else 0.0
        return RoutePoint(station, point.x, point.y, elevation)

    def required_stations(self) -> List[float]:
        values = self.horizontal.boundary_stations()
        values.extend(record.station for record in self.stations)
        if self.vertical:
            values.extend(self.vertical.boundary_stations())
        return _unique_stations(
            station
            for station in values
            if self.station_start - EPSILON <= station <= self.station_end + EPSILON
        )

    def sample_points(
        self,
        max_step: float = 2.0,
        chord_tolerance: float = 0.002,
    ) -> List[RoutePoint]:
        if max_step <= 0.0 or chord_tolerance <= 0.0:
            raise ValueError("max_step and chord_tolerance must be positive.")

        anchors = _unique_stations(
            list(self.required_stations()) + [self.station_start, self.station_end]
        )
        stations = set(anchors)
        for station_start, station_end in zip(anchors, anchors[1:]):
            midpoint = (station_start + station_end) * 0.5
            segment = self.horizontal.segment_at(midpoint)
            local_start = station_start - segment.station_start
            local_end = station_end - segment.station_start
            horizontal_curvature = max(
                abs(segment.curvature_start + segment.curvature_rate * local_start),
                abs(segment.curvature_start + segment.curvature_rate * local_end),
            )
            vertical_curvature = self.vertical.curvature_at(midpoint) if self.vertical else 0.0
            spatial_curvature = math.hypot(horizontal_curvature, vertical_curvature)
            permitted_step = max_step
            if spatial_curvature > EPSILON:
                permitted_step = min(
                    permitted_step,
                    math.sqrt(8.0 * chord_tolerance / spatial_curvature),
                )
            next_station = station_start + permitted_step
            while next_station < station_end - EPSILON:
                stations.add(next_station)
                next_station += permitted_step
        return [self.point_at(station) for station in sorted(stations)]


@dataclass(frozen=True)
class JDRecord:
    kind: int
    point: Point2D
    end_point: Optional[Point2D]
    radius_in: float
    transition_in: float
    radius_out: float
    transition_out: float


@dataclass(frozen=True)
class JDRecords:
    station_start: float
    start: Point2D
    end: Point2D
    pis: List[JDRecord]


@dataclass(frozen=True)
class JDGroup:
    record: JDRecord
    heading_start: float
    heading_end: float
    curvature_start: float
    curvature_main: float
    curvature_end: float
    transition_in: float
    arc_length: float
    transition_out: float
    start: Point2D


def read_st(path: Path | str) -> List[StationRecord]:
    result: List[StationRecord] = []
    for line in _nonempty_lines(_read_text(Path(path))):
        fields = line.replace(",", " ").split()
        try:
            station = float(fields[0])
        except (IndexError, ValueError):
            continue
        result.append(StationRecord(station, " ".join(fields[1:])))
    return result


def discover_route_files(route_directory: Path | str, recursive: bool = False) -> List[Path]:
    """Return one preferred horizontal source file for each route in a folder.

    ICD is preferred over JD for a same-stem pair because it contains the
    original element sequence.  This also supports EICAD project folders that
    keep several route file sets side by side instead of using one folder per
    route.  Set ``recursive=True`` to include route sets in subdirectories.
    """

    route_directory = Path(route_directory)
    if not route_directory.is_dir():
        raise EICADParseError("Route directory does not exist: {}".format(route_directory))

    routes = {}
    paths = route_directory.rglob("*") if recursive else route_directory.iterdir()
    for path in paths:
        if not path.is_file() or path.suffix.lower() not in (".icd", ".jd"):
            continue
        route_key = (path.parent, path.stem.casefold())
        existing = routes.get(route_key)
        if existing is None or path.suffix.lower() == ".icd":
            routes[route_key] = path
    return sorted(
        routes.values(),
        key=lambda path: str(path.relative_to(route_directory)).casefold(),
    )


def load_route(
    route_directory: Path | str,
    horizontal_source: str = "auto",
) -> RouteModel:
    """Load a same-stem EICAD route directory or a selected ICD/JD file.

    In auto mode ICD is selected before JD because it stores the exact element
    sequence and thus preserves compound curves without inference.
    """

    route_input = Path(route_directory)
    selected_source = None
    if route_input.is_file():
        selected_source = route_input.suffix.lower().lstrip(".")
        if selected_source not in ("icd", "jd"):
            raise EICADParseError("Route file must be an ICD or JD file: {}".format(route_input))
        route_directory = route_input.parent
        name = route_input.stem
    elif route_input.is_dir():
        route_directory = route_input
        name = route_directory.name
        exact_icd = route_directory / (name + ".ICD")
        exact_jd = route_directory / (name + ".JD")
        if not exact_icd.exists() and not exact_jd.exists():
            candidates = discover_route_files(route_directory)
            if len(candidates) == 1:
                route_input = candidates[0]
                selected_source = route_input.suffix.lower().lstrip(".")
                name = route_input.stem
            elif len(candidates) > 1:
                choices = ", ".join(candidate.name for candidate in candidates)
                raise EICADParseError(
                    "Multiple EICAD routes were found; select one ICD/JD file: {}".format(choices)
                )
            else:
                raise EICADParseError("Neither ICD nor JD horizontal data was found.")
    else:
        raise EICADParseError("Route path does not exist: {}".format(route_input))

    source = horizontal_source.lower()
    icd_path = route_directory / (name + ".ICD")
    jd_path = route_directory / (name + ".JD")
    sqx_path = route_directory / (name + ".SQX")
    st_path = route_directory / (name + ".ST")

    if source not in ("auto", "icd", "jd"):
        raise ValueError("horizontal_source must be auto, icd, or jd.")
    if source == "auto" and selected_source:
        source = selected_source
    if source == "icd" or (source == "auto" and icd_path.exists()):
        horizontal = HorizontalAlignment.from_icd(icd_path)
    elif jd_path.exists():
        horizontal = HorizontalAlignment.from_jd(jd_path)
    else:
        raise EICADParseError("Neither ICD nor JD horizontal data was found.")

    vertical = (
        VerticalAlignment.from_sqx(sqx_path)
        if sqx_path.exists() and sqx_path.stat().st_size > 0
        else None
    )
    stations = read_st(st_path) if st_path.exists() else []
    return RouteModel(horizontal=horizontal, vertical=vertical, stations=stations, name=name)


def route_report(route: RouteModel) -> str:
    start = route.point_at(route.station_start)
    end = route.point_at(route.station_end)
    report = (
        "{name}: source={source}; station={s0:.3f}..{s1:.3f}; "
        "elements={count}; start=({x0:.3f},{y0:.3f},{z0:.3f}); "
        "end=({x1:.3f},{y1:.3f},{z1:.3f})"
    ).format(
        name=route.name,
        source=route.horizontal.source,
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
    if route.vertical:
        vertical_start = route.vertical.pvis[0].station
        vertical_end = route.vertical.pvis[-1].station
        if vertical_start > route.station_start + EPSILON or vertical_end < route.station_end - EPSILON:
            report += (
                "; WARNING: SQX station range {:.3f}..{:.3f} does not cover the "
                "full route; elevations outside that range are extrapolated"
            ).format(vertical_start, vertical_end)
    return report


def _icd_geometry(code: int, values: Sequence[float]) -> Tuple[float, float, float, str]:
    if code == 1:
        if len(values) < 2:
            raise EICADParseError("ICD line element needs a length.")
        return values[1], 0.0, 0.0, "line"
    if code == 2:
        if len(values) < 4:
            raise EICADParseError("ICD circular element needs radius, length and turn sign.")
        radius, length, sign = values[1], values[2], values[3]
        return length, sign / radius, sign / radius, "circular arc"
    if code in (3, 4):
        if len(values) < 4:
            raise EICADParseError("ICD spiral needs A, radius and turn sign.")
        a_value, radius, sign = values[1], values[2], values[3]
        if a_value <= 0.0 or radius <= 0.0:
            raise EICADParseError("ICD spiral A and radius must be positive.")
        length = a_value * a_value / radius
        circular_curvature = sign / radius
        if code == 3:
            return length, 0.0, circular_curvature, "entry spiral"
        return length, circular_curvature, 0.0, "exit spiral"
    if code in (5, 6):
        if len(values) < 5:
            raise EICADParseError("ICD compound spiral needs A, two radii and turn sign.")
        a_value, radius_start, radius_end, sign = values[1], values[2], values[3], values[4]
        if a_value <= 0.0 or radius_start <= 0.0 or radius_end <= 0.0:
            raise EICADParseError("ICD compound spiral A and radii must be positive.")
        length = a_value * a_value * abs(1.0 / radius_end - 1.0 / radius_start)
        return (
            length,
            sign / radius_start,
            sign / radius_end,
            "compound spiral {}".format("in" if code == 5 else "out"),
        )
    raise EICADParseError("Unsupported ICD element type {}.".format(code))


def _build_jd_groups(records: JDRecords) -> List[JDGroup]:
    headings_start: List[float] = []
    headings_end: List[float] = []
    for index, record in enumerate(records.pis):
        previous = records.start if index == 0 else _jd_exit_reference(records.pis[index - 1])
        following = records.end if index == len(records.pis) - 1 else records.pis[index + 1].point
        headings_start.append(_heading(previous, record.point))
        if record.kind == 4:
            if record.end_point is None:
                raise EICADParseError("JD type-4 record has no end coordinate.")
            headings_end.append(_heading(record.end_point, following))
        else:
            headings_end.append(_heading(record.point, following))

    provisional_signs = []
    for heading_start, heading_end in zip(headings_start, headings_end):
        turn = _signed_angle(heading_end - heading_start)
        provisional_signs.append(0.0 if abs(turn) <= EPSILON else (1.0 if turn > 0.0 else -1.0))
    groups: List[JDGroup] = []
    for index, record in enumerate(records.pis):
        next_record = records.pis[index + 1] if index + 1 < len(records.pis) else None
        next_sign = provisional_signs[index + 1] if next_record else 0.0
        if record.kind == 4:
            group = _build_type4_jd_group(
                record,
                headings_start[index],
                headings_end[index],
                next_record,
                next_sign,
            )
        else:
            group = _build_type3_jd_group(
                record,
                headings_start[index],
                headings_end[index],
                provisional_signs[index],
                next_record,
                next_sign,
            )
        groups.append(group)
    return groups


def _build_type3_jd_group(
    record: JDRecord,
    heading_start: float,
    heading_end: float,
    sign: float,
    next_record: Optional[JDRecord],
    next_sign: float,
) -> JDGroup:
    if _jd_is_unrounded_pi(record):
        if abs(record.transition_in) > EPSILON or abs(record.transition_out) > EPSILON:
            raise EICADParseError("JD zero-radius PI cannot have transition lengths.")
        return JDGroup(
            record=record,
            heading_start=heading_start,
            heading_end=heading_end,
            curvature_start=0.0,
            curvature_main=0.0,
            curvature_end=0.0,
            transition_in=0.0,
            arc_length=0.0,
            transition_out=0.0,
            start=record.point,
        )

    if abs(sign) <= EPSILON:
        raise EICADParseError("JD control record has a zero tangent intersection angle.")

    turn = _signed_angle(heading_end - heading_start)
    curvature_start, curvature_main, curvature_end = _jd_curvatures(
        record, sign, next_record, next_sign
    )
    arc_length = _jd_arc_length(
        turn,
        curvature_start,
        curvature_main,
        curvature_end,
        record.transition_in,
        record.transition_out,
    )
    local_end = _jd_local_end(
        curvature_start,
        curvature_main,
        curvature_end,
        record.transition_in,
        arc_length,
        record.transition_out,
    )
    sine = math.sin(turn)
    if abs(sine) <= EPSILON:
        raise EICADParseError("JD control record has a zero tangent intersection angle.")
    tangent_out = local_end.y / sine
    tangent_in = local_end.x - tangent_out * math.cos(turn)
    start = record.point.translated(heading_start + math.pi, tangent_in)
    return JDGroup(
        record=record,
        heading_start=heading_start,
        heading_end=heading_end,
        curvature_start=curvature_start,
        curvature_main=curvature_main,
        curvature_end=curvature_end,
        transition_in=record.transition_in,
        arc_length=arc_length,
        transition_out=record.transition_out,
        start=start,
    )


def _build_type4_jd_group(
    record: JDRecord,
    heading_start: float,
    heading_end: float,
    next_record: Optional[JDRecord],
    next_sign: float,
) -> JDGroup:
    if record.end_point is None:
        raise EICADParseError("JD type-4 record has no end coordinate.")

    candidates: List[Tuple[float, JDGroup]] = []
    raw_turn = heading_end - heading_start
    for sign in (-1.0, 1.0):
        curvature_start, curvature_main, curvature_end = _jd_curvatures(
            record, sign, next_record, next_sign
        )
        for wraps in range(-1, 3):
            turn = raw_turn + wraps * 2.0 * math.pi
            try:
                arc_length = _jd_arc_length(
                    turn,
                    curvature_start,
                    curvature_main,
                    curvature_end,
                    record.transition_in,
                    record.transition_out,
                )
            except EICADParseError:
                continue
            local_end = _jd_local_end(
                curvature_start,
                curvature_main,
                curvature_end,
                record.transition_in,
                arc_length,
                record.transition_out,
            )
            calculated_end = _rotate_and_translate(record.point, heading_start, local_end)
            error = calculated_end.distance_to(record.end_point)
            candidates.append(
                (
                    error,
                    JDGroup(
                        record=record,
                        heading_start=heading_start,
                        heading_end=heading_end,
                        curvature_start=curvature_start,
                        curvature_main=curvature_main,
                        curvature_end=curvature_end,
                        transition_in=record.transition_in,
                        arc_length=arc_length,
                        transition_out=record.transition_out,
                        start=record.point,
                    ),
                )
            )
    if not candidates:
        raise EICADParseError("JD type-4 record has no valid curve solution.")
    error, group = min(candidates, key=lambda candidate: candidate[0])
    if error > 0.002:
        raise EICADParseError(
            "JD type-4 curve end mismatch is {:.6f} m.".format(error)
        )
    return group


def _jd_exit_reference(record: JDRecord) -> Point2D:
    return record.end_point if record.kind == 4 and record.end_point else record.point


def _turn_sign(turn: float) -> float:
    if abs(turn) <= EPSILON:
        raise EICADParseError("JD control records have no deflection angle.")
    return 1.0 if turn > 0.0 else -1.0


def _jd_curvatures(
    record: JDRecord,
    sign: float,
    next_record: Optional[JDRecord],
    next_sign: float,
) -> Tuple[float, float, float]:
    if record.radius_out >= INFINITE_RADIUS:
        raise EICADParseError("JD record has no main circular radius.")
    curvature_start = 0.0 if record.radius_in >= INFINITE_RADIUS else sign / record.radius_in
    curvature_main = sign / record.radius_out
    if next_record is None or next_record.radius_in >= INFINITE_RADIUS:
        curvature_end = 0.0
    else:
        curvature_end = next_sign / next_record.radius_in
    return curvature_start, curvature_main, curvature_end


def _jd_is_unrounded_pi(record: JDRecord) -> bool:
    """Return whether EICAD's zero main radius denotes a sharp PI."""

    return abs(record.radius_out) <= EPSILON


def _jd_arc_length(
    turn: float,
    curvature_start: float,
    curvature_main: float,
    curvature_end: float,
    transition_in: float,
    transition_out: float,
) -> float:
    if abs(curvature_main) <= EPSILON:
        raise EICADParseError("JD record has a zero main curvature.")
    transition_turn = (
        0.5 * (curvature_start + curvature_main) * transition_in
        + 0.5 * (curvature_main + curvature_end) * transition_out
    )
    arc_length = (turn - transition_turn) / curvature_main
    if arc_length < -1.0e-6:
        raise EICADParseError("JD transition parameters exceed the available deflection.")
    return max(0.0, arc_length)


def _jd_local_end(
    curvature_start: float,
    curvature_main: float,
    curvature_end: float,
    transition_in: float,
    arc_length: float,
    transition_out: float,
) -> Point2D:
    segments = _jd_curve_segments(
        start=Point2D(0.0, 0.0),
        heading_start=0.0,
        station_start=0.0,
        curvature_start=curvature_start,
        curvature_main=curvature_main,
        curvature_end=curvature_end,
        transition_in=transition_in,
        arc_length=arc_length,
        transition_out=transition_out,
    )
    return segments[-1].end


def _jd_curve_segments(
    start: Point2D,
    heading_start: float,
    station_start: float,
    curvature_start: float,
    curvature_main: float,
    curvature_end: float,
    transition_in: float,
    arc_length: float,
    transition_out: float,
) -> List[HorizontalSegment]:
    segments: List[HorizontalSegment] = []
    current_start = start
    current_heading = heading_start
    current_station = station_start

    def append(code: int, length: float, k0: float, k1: float, description: str) -> None:
        nonlocal current_start, current_heading, current_station
        if length <= EPSILON:
            return
        segment = HorizontalSegment(
            code=code,
            station_start=current_station,
            length=length,
            start=current_start,
            heading_start=current_heading,
            curvature_start=k0,
            curvature_end=k1,
            description=description,
        )
        segments.append(segment)
        current_start = segment.end
        current_heading = segment.heading_end
        current_station = segment.station_end

    append(
        _jd_transition_code(curvature_start, curvature_main),
        transition_in,
        curvature_start,
        curvature_main,
        "inbound spiral from JD",
    )
    append(2, arc_length, curvature_main, curvature_main, "circular arc from JD")
    append(
        _jd_transition_code(curvature_main, curvature_end),
        transition_out,
        curvature_main,
        curvature_end,
        "outbound spiral from JD",
    )
    if not segments:
        raise EICADParseError("JD control record has zero geometry length.")
    return segments


def _jd_transition_code(curvature_start: float, curvature_end: float) -> int:
    if abs(curvature_start) <= EPSILON:
        return 3
    if abs(curvature_end) <= EPSILON:
        return 4
    return 5 if abs(curvature_end) >= abs(curvature_start) else 6


def _rotate_and_translate(origin: Point2D, heading: float, local_point: Point2D) -> Point2D:
    return Point2D(
        origin.x + math.cos(heading) * local_point.x - math.sin(heading) * local_point.y,
        origin.y + math.sin(heading) * local_point.x + math.cos(heading) * local_point.y,
    )


def _parse_jd_records(path: Path) -> JDRecords:
    start: Optional[Point2D] = None
    station_start: Optional[float] = None
    end: Optional[Point2D] = None
    pis: List[JDRecord] = []
    for line_number, line in enumerate(_nonempty_lines(_read_text(path)), start=1):
        values = _numbers(line)
        if not values:
            continue
        kind = int(values[0])
        if kind == -1:
            if len(values) < 4:
                raise EICADParseError("JD start record needs X, Y and station.")
            start = Point2D(values[1], values[2])
            station_start = values[3]
        elif kind == -2:
            if len(values) < 3:
                raise EICADParseError("JD end record needs X and Y.")
            end = Point2D(values[1], values[2])
        elif kind in (3, 4):
            if kind == 4:
                # Type-4 has two coordinate pairs before its radius fields.
                if len(values) < 9:
                    raise EICADParseError("JD type-4 record is incomplete at line {}.".format(line_number))
                offset = 5
                end_point = Point2D(values[3], values[4])
            else:
                if len(values) < 7:
                    raise EICADParseError("JD PI record is incomplete at line {}.".format(line_number))
                offset = 3
                end_point = None
            pis.append(
                JDRecord(
                    kind=kind,
                    point=Point2D(values[1], values[2]),
                    end_point=end_point,
                    radius_in=values[offset],
                    transition_in=values[offset + 1],
                    radius_out=values[offset + 2],
                    transition_out=values[offset + 3],
                )
            )
        else:
            raise EICADParseError("Unsupported JD record type {} at line {}.".format(kind, line_number))
    if start is None or station_start is None or end is None:
        raise EICADParseError("JD file needs -1 start and -2 end records.")
    return JDRecords(station_start, start, end, pis)


def _integrate_clothoid(
    heading_start: float,
    curvature_start: float,
    curvature_rate: float,
    length: float,
) -> Tuple[float, float]:
    half_length = length * 0.5
    midpoint = half_length
    dx = 0.0
    dy = 0.0
    for node, weight in zip(GAUSS_NODES, GAUSS_WEIGHTS):
        station = midpoint + half_length * node
        heading = heading_start + curvature_start * station + 0.5 * curvature_rate * station * station
        dx += weight * math.cos(heading)
        dy += weight * math.sin(heading)
    return dx * half_length, dy * half_length


def _circular_point(start: Point2D, heading: float, curvature: float, length: float) -> Point2D:
    heading_end = heading + curvature * length
    return Point2D(
        start.x + (math.sin(heading_end) - math.sin(heading)) / curvature,
        start.y - (math.cos(heading_end) - math.cos(heading)) / curvature,
    )


def _heading(start: Point2D, end: Point2D) -> float:
    if start.distance_to(end) <= EPSILON:
        raise EICADParseError("Two route control points have the same coordinates.")
    return math.atan2(end.y - start.y, end.x - start.x)


def _signed_angle(angle: float) -> float:
    while angle <= -math.pi:
        angle += 2.0 * math.pi
    while angle > math.pi:
        angle -= 2.0 * math.pi
    return angle


def _grade_between(start: VerticalPVI, end: VerticalPVI) -> float:
    return (end.elevation - start.elevation) / (end.station - start.station)


def _numbers(text: str) -> List[float]:
    return [float(value) for value in re.findall(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[Ee][-+]?\d+)?", text)]


def _nonempty_lines(text: str) -> List[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise EICADParseError("Cannot decode text file: {}".format(path))


def _unique_stations(stations: Iterable[float]) -> List[float]:
    result: List[float] = []
    for station in sorted(stations):
        if not result or abs(station - result[-1]) > EPSILON:
            result.append(station)
    return result

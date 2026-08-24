#! python 3
# -*- coding: utf-8 -*-

"""Rhino 8 entry point for importing an EICAD centreline route.

Run this file from Rhino's ScriptEditor/RunPythonScript, or import
``import_route`` from a RhinoClaw Python 3 execution.  The calculation core
is kept in eicad_alignment.py so it can be tested without a Rhino session.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, Optional, Sequence, Tuple
import importlib
import sys


MODULE_DIRECTORY = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
if str(MODULE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(MODULE_DIRECTORY))

import eicad_alignment

# ScriptEditor keeps imported modules alive between runs. Reload the local
# calculation module so rerunning this entry point always uses the saved code.
importlib.reload(eicad_alignment)
from eicad_alignment import (
    RouteModel,
    StationRecord,
    discover_route_files,
    load_route,
    route_report,
)


STATION_DISPLAY_TOLERANCE = 0.001


def import_route(
    route_directory: str,
    horizontal_source: str = "auto",
    max_step: float = 2.0,
    chord_tolerance: float = 0.002,
    coordinate_scale: float = 1.0,
    swap_xy: bool = False,
    origin_offset: Sequence[float] = (0.0, 0.0, 0.0),
    add_key_station_points: bool = True,
    replace_existing: bool = False,
) -> Dict[str, object]:
    """Create a 3D EICAD centreline in the active Rhino document.

    EICAD coordinates are normally metres.  Set coordinate_scale=1000.0 when
    a millimetre Rhino document is intentionally being used.  ``origin_offset``
    is applied after scaling, and is useful for a local BIM coordinate system.
    ``route_directory`` can be a traditional same-stem route folder or a
    specific ``.icd``/``.jd`` file selected from a multi-route project folder.
    Set ``swap_xy=True`` when EICAD's first coordinate is northing and Rhino's
    X axis is intended to be easting.
    """

    rs = _rhinoscript_syntax()
    route = load_route(route_directory, horizontal_source=horizontal_source)
    if coordinate_scale <= 0.0:
        raise ValueError("coordinate_scale must be positive.")
    if len(origin_offset) != 3:
        raise ValueError("origin_offset must contain X, Y and Z.")

    layer_root = "EICAD::{}".format(route.name)
    centreline_layer = "{}::Centerline".format(layer_root)
    station_layer = "{}::Stations".format(layer_root)
    _ensure_layer(rs, centreline_layer, (30, 150, 90))
    _ensure_layer(rs, station_layer, (230, 140, 40))

    if replace_existing:
        _delete_route_objects(rs, route.name)

    points = route.sample_points(max_step=max_step, chord_tolerance=chord_tolerance)
    rhino_points = [
        _scaled_point(point.x, point.y, point.z, coordinate_scale, swap_xy, origin_offset)
        for point in points
    ]
    centreline_id = rs.AddPolyline(rhino_points)
    if not centreline_id:
        raise RuntimeError("Rhino could not create the route polyline.")
    rs.ObjectLayer(centreline_id, centreline_layer)
    rs.ObjectName(centreline_id, "EICAD_CENTERLINE::{}".format(route.name))
    _set_route_metadata(
        rs,
        centreline_id,
        route,
        {
            "eicad_geometry": "3d_centerline",
            "eicad_sample_count": str(len(points)),
            "eicad_max_step_m": "{:.6f}".format(max_step),
            "eicad_chord_tolerance_m": "{:.6f}".format(chord_tolerance),
            "eicad_coordinate_scale": "{:.12g}".format(coordinate_scale),
            "eicad_swap_xy": str(bool(swap_xy)).lower(),
            "eicad_origin_offset": "{:.12g},{:.12g},{:.12g}".format(*origin_offset),
            "eicad_source_path": str(Path(route_directory).resolve()),
        },
    )

    station_ids = []
    if add_key_station_points:
        for station in _key_station_records(route):
            point = route.point_at(station.station)
            point_id = rs.AddPoint(
                _scaled_point(point.x, point.y, point.z, coordinate_scale, swap_xy, origin_offset)
            )
            if not point_id:
                continue
            rs.ObjectLayer(point_id, station_layer)
            label = station.label or "ST{:.3f}".format(station.station)
            rs.ObjectName(point_id, "EICAD_STATION::{}::{}".format(route.name, label))
            _set_route_metadata(
                rs,
                point_id,
                route,
                {
                    "eicad_geometry": "key_station",
                    "eicad_station": "{:.6f}".format(station.station),
                    "eicad_station_label": label,
                },
            )
            station_ids.append(point_id)

    report = route_report(route)
    print(report)
    print(
        "Rhino import: centreline={}; vertices={}; key_stations={}".format(
            centreline_id, len(rhino_points), len(station_ids)
        )
    )
    return {
        "route": route.name,
        "centreline_id": str(centreline_id),
        "station_ids": [str(identifier) for identifier in station_ids],
        "vertex_count": len(rhino_points),
        "route_report": report,
    }


def _rhinoscript_syntax():
    try:
        import rhinoscriptsyntax as rs
    except ImportError as error:
        raise RuntimeError("This module must run inside Rhino.") from error
    return rs


def _ensure_layer(rs, layer_name: str, color: Tuple[int, int, int]) -> None:
    if not rs.IsLayer(layer_name):
        layer_id = rs.AddLayer(layer_name, color)
        if not layer_id:
            raise RuntimeError("Rhino could not create layer {}.".format(layer_name))


def _delete_route_objects(rs, route_name: str) -> None:
    import Rhino

    settings = Rhino.DocObjects.ObjectEnumeratorSettings()
    settings.ActiveObjects = True
    objects = [
        rhino_object.Id
        for rhino_object in Rhino.RhinoDoc.ActiveDoc.Objects.GetObjectList(settings)
        if rhino_object.Attributes.GetUserString("eicad_route") == route_name
    ]
    if objects:
        rs.DeleteObjects(objects)


def _set_route_metadata(rs, object_id, route: RouteModel, extra: Dict[str, str]) -> None:
    values = {
        "eicad_route": route.name,
        "eicad_horizontal_source": route.horizontal.source,
        "eicad_station_start": "{:.6f}".format(route.station_start),
        "eicad_station_end": "{:.6f}".format(route.station_end),
    }
    values.update(extra)
    for key, value in values.items():
        rs.SetUserText(object_id, key, value)


def _key_station_records(route: RouteModel):
    records = []
    for record in route.stations:
        if not record.label:
            continue
        if record.station < route.station_start - STATION_DISPLAY_TOLERANCE:
            raise ValueError("ST station {:.6f} is before the route start.".format(record.station))
        if record.station > route.station_end + STATION_DISPLAY_TOLERANCE:
            raise ValueError("ST station {:.6f} is after the route end.".format(record.station))
        station = min(route.station_end, max(route.station_start, record.station))
        records.append(StationRecord(station, record.label))
    known = {round(record.station, 6) for record in records}
    for station, label in ((route.station_start, "QD"), (route.station_end, "ZD")):
        if round(station, 6) not in known:
            records.append(StationRecord(station, label))
            known.add(round(station, 6))
    return sorted(records, key=lambda record: record.station)


def _scaled_point(
    x: float,
    y: float,
    z: float,
    scale: float,
    swap_xy: bool,
    origin_offset: Sequence[float],
) -> Tuple[float, float, float]:
    if swap_xy:
        x, y = y, x
    return (
        x * scale + origin_offset[0],
        y * scale + origin_offset[1],
        z * scale + origin_offset[2],
    )


def main() -> None:
    rs = _rhinoscript_syntax()
    route_directory = rs.BrowseForFolder(None, "Select an EICAD route directory")
    if not route_directory:
        return

    candidates = discover_route_files(route_directory)
    if len(candidates) <= 1:
        import_route(str(candidates[0]) if candidates else route_directory)
        return

    labels = [candidate.name for candidate in candidates]
    selected_label = rs.ListBox(labels, "Select an EICAD horizontal route file", "EICAD Route")
    if selected_label:
        selected_index = labels.index(selected_label)
        import_route(str(candidates[selected_index]))


if __name__ == "__main__":
    main()

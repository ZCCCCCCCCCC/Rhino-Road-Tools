"""Rhino 8 functions for importing LandXML alignment centre lines.

Run ``rhino_import_landxml_multi_py3.py`` for the selection dialog, or import
``import_alignment`` when another Rhino script already chose an alignment.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Sequence, Tuple
import importlib
import sys


MODULE_DIRECTORY = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
if str(MODULE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(MODULE_DIRECTORY))

import landxml_alignment

importlib.reload(landxml_alignment)
from landxml_alignment import RouteModel, route_report


def import_alignment(
    route: RouteModel,
    source_path: str,
    coordinate_scale: float = 1.0,
    origin_offset: Sequence[float] = (0.0, 0.0, 0.0),
    max_step: float = 2.0,
    chord_tolerance: float = 0.002,
    add_key_station_points: bool = True,
    replace_existing: bool = False,
) -> Dict[str, object]:
    """Create one 3D LandXML alignment in the active Rhino document."""

    rs = _rhinoscript_syntax()
    if coordinate_scale <= 0.0:
        raise ValueError("coordinate_scale must be positive.")
    if len(origin_offset) != 3:
        raise ValueError("origin_offset must contain X, Y and Z.")

    route_key = _route_key(route)
    layer_root = "LandXML::{}".format(_safe_layer_component(route.name))
    centreline_layer = "{}::Centerline".format(layer_root)
    station_layer = "{}::Stations".format(layer_root)
    _ensure_layer(rs, centreline_layer, (30, 150, 90))
    _ensure_layer(rs, station_layer, (230, 140, 40))
    if replace_existing:
        _delete_route_objects(rs, route_key)

    points = route.sample_points(max_step=max_step, chord_tolerance=chord_tolerance)
    centreline_id = rs.AddPolyline(
        [_scaled_point(point.x, point.y, point.z, coordinate_scale, origin_offset) for point in points]
    )
    if not centreline_id:
        raise RuntimeError("Rhino could not create the LandXML route polyline.")
    rs.ObjectLayer(centreline_id, centreline_layer)
    rs.ObjectName(centreline_id, "LANDXML_CENTERLINE::{}".format(route.name))
    _set_metadata(
        rs,
        centreline_id,
        route,
        route_key,
        {
            "landxml_geometry": "3d_centerline",
            "landxml_sample_count": str(len(points)),
            "landxml_max_step_m": "{:.6f}".format(max_step),
            "landxml_chord_tolerance_m": "{:.6f}".format(chord_tolerance),
            "landxml_coordinate_scale": "{:.12g}".format(coordinate_scale),
            "landxml_origin_offset": "{:.12g},{:.12g},{:.12g}".format(*origin_offset),
            "landxml_source_path": str(Path(source_path).resolve()),
            "landxml_has_profile": str(bool(route.vertical)).lower(),
        },
    )

    station_ids = []
    if add_key_station_points:
        for station, label in _key_stations(route):
            point = route.point_at(station)
            point_id = rs.AddPoint(
                _scaled_point(point.x, point.y, point.z, coordinate_scale, origin_offset)
            )
            if not point_id:
                continue
            rs.ObjectLayer(point_id, station_layer)
            rs.ObjectName(point_id, "LANDXML_STATION::{}::{}".format(route.name, label))
            _set_metadata(
                rs,
                point_id,
                route,
                route_key,
                {
                    "landxml_geometry": "key_station",
                    "landxml_station": "{:.6f}".format(station),
                    "landxml_station_label": label,
                },
            )
            station_ids.append(point_id)

    report = route_report(route)
    print(report)
    print(
        "Rhino import: centreline={}; vertices={}; key_stations={}".format(
            centreline_id, len(points), len(station_ids)
        )
    )
    return {
        "route": route.name,
        "centreline_id": str(centreline_id),
        "station_ids": [str(identifier) for identifier in station_ids],
        "vertex_count": len(points),
        "route_report": report,
    }


def _rhinoscript_syntax():
    try:
        import rhinoscriptsyntax as rs
    except ImportError as error:
        raise RuntimeError("This module must run inside Rhino.") from error
    return rs


def _route_key(route: RouteModel) -> str:
    index = getattr(route, "landxml_alignment_index", 0)
    return "{}::{}".format(index, route.name)


def _safe_layer_component(value: str) -> str:
    return value.replace("::", "_")


def _ensure_layer(rs, layer_name: str, color: Tuple[int, int, int]) -> None:
    if not rs.IsLayer(layer_name):
        layer_id = rs.AddLayer(layer_name, color)
        if not layer_id:
            raise RuntimeError("Rhino could not create layer {}.".format(layer_name))


def _delete_route_objects(rs, route_key: str) -> None:
    import Rhino

    settings = Rhino.DocObjects.ObjectEnumeratorSettings()
    settings.ActiveObjects = True
    objects = [
        rhino_object.Id
        for rhino_object in Rhino.RhinoDoc.ActiveDoc.Objects.GetObjectList(settings)
        if rhino_object.Attributes.GetUserString("landxml_route_key") == route_key
    ]
    if objects:
        rs.DeleteObjects(objects)


def _set_metadata(rs, object_id, route: RouteModel, route_key: str, extra: Dict[str, str]) -> None:
    values = {
        "landxml_route": route.name,
        "landxml_route_key": route_key,
        "landxml_station_start": "{:.6f}".format(route.station_start),
        "landxml_station_end": "{:.6f}".format(route.station_end),
        "landxml_horizontal_source": route.horizontal.source,
    }
    values.update(extra)
    for key, value in values.items():
        rs.SetUserText(object_id, key, value)


def _key_stations(route: RouteModel):
    stations = {round(route.station_start, 6): (route.station_start, "QD")}
    stations[round(route.station_end, 6)] = (route.station_end, "ZD")
    for station in route.horizontal.boundary_stations()[1:-1]:
        stations.setdefault(round(station, 6), (station, "HZ"))
    if route.vertical:
        for station in route.vertical.boundary_stations():
            if route.station_start - 1.0e-6 <= station <= route.station_end + 1.0e-6:
                stations.setdefault(round(station, 6), (station, "VD"))
    return [stations[key] for key in sorted(stations)]


def _scaled_point(
    x: float,
    y: float,
    z: float,
    scale: float,
    origin_offset: Sequence[float],
):
    return (
        x * scale + origin_offset[0],
        y * scale + origin_offset[1],
        z * scale + origin_offset[2],
    )

#! python 3
# -*- coding: utf-8 -*-

"""Build editable 3D road section and breakline geometry in Rhino 8.

This is the Rhino adapter for corridor_model.py.  It intentionally generates
only verified section polylines and longitudinal control lines; later tools can
create pavement and structure surfaces from these objects.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple
import importlib
import sys


MODULE_DIRECTORY = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
if str(MODULE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(MODULE_DIRECTORY))

import corridor_model
import eicad_alignment

# ScriptEditor keeps imported modules alive between executions.
importlib.reload(corridor_model)
importlib.reload(eicad_alignment)
from corridor_model import CoordinateTransform, CorridorDefinition, evaluate_corridor, load_definition
from eicad_alignment import RouteModel, load_route


def build_corridor(
    route_path: str,
    definition_path: str,
    coordinate_scale: float = 1.0,
    swap_xy: bool = True,
    origin_offset: Sequence[float] = (0.0, 0.0, 0.0),
    replace_existing: bool = True,
) -> Dict[str, object]:
    """Create Rhino sections and breaklines from a route and corridor JSON."""

    rs = _rhinoscript_syntax()
    route = load_route(route_path)
    definition = load_definition(definition_path)
    transform = CoordinateTransform(
        coordinate_scale=coordinate_scale,
        swap_xy=swap_xy,
        origin_offset=tuple(origin_offset),
    )
    geometry = evaluate_corridor(route, definition, transform)
    corridor_name = _layer_component(definition.name)
    layer_root = "EICAD::{}::Corridor::{}".format(route.name, corridor_name)
    section_layer = "{}::Sections".format(layer_root)
    breakline_root = "{}::Breaklines".format(layer_root)
    _ensure_layer(rs, section_layer, (95, 130, 220))

    if replace_existing:
        _delete_corridor_objects(rs, route.name, definition.name)

    section_ids = []
    for section in geometry.sections:
        object_id = rs.AddPolyline([(vertex.x, vertex.y, vertex.z) for vertex in section.vertices])
        if not object_id:
            raise RuntimeError("Rhino could not create the section at station {:.3f}.".format(section.station))
        rs.ObjectLayer(object_id, section_layer)
        rs.ObjectName(
            object_id,
            "CORRIDOR_SECTION::{}::{}::ST{:.3f}".format(route.name, definition.name, section.station),
        )
        _set_metadata(
            rs,
            object_id,
            route,
            definition,
            {
                "corridor_geometry": "section",
                "corridor_station": "{:.6f}".format(section.station),
                "corridor_template": section.template,
                "corridor_range_index": str(section.range_index),
                "corridor_definition_path": str(Path(definition_path).resolve()),
            },
        )
        section_ids.append(object_id)

    breakline_ids = []
    for breakline in geometry.breaklines:
        layer_name = "{}::{}".format(breakline_root, _layer_component(breakline.layer))
        _ensure_layer(rs, layer_name, (220, 140, 60))
        object_id = rs.AddPolyline([(vertex.x, vertex.y, vertex.z) for vertex in breakline.vertices])
        if not object_id:
            raise RuntimeError("Rhino could not create breakline {}.".format(breakline.name))
        rs.ObjectLayer(object_id, layer_name)
        rs.ObjectName(
            object_id,
            "CORRIDOR_BREAKLINE::{}::{}::{}".format(route.name, definition.name, breakline.name),
        )
        _set_metadata(
            rs,
            object_id,
            route,
            definition,
            {
                "corridor_geometry": "breakline",
                "corridor_feature": breakline.feature,
                "corridor_point_name": breakline.name,
                "corridor_layer": breakline.layer,
                "corridor_range_index": str(breakline.range_index),
                "corridor_definition_path": str(Path(definition_path).resolve()),
            },
        )
        breakline_ids.append(object_id)

    print(
        "Corridor build: route={}; corridor={}; sections={}; breaklines={}".format(
            route.name, definition.name, len(section_ids), len(breakline_ids)
        )
    )
    return {
        "route": route.name,
        "corridor": definition.name,
        "section_ids": [str(object_id) for object_id in section_ids],
        "breakline_ids": [str(object_id) for object_id in breakline_ids],
        "section_count": len(section_ids),
        "breakline_count": len(breakline_ids),
    }


def main() -> None:
    rs = _rhinoscript_syntax()
    centreline_id = rs.GetObject("Select an imported EICAD centreline", rs.filter.curve, preselect=True)
    if not centreline_id:
        return

    route_path = _route_path_for_centreline(rs, centreline_id)
    if not route_path:
        return

    definition_path = rs.OpenFileName(
        "Select a corridor definition JSON file",
        "Corridor JSON (*.json)|*.json||",
    )
    if not definition_path:
        return

    build_corridor(
        route_path,
        definition_path,
        coordinate_scale=_number_user_text(rs.GetUserText(centreline_id, "eicad_coordinate_scale"), 1.0),
        swap_xy=_bool_user_text(rs.GetUserText(centreline_id, "eicad_swap_xy"), True),
        origin_offset=_origin_user_text(rs.GetUserText(centreline_id, "eicad_origin_offset")),
    )


def _set_metadata(rs, object_id, route: RouteModel, definition: CorridorDefinition, extra: Dict[str, str]) -> None:
    values = {
        "eicad_route": route.name,
        "corridor_name": definition.name,
        "corridor_version": str(definition.version),
    }
    values.update(extra)
    for key, value in values.items():
        rs.SetUserText(object_id, key, value)


def _delete_corridor_objects(rs, route_name: str, corridor_name: str) -> None:
    import Rhino

    settings = Rhino.DocObjects.ObjectEnumeratorSettings()
    settings.ActiveObjects = True
    object_ids = [
        rhino_object.Id
        for rhino_object in Rhino.RhinoDoc.ActiveDoc.Objects.GetObjectList(settings)
        if rhino_object.Attributes.GetUserString("eicad_route") == route_name
        and rhino_object.Attributes.GetUserString("corridor_name") == corridor_name
    ]
    if object_ids:
        rs.DeleteObjects(object_ids)


def _ensure_layer(rs, layer_name: str, color: Tuple[int, int, int]) -> None:
    if not rs.IsLayer(layer_name):
        layer_id = rs.AddLayer(layer_name, color)
        if not layer_id:
            raise RuntimeError("Rhino could not create layer {}.".format(layer_name))


def _layer_component(value: str) -> str:
    result = value.strip().replace("::", "_").replace("\\", "_").replace("/", "_")
    return result or "Unnamed"


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

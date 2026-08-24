"""Regression checks for the parametric road corridor calculation core.

Usage from the repository root:
    py -3.10 eicad_rhino/corridor_modeling/validate_corridor.py
"""

from __future__ import annotations

from pathlib import Path
import math

from corridor_model import (
    CoordinateTransform,
    CorridorDefinition,
    SectionBlock,
    SectionPoint,
    SectionTemplate,
    TemplateRange,
    default_block_definition,
    definition_from_dict,
    definition_to_dict,
    evaluate_corridor,
    points_from_blocks,
)
from eicad_alignment import load_route


def main() -> None:
    root = Path(__file__).resolve().parents[2] / "2026.1.20机场北线立交发BIM" / "机场北线-A"
    route = load_route(root)
    template = SectionTemplate(
        "Asymmetric median",
        (
            SectionPoint("Left verge", 12.0, elevation=0.12, feature="verge", layer="Surface"),
            SectionPoint("Median left", 4.0, cross_slope=0.02, feature="median", layer="Median"),
            SectionPoint("Datum", 0.0, elevation=0.0, feature="datum", layer="Reference"),
            SectionPoint("Right lane", -7.5, cross_slope=0.02, feature="lane", layer="Surface"),
            SectionPoint("Right edge", -10.5, cross_slope=0.03, feature="edge", layer="Surface"),
        ),
    )
    definition = CorridorDefinition(
        "Regression corridor",
        25.0,
        {template.name: template},
        (
            TemplateRange(
                route.station_start,
                route.station_start + 100.0,
                template.name,
                alignment_offset=2.5,
                elevation_offset=0.3,
            ),
        ),
    )

    # The JSON representation must retain all editable section semantics.
    round_trip = definition_from_dict(definition_to_dict(definition))
    geometry = evaluate_corridor(route, round_trip, CoordinateTransform(swap_xy=True))
    assert len(geometry.sections) == 5
    assert len(geometry.breaklines) == 5

    first = geometry.sections[0]
    by_name = {vertex.name: vertex for vertex in first.vertices}
    assert abs(by_name["Median left"].elevation_offset - 0.26) < 1.0e-9
    assert abs(by_name["Right edge"].elevation_offset - 0.06) < 1.0e-9

    # After a Y/X mapping, positive offsets must still be left of stationing.
    heading = route.horizontal.heading_at(first.station)
    tangent_x, tangent_y = math.sin(heading), math.cos(heading)
    left_x, left_y = -tangent_y, tangent_x
    datum = by_name["Datum"]
    left = by_name["Left verge"]
    left_dot = (left.x - datum.x) * left_x + (left.y - datum.y) * left_y
    assert left_dot > 0.0

    # A visual block template must regenerate the same control points after a
    # JSON round trip, including relative block elevations and curb steps.
    block_definition = default_block_definition(route)
    block_round_trip = definition_from_dict(definition_to_dict(block_definition))
    block_template = block_round_trip.templates["板块模板"]
    assert len(block_template.blocks) == 7
    assert len(block_template.points) == 8
    assert math.isclose(block_template.points[0].offset, 17.25, abs_tol=1.0e-9)
    assert math.isclose(block_template.points[-1].offset, -17.25, abs_tol=1.0e-9)
    assert math.isclose(block_template.points[-1].elevation or 0.0, 0.0, abs_tol=1.0e-9)
    block_geometry = evaluate_corridor(route, block_round_trip, CoordinateTransform(swap_xy=True))
    assert len(block_geometry.breaklines) == 8

    curb_points = points_from_blocks(
        (
            SectionBlock("左侧机动车道", "机动车道", 3.0),
            SectionBlock(
                "中分带",
                "分隔带",
                3.0,
                relative_elevation=0.15,
            ),
            SectionBlock("右侧机动车道", "机动车道", 3.0),
        ),
        start_offset=4.5,
        start_elevation=0.0,
    )
    assert len(curb_points) == 6
    assert math.isclose(curb_points[2].offset, 1.5, abs_tol=1.0e-9)
    assert math.isclose(curb_points[2].elevation or 0.0, 0.15, abs_tol=1.0e-9)
    assert math.isclose(curb_points[4].offset, -1.5, abs_tol=1.0e-9)
    assert math.isclose(curb_points[4].elevation or 0.0, 0.0, abs_tol=1.0e-9)

    legacy_blocks = definition_from_dict(
        {
            "name": "Legacy curb",
            "sample_interval": 10.0,
            "templates": [
                {
                    "name": "Legacy",
                    "block_start_offset": 1.0,
                    "block_start_elevation": 0.0,
                    "blocks": [
                        {
                            "name": "Median",
                            "block_type": "median",
                            "width": 2.0,
                            "cross_slope": 0.0,
                            "left_curb_height": 0.15,
                            "right_curb_height": -0.15,
                            "feature": "median",
                            "layer": "Median",
                        }
                    ],
                }
            ],
            "ranges": [{"station_start": 0.0, "station_end": 10.0, "template": "Legacy"}],
        }
    )
    assert math.isclose(
        legacy_blocks.templates["Legacy"].blocks[0].relative_elevation,
        0.15,
        abs_tol=1.0e-9,
    )

    print(
        "Corridor checks passed: sections={}; breaklines={}; block_points={}; left_offset_projection={:.6f} m".format(
            len(geometry.sections), len(geometry.breaklines), len(block_template.points), left_dot
        )
    )


if __name__ == "__main__":
    main()

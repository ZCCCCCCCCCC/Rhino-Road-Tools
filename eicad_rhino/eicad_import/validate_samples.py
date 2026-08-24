"""Regression checks for the supplied EICAD A-E sample routes.

Usage from this repository root:
    python eicad_rhino/eicad_import/validate_samples.py

An optional first argument can point to a different folder that contains
same-stem EICAD route directories.
"""

from __future__ import annotations

from pathlib import Path
import sys

from eicad_alignment import HorizontalAlignment, _parse_jd_records, load_route


ENDPOINT_TOLERANCE_METRES = 0.001
STATION_TOLERANCE_METRES = 0.001


def validate_route(route_directory: Path) -> str:
    name = route_directory.name
    icd_path = route_directory / (name + ".ICD")
    jd_path = route_directory / (name + ".JD")
    if not icd_path.exists() or not jd_path.exists():
        raise AssertionError("{} needs both ICD and JD test files.".format(name))

    route = load_route(route_directory, horizontal_source="icd")
    jd = _parse_jd_records(jd_path)
    endpoint_error = route.horizontal.end.distance_to(jd.end)
    assert endpoint_error <= ENDPOINT_TOLERANCE_METRES, (
        "{} endpoint error {:.9f} m".format(name, endpoint_error)
    )

    labelled_stations = [record for record in route.stations if record.label]
    for record in labelled_stations:
        boundary_error = min(
            abs(record.station - boundary) for boundary in route.horizontal.boundary_stations()
        )
        assert boundary_error <= STATION_TOLERANCE_METRES, (
            "{} station {} ({}) is not at an ICD boundary".format(
                name, record.station, record.label
            )
        )

    if route.vertical:
        for curve in route.vertical.curves:
            before_bvc = route.vertical.elevation_at(curve.station_bvc - 1.0e-6)
            at_bvc = route.vertical.elevation_at(curve.station_bvc)
            at_evc = route.vertical.elevation_at(curve.station_evc)
            after_evc = route.vertical.elevation_at(curve.station_evc + 1.0e-6)
            assert abs(before_bvc - at_bvc) < 1.0e-7
            assert abs(after_evc - at_evc) < 1.0e-7

    samples = route.sample_points(max_step=2.0, chord_tolerance=0.002)
    assert abs(samples[0].station - route.station_start) <= STATION_TOLERANCE_METRES
    assert abs(samples[-1].station - route.station_end) <= STATION_TOLERANCE_METRES
    assert len(samples) > 2
    max_chord_deviation = max(
        _chord_midpoint_deviation(route, first, second)
        for first, second in zip(samples, samples[1:])
    )
    assert max_chord_deviation <= 0.0020001, (
        "{} chord deviation {:.9f} m".format(name, max_chord_deviation)
    )
    return "{}: endpoint_error={:.9f} m; samples={}; chord_deviation={:.9f} m".format(
        name, endpoint_error, len(samples), max_chord_deviation
    )


def validate_jd(route_directory: Path) -> str:
    name = route_directory.name
    jd = HorizontalAlignment.from_jd(route_directory / (name + ".JD"))
    icd = HorizontalAlignment.from_icd(route_directory / (name + ".ICD"))
    endpoint_error = jd.end.distance_to(icd.end)
    assert endpoint_error <= ENDPOINT_TOLERANCE_METRES, (
        "{} JD endpoint error {:.9f} m".format(name, endpoint_error)
    )
    station_error = abs(jd.station_end - icd.station_end)
    assert station_error <= STATION_TOLERANCE_METRES, (
        "{} JD station error {:.9f} m".format(name, station_error)
    )
    return "{} JD: endpoint_error={:.9f} m; station_error={:.9f} m".format(
        name, endpoint_error, station_error
    )


def _chord_midpoint_deviation(route, first, second) -> float:
    midpoint = route.point_at((first.station + second.station) * 0.5)
    chord_midpoint = (
        (first.x + second.x) * 0.5,
        (first.y + second.y) * 0.5,
        (first.z + second.z) * 0.5,
    )
    return (
        (midpoint.x - chord_midpoint[0]) ** 2
        + (midpoint.y - chord_midpoint[1]) ** 2
        + (midpoint.z - chord_midpoint[2]) ** 2
    ) ** 0.5


def main() -> None:
    default_root = Path(__file__).resolve().parents[2] / "2026.1.20机场北线立交发BIM"
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else default_root
    route_directories = sorted(path for path in root.iterdir() if path.is_dir())
    if not route_directories:
        raise AssertionError("No EICAD route directories found in {}".format(root))
    for route_directory in route_directories:
        print(validate_route(route_directory))

    for route_directory in route_directories:
        print(validate_jd(route_directory))
    print("All EICAD sample checks passed.")


if __name__ == "__main__":
    main()

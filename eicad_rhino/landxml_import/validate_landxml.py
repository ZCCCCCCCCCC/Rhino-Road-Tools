"""Validate LandXML centre-line parsing without a Rhino session.

Usage:
    python eicad_rhino/landxml_import/validate_landxml.py "D:\\project\\...\\all.xml"
"""

from __future__ import annotations

from pathlib import Path
import sys

from landxml_alignment import list_alignments, load_alignments, route_report


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Pass the path to one LandXML file.")
    source_path = Path(sys.argv[1])
    infos = list_alignments(source_path)
    routes = load_alignments(source_path)
    assert len(infos) == len(routes), "Alignment listing and parse result differ."
    for route in routes:
        samples = route.sample_points(max_step=2.0, chord_tolerance=0.002)
        assert len(samples) >= 2, "{} has too few samples.".format(route.name)
        assert abs(samples[0].station - route.station_start) <= 1.0e-6
        assert abs(samples[-1].station - route.station_end) <= 1.0e-6
        print(route_report(route))
    print("Validated {} LandXML alignments from {}.".format(len(routes), source_path))


if __name__ == "__main__":
    main()

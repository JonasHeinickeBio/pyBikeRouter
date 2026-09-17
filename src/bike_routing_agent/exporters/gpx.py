"""GPX export for a selected route candidate."""

from __future__ import annotations

from typing import Any, cast
from xml.etree import ElementTree as ET

from bike_routing_agent.models import RouteCandidate

_GPX_NS = "http://www.topografix.com/GPX/1/1"


def _coordinate_lines(geometry: dict[str, Any]) -> list[list[list[float]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "LineString":
        return [cast(list[list[float]], coordinates)]
    if geometry_type == "MultiLineString":
        return cast(list[list[list[float]]], coordinates)
    raise ValueError(f"unsupported geometry type for export: {geometry_type!r}")


def to_gpx_element(candidate: RouteCandidate, *, name: str = "route") -> ET.Element:
    gpx = ET.Element(
        "gpx",
        {
            "version": "1.1",
            "creator": "bike-routing-agent",
            "xmlns": _GPX_NS,
        },
    )

    metadata = ET.SubElement(gpx, "metadata")
    ET.SubElement(metadata, "name").text = name
    extensions = ET.SubElement(metadata, "extensions")
    ET.SubElement(extensions, "provider").text = candidate.provider
    ET.SubElement(extensions, "provider_profile").text = candidate.provider_profile

    trk = ET.SubElement(gpx, "trk")
    ET.SubElement(trk, "name").text = name

    for line in _coordinate_lines(candidate.geometry_geojson):
        trkseg = ET.SubElement(trk, "trkseg")
        for point in line:
            lon, lat = point[0], point[1]
            trkpt = ET.SubElement(trkseg, "trkpt", {"lon": str(lon), "lat": str(lat)})
            if len(point) > 2:
                ET.SubElement(trkpt, "ele").text = str(point[2])

    return gpx


def to_gpx_str(candidate: RouteCandidate, *, name: str = "route") -> str:
    element = to_gpx_element(candidate, name=name)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(element, encoding="unicode")

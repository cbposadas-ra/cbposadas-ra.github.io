"""Generate per-bridge 5 km liquefaction GeoJSON files.

Requires: pip install shapely pyproj
"""

import argparse
import json
import math
from pathlib import Path

from pyproj import Transformer
from shapely import make_valid
from shapely.geometry import GeometryCollection, MultiPolygon, Point, Polygon, shape, mapping
from shapely.ops import transform


ROOT = Path(__file__).resolve().parents[1]
BRIDGES_PATH = ROOT / "bridge_points.geojson"
LIQUEFACTION_PATH = ROOT / "Hazard" / "Liquefaction.geojson"
OUTPUT_DIR = ROOT / "Hazard" / "Liquefaction 5km"
BUFFER_METERS = 5000


def polygonal_parts(geometry):
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return list(geometry.geoms)
    if isinstance(geometry, GeometryCollection):
        return [part for member in geometry.geoms for part in polygonal_parts(member)]
    return []


def bridge_buffer(lon, lat):
    zone = min(60, max(1, math.floor((lon + 180) / 6) + 1))
    epsg = (32600 if lat >= 0 else 32700) + zone
    to_projected = Transformer.from_crs(4326, epsg, always_xy=True).transform
    to_geographic = Transformer.from_crs(epsg, 4326, always_xy=True).transform
    projected_point = transform(to_projected, Point(lon, lat))
    return projected_point.buffer(BUFFER_METERS, quad_segs=16), to_projected, to_geographic


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report output size without writing files")
    args = parser.parse_args()

    with BRIDGES_PATH.open(encoding="utf-8") as source:
        bridges = json.load(source)["features"]
    with LIQUEFACTION_PATH.open(encoding="utf-8") as source:
        liquefaction_features = json.load(source)["features"]

    prepared_features = []
    for feature in liquefaction_features:
        geometry = shape(feature["geometry"])
        prepared_features.append((feature, geometry, geometry.bounds))

    total_bytes = 0
    output_dir_ready = False
    for bridge in bridges:
        properties = bridge.get("properties") or {}
        bridge_id = properties.get("SPI_BRIDGE_ID")
        coordinates = (bridge.get("geometry") or {}).get("coordinates") or []
        if bridge_id is None or len(coordinates) < 2:
            continue

        lon, lat = float(coordinates[0]), float(coordinates[1])
        buffer_geometry, to_projected, to_geographic = bridge_buffer(lon, lat)
        lat_delta = BUFFER_METERS / 110574
        lon_delta = BUFFER_METERS / (111320 * max(math.cos(math.radians(lat)), 0.01))
        candidates = [
            (feature, geometry)
            for feature, geometry, bounds in prepared_features
            if bounds[0] <= lon + lon_delta
            and bounds[2] >= lon - lon_delta
            and bounds[1] <= lat + lat_delta
            and bounds[3] >= lat - lat_delta
        ]

        clipped_features = []
        for feature, geometry in candidates:
            projected_geometry = transform(to_projected, geometry)
            if not projected_geometry.is_valid:
                projected_geometry = make_valid(projected_geometry)
            clipped_geometry = projected_geometry.intersection(buffer_geometry)
            parts = polygonal_parts(clipped_geometry)
            if not parts:
                continue

            geographic_geometry = transform(to_geographic, MultiPolygon(parts) if len(parts) > 1 else parts[0])
            clipped_feature = {
                "type": "Feature",
                "properties": feature.get("properties") or {},
                "geometry": mapping(geographic_geometry),
            }
            if "id" in feature:
                clipped_feature["id"] = feature["id"]
            clipped_features.append(clipped_feature)

        collection = {"type": "FeatureCollection", "features": clipped_features}
        output_bytes = len(json.dumps(collection, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        total_bytes += output_bytes
        if not args.dry_run:
            if not output_dir_ready:
                OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                output_dir_ready = True
            output_path = OUTPUT_DIR / f"SPI_Bridge_{bridge_id}.geojson"
            with output_path.open("w", encoding="utf-8", newline="\n") as destination:
                json.dump(collection, destination, separators=(",", ":"), allow_nan=False)

    print(f"Bridge clips: {len(bridges)}")
    print(f"Original liquefaction data: {LIQUEFACTION_PATH.stat().st_size:,} bytes")
    print(f"Generated clip data: {total_bytes:,} bytes")
    if args.dry_run:
        print("Dry run only; no files written.")


if __name__ == "__main__":
    main()
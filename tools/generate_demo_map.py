#!/usr/bin/env python3
"""Generate the synthetic Guanshanhu POC basemap as an MBTiles archive.

The checked-in PMTiles is produced from this deterministic intermediate file.
It deliberately contains no external map data; roads and water are illustrative.
"""

from __future__ import annotations

import math
import sqlite3
import struct
import sys
import zlib
from pathlib import Path

TILE_SIZE = 256
BOUNDS = (106.52, 26.53, 106.74, 26.75)
CENTER = (106.6282, 26.6467)
MIN_ZOOM = 10
MAX_ZOOM = 13

ROADS = (
    ((106.535, 26.685), (106.575, 26.671), (106.628, 26.648), (106.681, 26.621), (106.728, 26.601)),
    ((106.568, 26.545), (106.588, 26.592), (106.611, 26.642), (106.636, 26.698), (106.657, 26.742)),
    ((106.526, 26.617), (106.575, 26.623), (106.628, 26.632), (106.684, 26.646), (106.734, 26.665)),
    ((106.551, 26.721), (106.598, 26.694), (106.645, 26.667), (106.696, 26.637)),
)

LAKE = (
    (106.612, 26.626),
    (106.623, 26.620),
    (106.636, 26.624),
    (106.642, 26.638),
    (106.633, 26.648),
    (106.618, 26.644),
)


def lonlat_to_world(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    scale = TILE_SIZE * (2**zoom)
    x = (lon + 180.0) / 360.0 * scale
    sin_lat = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * scale
    return x, y


def tile_range(zoom: int) -> tuple[range, range]:
    west, south, east, north = BOUNDS
    left, top = lonlat_to_world(west, north, zoom)
    right, bottom = lonlat_to_world(east, south, zoom)
    return (
        range(math.floor(left / TILE_SIZE), math.floor(right / TILE_SIZE) + 1),
        range(math.floor(top / TILE_SIZE), math.floor(bottom / TILE_SIZE) + 1),
    )


def set_pixel(image: bytearray, x: int, y: int, color: tuple[int, int, int, int]) -> None:
    if not (0 <= x < TILE_SIZE and 0 <= y < TILE_SIZE):
        return
    offset = (y * TILE_SIZE + x) * 4
    image[offset : offset + 4] = bytes(color)


def draw_line(
    image: bytearray,
    start: tuple[float, float],
    end: tuple[float, float],
    color: tuple[int, int, int, int],
    width: int,
) -> None:
    x0, y0 = (round(start[0]), round(start[1]))
    x1, y1 = (round(end[0]), round(end[1]))
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    step_x, step_y = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    error = dx + dy
    while True:
        radius = width // 2
        for py in range(y0 - radius, y0 + radius + 1):
            for px in range(x0 - radius, x0 + radius + 1):
                set_pixel(image, px, py, color)
        if x0 == x1 and y0 == y1:
            break
        twice_error = 2 * error
        if twice_error >= dy:
            error += dy
            x0 += step_x
        if twice_error <= dx:
            error += dx
            y0 += step_y


def point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if (current[1] > y) != (previous[1] > y):
            boundary_x = (previous[0] - current[0]) * (y - current[1]) / (previous[1] - current[1]) + current[0]
            if x < boundary_x:
                inside = not inside
        previous = current
    return inside


def png_bytes(image: bytearray) -> bytes:
    rows = bytearray()
    stride = TILE_SIZE * 4
    for row in range(TILE_SIZE):
        rows.append(0)
        rows.extend(image[row * stride : (row + 1) * stride])

    def chunk(kind: bytes, data: bytes) -> bytes:
        payload = kind + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload))

    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", TILE_SIZE, TILE_SIZE, 8, 6, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(bytes(rows), level=9)),
            chunk(b"IEND", b""),
        )
    )


def render_tile(zoom: int, tile_x: int, tile_y: int) -> bytes:
    image = bytearray((8, 27, 30, 255) * (TILE_SIZE * TILE_SIZE))
    origin_x, origin_y = tile_x * TILE_SIZE, tile_y * TILE_SIZE

    grid_spacing = max(24, 96 - zoom * 4)
    grid_color = (26, 62, 64, 255)
    start = -int(origin_x % grid_spacing)
    for coordinate in range(start, TILE_SIZE + grid_spacing, grid_spacing):
        draw_line(image, (coordinate, 0), (coordinate, TILE_SIZE), grid_color, 1)
    start = -int(origin_y % grid_spacing)
    for coordinate in range(start, TILE_SIZE + grid_spacing, grid_spacing):
        draw_line(image, (0, coordinate), (TILE_SIZE, coordinate), grid_color, 1)

    local_lake = [
        (x - origin_x, y - origin_y) for x, y in (lonlat_to_world(lon, lat, zoom) for lon, lat in LAKE)
    ]
    min_x = max(0, math.floor(min(point[0] for point in local_lake)))
    max_x = min(TILE_SIZE - 1, math.ceil(max(point[0] for point in local_lake)))
    min_y = max(0, math.floor(min(point[1] for point in local_lake)))
    max_y = min(TILE_SIZE - 1, math.ceil(max(point[1] for point in local_lake)))
    for y in range(min_y, max_y + 1):
        for x in range(min_x, max_x + 1):
            if point_in_polygon(x, y, local_lake):
                set_pixel(image, x, y, (15, 64, 69, 255))

    for road_index, road in enumerate(ROADS):
        points = [
            (x - origin_x, y - origin_y)
            for x, y in (lonlat_to_world(lon, lat, zoom) for lon, lat in road)
        ]
        for start_point, end_point in zip(points, points[1:]):
            draw_line(image, start_point, end_point, (30, 71, 72, 255), 5 if road_index < 2 else 3)
            draw_line(image, start_point, end_point, (91, 133, 130, 255), 1)

    center_x, center_y = lonlat_to_world(*CENTER, zoom)
    local_center = (center_x - origin_x, center_y - origin_y)
    draw_line(image, (local_center[0] - 5, local_center[1]), (local_center[0] + 5, local_center[1]), (85, 226, 213, 255), 1)
    draw_line(image, (local_center[0], local_center[1] - 5), (local_center[0], local_center[1] + 5), (85, 226, 213, 255), 1)
    return png_bytes(image)


def build_mbtiles(destination: Path) -> None:
    if destination.exists():
        destination.unlink()
    with sqlite3.connect(destination) as database:
        database.executescript(
            """
            CREATE TABLE metadata (name TEXT, value TEXT);
            CREATE TABLE tiles (
              zoom_level INTEGER,
              tile_column INTEGER,
              tile_row INTEGER,
              tile_data BLOB
            );
            CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row);
            """
        )
        metadata = {
            "name": "Guanshanhu POC synthetic basemap",
            "description": "Deterministic illustrative basemap for offline POC testing",
            "version": "1.0.0",
            "type": "baselayer",
            "format": "png",
            "bounds": ",".join(str(value) for value in BOUNDS),
            "center": f"{CENTER[0]},{CENTER[1]},12",
            "minzoom": str(MIN_ZOOM),
            "maxzoom": str(MAX_ZOOM),
            "attribution": "Synthetic POC basemap; no external map data",
        }
        database.executemany("INSERT INTO metadata VALUES (?, ?)", metadata.items())
        for zoom in range(MIN_ZOOM, MAX_ZOOM + 1):
            x_range, y_range = tile_range(zoom)
            for tile_x in x_range:
                for tile_y in y_range:
                    tms_y = (2**zoom - 1) - tile_y
                    database.execute(
                        "INSERT INTO tiles VALUES (?, ?, ?, ?)",
                        (zoom, tile_x, tms_y, render_tile(zoom, tile_x, tile_y)),
                    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: generate_demo_map.py OUTPUT.mbtiles")
    build_mbtiles(Path(sys.argv[1]))

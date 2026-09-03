from __future__ import annotations

from itertools import pairwise
from numbers import Real

Point = tuple[float, float]
EPSILON = 1e-10


def polygon_ring(geometry: dict[str, object]) -> list[Point]:
    if geometry.get("type") != "Polygon":
        raise ValueError("水平范围必须是 WGS-84 GeoJSON Polygon")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) != 1:
        raise ValueError("POC 水平范围必须且只能包含一个外环")
    raw_ring = coordinates[0]
    if not isinstance(raw_ring, list) or len(raw_ring) < 4:
        raise ValueError("Polygon 外环至少需要四个坐标点")
    ring: list[Point] = []
    for coordinate in raw_ring:
        if (
            not isinstance(coordinate, list)
            or len(coordinate) != 2
            or isinstance(coordinate[0], bool)
            or isinstance(coordinate[1], bool)
            or not isinstance(coordinate[0], Real)
            or not isinstance(coordinate[1], Real)
        ):
            raise ValueError("坐标点必须是 [经度, 纬度]")
        longitude, latitude = float(coordinate[0]), float(coordinate[1])
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise ValueError("坐标超出 WGS-84 范围")
        ring.append((longitude, latitude))
    if ring[0] != ring[-1]:
        raise ValueError("Polygon 外环必须闭合")
    if abs(_signed_area(ring)) < 1e-12:
        raise ValueError("Polygon 面积必须大于零")
    _reject_self_intersection(ring)
    return ring


def _signed_area(ring: list[Point]) -> float:
    return (
        sum(
            first[0] * second[1] - second[0] * first[1]
            for first, second in pairwise(ring)
        )
        / 2
    )


def _orientation(first: Point, second: Point, third: Point) -> float:
    return (second[0] - first[0]) * (third[1] - first[1]) - (second[1] - first[1]) * (
        third[0] - first[0]
    )


def _segments_intersect(
    first_start: Point, first_end: Point, second_start: Point, second_end: Point
) -> bool:
    first_side = _orientation(first_start, first_end, second_start)
    second_side = _orientation(first_start, first_end, second_end)
    third_side = _orientation(second_start, second_end, first_start)
    fourth_side = _orientation(second_start, second_end, first_end)
    if (
        (first_side > EPSILON and second_side < -EPSILON)
        or (first_side < -EPSILON and second_side > EPSILON)
    ) and (
        (third_side > EPSILON and fourth_side < -EPSILON)
        or (third_side < -EPSILON and fourth_side > EPSILON)
    ):
        return True
    return any(
        abs(orientation) <= EPSILON and _point_on_segment(point, start, end)
        for orientation, point, start, end in (
            (first_side, second_start, first_start, first_end),
            (second_side, second_end, first_start, first_end),
            (third_side, first_start, second_start, second_end),
            (fourth_side, first_end, second_start, second_end),
        )
    )


def point_location(point: Point, ring: list[Point]) -> str:
    inside = False
    for start, end in pairwise(ring):
        if _point_on_segment(point, start, end):
            return "boundary"
        if (start[1] > point[1]) != (end[1] > point[1]):
            crossing_longitude = start[0] + (point[1] - start[1]) * (
                end[0] - start[0]
            ) / (end[1] - start[1])
            if crossing_longitude > point[0]:
                inside = not inside
    return "inside" if inside else "outside"


def segment_boundary_parameters(
    segment_start: Point, segment_end: Point, ring: list[Point]
) -> list[float]:
    parameters: list[float] = []
    direction = (
        segment_end[0] - segment_start[0],
        segment_end[1] - segment_start[1],
    )
    for edge_start, edge_end in pairwise(ring):
        edge_direction = (
            edge_end[0] - edge_start[0],
            edge_end[1] - edge_start[1],
        )
        denominator = _cross(direction, edge_direction)
        offset = (
            edge_start[0] - segment_start[0],
            edge_start[1] - segment_start[1],
        )
        if abs(denominator) > EPSILON:
            route_parameter = _cross(offset, edge_direction) / denominator
            edge_parameter = _cross(offset, direction) / denominator
            if (
                -EPSILON <= route_parameter <= 1 + EPSILON
                and -EPSILON <= edge_parameter <= 1 + EPSILON
            ):
                parameters.append(min(1.0, max(0.0, route_parameter)))
            continue
        if abs(_cross(offset, direction)) <= EPSILON:
            for point in (edge_start, edge_end):
                parameter = _parameter_on_segment(point, segment_start, segment_end)
                if parameter is not None:
                    parameters.append(parameter)
    return sorted({round(parameter, 12) for parameter in parameters})


def _cross(first: Point, second: Point) -> float:
    return first[0] * second[1] - first[1] * second[0]


def _point_on_segment(point: Point, start: Point, end: Point) -> bool:
    if abs(_orientation(start, end, point)) > EPSILON:
        return False
    return (
        min(start[0], end[0]) - EPSILON <= point[0] <= max(start[0], end[0]) + EPSILON
        and min(start[1], end[1]) - EPSILON
        <= point[1]
        <= max(start[1], end[1]) + EPSILON
    )


def _parameter_on_segment(point: Point, start: Point, end: Point) -> float | None:
    if not _point_on_segment(point, start, end):
        return None
    longitude_delta = end[0] - start[0]
    latitude_delta = end[1] - start[1]
    if abs(longitude_delta) >= abs(latitude_delta) and abs(longitude_delta) > EPSILON:
        return (point[0] - start[0]) / longitude_delta
    if abs(latitude_delta) > EPSILON:
        return (point[1] - start[1]) / latitude_delta
    return 0.0


def _reject_self_intersection(ring: list[Point]) -> None:
    edge_count = len(ring) - 1
    for first_index in range(edge_count):
        for second_index in range(first_index + 1, edge_count):
            if second_index == first_index + 1 or {
                first_index,
                second_index,
            } == {0, edge_count - 1}:
                continue
            if _segments_intersect(
                ring[first_index],
                ring[first_index + 1],
                ring[second_index],
                ring[second_index + 1],
            ):
                raise ValueError("Polygon 外环不能自相交")

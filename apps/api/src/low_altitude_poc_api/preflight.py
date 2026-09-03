from __future__ import annotations

from datetime import datetime
from itertools import pairwise
from typing import Literal

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from low_altitude_poc_api.auth import AuthenticatedUser, AuthService
from low_altitude_poc_api.geometry import (
    point_location,
    polygon_ring,
    segment_boundary_parameters,
)
from low_altitude_poc_api.spatial_rules import (
    SpatialRuleDraft,
    effective_rule_records,
)


class PlannedRoutePoint(BaseModel):
    longitude: float
    latitude: float
    altitude_m: float
    time: datetime

    @field_validator("longitude")
    @classmethod
    def longitude_is_wgs84(cls, value: float) -> float:
        if not -180 <= value <= 180:
            raise ValueError("经度超出 WGS-84 范围")
        return value

    @field_validator("latitude")
    @classmethod
    def latitude_is_wgs84(cls, value: float) -> float:
        if not -90 <= value <= 90:
            raise ValueError("纬度超出 WGS-84 范围")
        return value

    @field_validator("time")
    @classmethod
    def time_has_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("计划航线时间必须包含时区")
        return value


class PlannedRoute(BaseModel):
    coordinate_reference: Literal["WGS84"]
    points: list[PlannedRoutePoint] = Field(min_length=2)


class ViolationPosition(BaseModel):
    longitude: float
    latitude: float
    altitude_m: float
    time: datetime


class PreflightViolation(BaseModel):
    outcome: Literal["entered_no_fly_zone", "outside_geofence"]
    rule_id: str
    rule_version: int
    position: ViolationPosition
    reason: str


class PreflightResult(BaseModel):
    result: Literal["passed", "entered_no_fly_zone", "outside_geofence"]
    violations: list[PreflightViolation]


def _interpolate(
    start: PlannedRoutePoint, end: PlannedRoutePoint, parameter: float
) -> ViolationPosition:
    return ViolationPosition(
        longitude=start.longitude + (end.longitude - start.longitude) * parameter,
        latitude=start.latitude + (end.latitude - start.latitude) * parameter,
        altitude_m=start.altitude_m + (end.altitude_m - start.altitude_m) * parameter,
        time=start.time + (end.time - start.time) * parameter,
    )


def _candidate_positions(
    route: PlannedRoute,
    ring: list[tuple[float, float]],
    rule: SpatialRuleDraft,
) -> list[ViolationPosition]:
    positions: list[ViolationPosition] = []
    for start, end in pairwise(route.points):
        parameters = [
            0.0,
            *segment_boundary_parameters(
                (start.longitude, start.latitude),
                (end.longitude, end.latitude),
                ring,
            ),
            1.0,
        ]
        altitude_delta = end.altitude_m - start.altitude_m
        if altitude_delta != 0:
            for altitude_boundary in (
                rule.min_altitude_m,
                rule.max_altitude_m,
            ):
                parameter = (altitude_boundary - start.altitude_m) / altitude_delta
                if 0 <= parameter <= 1:
                    parameters.append(parameter)
        time_delta = (end.time - start.time).total_seconds()
        if time_delta != 0:
            for time_boundary in (rule.valid_from, rule.valid_to):
                parameter = (time_boundary - start.time).total_seconds() / time_delta
                if 0 <= parameter <= 1:
                    parameters.append(parameter)
        ordered = sorted(set(parameters))
        sample_parameters = ordered + [
            (first + second) / 2 for first, second in pairwise(ordered)
        ]
        positions.extend(
            _interpolate(start, end, parameter)
            for parameter in sorted(set(sample_parameters))
        )
    return positions


def _rule_violation(
    route: PlannedRoute,
    rule: SpatialRuleDraft,
    version: int,
) -> PreflightViolation | None:
    ring = polygon_ring(rule.geometry)
    for position in _candidate_positions(route, ring, rule):
        if not rule.valid_from <= position.time <= rule.valid_to:
            continue
        location = point_location((position.longitude, position.latitude), ring)
        within_altitude = (
            rule.min_altitude_m <= position.altitude_m <= rule.max_altitude_m
        )
        if rule.rule_type == "no_fly_zone" and (
            location != "outside" and within_altitude
        ):
            return PreflightViolation(
                outcome="entered_no_fly_zone",
                rule_id=rule.rule_id,
                rule_version=version,
                position=position,
                reason=(
                    f"计划航线在有效时间和 {rule.min_altitude_m:g}–"
                    f"{rule.max_altitude_m:g} 米高度范围内进入禁飞区"
                ),
            )
        if rule.rule_type == "geofence" and (
            location == "outside" or not within_altitude
        ):
            return PreflightViolation(
                outcome="outside_geofence",
                rule_id=rule.rule_id,
                rule_version=version,
                position=position,
                reason=("计划航线超出电子围栏的水平范围或允许高度范围"),
            )
    return None


def configure_preflight_validation(
    app: FastAPI, engine: Engine, auth: AuthService
) -> None:
    @app.post("/api/preflight-validations", response_model=PreflightResult)
    def validate_planned_route(
        route: PlannedRoute,
        _user: AuthenticatedUser = Depends(auth.require_user),  # noqa: B008
    ) -> PreflightResult:
        minimum_longitude = min(point.longitude for point in route.points)
        maximum_longitude = max(point.longitude for point in route.points)
        minimum_latitude = min(point.latitude for point in route.points)
        maximum_latitude = max(point.latitude for point in route.points)
        route_starts_at = min(point.time for point in route.points)
        route_ends_at = max(point.time for point in route.points)
        with Session(engine) as database:
            coarse_ids = set(
                database.execute(
                    text(
                        "SELECT id FROM spatial_rule_rtree "
                        "WHERE max_longitude >= :minimum_longitude "
                        "AND min_longitude <= :maximum_longitude "
                        "AND max_latitude >= :minimum_latitude "
                        "AND min_latitude <= :maximum_latitude"
                    ),
                    {
                        "minimum_longitude": minimum_longitude,
                        "maximum_longitude": maximum_longitude,
                        "minimum_latitude": minimum_latitude,
                        "maximum_latitude": maximum_latitude,
                    },
                ).scalars()
            )
            records = effective_rule_records(database, route_starts_at, route_ends_at)

        violations: list[PreflightViolation] = []
        for record, rule in records:
            if rule.rule_type == "no_fly_zone" and record.id not in coarse_ids:
                continue
            violation = _rule_violation(route, rule, record.version)
            if violation is not None:
                violations.append(violation)

        violations.sort(key=lambda item: item.outcome != "entered_no_fly_zone")
        result = violations[0].outcome if violations else "passed"
        return PreflightResult(result=result, violations=violations)

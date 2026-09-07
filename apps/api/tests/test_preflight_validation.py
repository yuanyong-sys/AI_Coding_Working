from fastapi.testclient import TestClient

from low_altitude_poc_api.app import create_app

TEST_PASSWORD = "local-test-password"


def login(client: TestClient, username: str = "platform-operator") -> None:
    assert (
        client.post(
            "/api/auth/login",
            json={"username": username, "password": TEST_PASSWORD},
        ).status_code
        == 200
    )


def publish_rule(
    client: TestClient,
    rule_id: str,
    rule_type: str,
    coordinates: list[list[float]],
    min_altitude: float = 60,
    max_altitude: float = 180,
    valid_from: str = "2026-09-03T00:00:00Z",
    valid_to: str = "2027-09-30T00:00:00Z",
) -> None:
    draft = {
        "rule_id": rule_id,
        "name": rule_id,
        "rule_type": rule_type,
        "geometry": {"type": "Polygon", "coordinates": [coordinates]},
        "min_altitude_m": min_altitude,
        "max_altitude_m": max_altitude,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "source": "航前规则校验测试",
    }
    assert client.post("/api/spatial-rules/drafts", json=draft).status_code == 201
    assert (
        client.post(f"/api/spatial-rules/drafts/{rule_id}/publish").status_code == 201
    )


def route(
    first: tuple[float, float],
    second: tuple[float, float],
    *,
    altitude: float = 100,
    start: str = "2026-09-03T01:00:00Z",
    end: str = "2026-09-03T01:01:00Z",
) -> dict:
    return {
        "coordinate_reference": "WGS84",
        "points": [
            {
                "longitude": first[0],
                "latitude": first[1],
                "altitude_m": altitude,
                "time": start,
            },
            {
                "longitude": second[0],
                "latitude": second[1],
                "altitude_m": altitude,
                "time": end,
            },
        ],
    }


def test_preflight_validation_explains_no_fly_and_geofence_results(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'preflight.db'}",
        demo_password=TEST_PASSWORD,
    )
    no_fly_zone = [
        [106.61, 26.63],
        [106.64, 26.63],
        [106.64, 26.66],
        [106.61, 26.66],
        [106.61, 26.63],
    ]
    geofence = [
        [106.58, 26.60],
        [106.68, 26.60],
        [106.68, 26.69],
        [106.58, 26.69],
        [106.58, 26.60],
    ]

    with TestClient(app) as client:
        login(client)
        publish_rule(client, "NFZ-001", "no_fly_zone", no_fly_zone)
        publish_rule(client, "FENCE-001", "geofence", geofence, 50, 200)

        entered = client.post(
            "/api/preflight-validations",
            json=route((106.60, 26.645), (106.65, 26.645), altitude=60),
        )
        outside = client.post(
            "/api/preflight-validations",
            json=route((106.67, 26.62), (106.70, 26.62)),
        )
        passed = client.post(
            "/api/preflight-validations",
            json=route((106.59, 26.62), (106.60, 26.62), altitude=59),
        )
        tangent = client.post(
            "/api/preflight-validations",
            json=route((106.60, 26.63), (106.65, 26.63)),
        )

    assert entered.status_code == 200
    assert entered.json()["result"] == "entered_no_fly_zone"
    assert entered.json()["violations"][0]["rule_id"] == "NFZ-001"
    assert entered.json()["violations"][0]["rule_version"] == 1
    assert entered.json()["violations"][0]["position"] == {
        "longitude": 106.61,
        "latitude": 26.645,
        "altitude_m": 60.0,
        "time": "2026-09-03T01:00:12Z",
    }
    assert "禁飞区" in entered.json()["violations"][0]["reason"]
    assert outside.json()["result"] == "outside_geofence"
    assert outside.json()["violations"][0]["rule_id"] == "FENCE-001"
    assert passed.json() == {"result": "passed", "violations": []}
    assert tangent.json()["result"] == "entered_no_fly_zone"


def test_preflight_boundaries_wgs84_and_invalid_plan_geometry(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'preflight-boundaries.db'}",
        demo_password=TEST_PASSWORD,
    )
    polygon = [
        [106.61, 26.63],
        [106.64, 26.63],
        [106.64, 26.66],
        [106.61, 26.66],
        [106.61, 26.63],
    ]

    with TestClient(app) as client:
        login(client)
        publish_rule(client, "NFZ-BOUNDARY", "no_fly_zone", polygon)
        publish_rule(
            client,
            "NFZ-BOUNDARY",
            "no_fly_zone",
            [
                [106.70, 26.70],
                [106.72, 26.70],
                [106.72, 26.72],
                [106.70, 26.72],
                [106.70, 26.70],
            ],
            valid_from="2028-01-01T00:00:00Z",
            valid_to="2028-12-31T00:00:00Z",
        )
        at_time_boundary = client.post(
            "/api/preflight-validations",
            json=route(
                (106.62, 26.64),
                (106.63, 26.64),
                start="2027-09-30T00:00:00Z",
                end="2027-09-30T00:00:00Z",
            ),
        )
        across_version_switch = client.post(
            "/api/preflight-validations",
            json=route(
                (106.62, 26.64),
                (106.62, 26.64),
                start="2027-09-30T00:00:00Z",
                end="2028-01-01T00:00:00Z",
            ),
        )
        at_upper_altitude_and_start_time = client.post(
            "/api/preflight-validations",
            json=route(
                (106.62, 26.64),
                (106.63, 26.64),
                altitude=180,
                start="2026-09-03T00:00:00Z",
                end="2026-09-03T00:00:00Z",
            ),
        )
        above_altitude = client.post(
            "/api/preflight-validations",
            json=route(
                (106.62, 26.64),
                (106.63, 26.64),
                altitude=180.001,
            ),
        )
        wrong_coordinates = client.post(
            "/api/preflight-validations",
            json={
                **route((106.62, 26.64), (106.63, 26.64)),
                "coordinate_reference": "GCJ02",
            },
        )
        invalid_geometry = client.post(
            "/api/preflight-validations",
            json={
                "coordinate_reference": "WGS84",
                "points": route((0, 0), (1, 1))["points"][:1],
            },
        )
        publish_rule(
            client,
            "NFZ-BOUNDARY",
            "no_fly_zone",
            [
                [106.70, 26.70],
                [106.72, 26.70],
                [106.72, 26.72],
                [106.70, 26.72],
                [106.70, 26.70],
            ],
        )
        replaced_version = client.post(
            "/api/preflight-validations",
            json=route((106.62, 26.64), (106.63, 26.64)),
        )

    assert at_time_boundary.json()["result"] == "entered_no_fly_zone"
    assert at_time_boundary.json()["violations"][0]["rule_version"] == 1
    assert across_version_switch.json()["result"] == "entered_no_fly_zone"
    assert across_version_switch.json()["violations"][0]["rule_version"] == 1
    assert at_upper_altitude_and_start_time.json()["result"] == "entered_no_fly_zone"
    assert above_altitude.json()["result"] == "passed"
    assert wrong_coordinates.status_code == 422
    assert invalid_geometry.status_code == 422
    assert replaced_version.json()["result"] == "passed"


def test_high_precision_tangent_is_not_rounded_outside(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'precise-tangent.db'}",
        demo_password=TEST_PASSWORD,
    )
    with TestClient(app) as client:
        login(client)
        publish_rule(
            client,
            "NFZ-PRECISE",
            "no_fly_zone",
            [
                [106.61, 26.63],
                [106.64, 26.63],
                [106.64, 26.66],
                [106.61, 26.66],
                [106.61, 26.63],
            ],
        )
        response = client.post(
            "/api/preflight-validations",
            json=route((106.610000004, 26.62), (106.610000004, 26.64)),
        )

    assert response.json()["result"] == "entered_no_fly_zone"

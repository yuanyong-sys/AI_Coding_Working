from fastapi.testclient import TestClient

from low_altitude_poc_api.app import create_app

TEST_PASSWORD = "local-test-password"


def test_incursion_alert_requires_persistence_and_ends_after_recovery(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'incursions.db'}",
        demo_password=TEST_PASSWORD,
    )
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/auth/login",
                json={"username": "platform-operator", "password": TEST_PASSWORD},
            ).status_code
            == 200
        )
        draft = {
            "rule_id": "GSH-NFZ-ALERT",
            "name": "告警测试禁飞区",
            "rule_type": "no_fly_zone",
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [106.61, 26.63],
                        [106.64, 26.63],
                        [106.64, 26.66],
                        [106.61, 26.66],
                        [106.61, 26.63],
                    ]
                ],
            },
            "min_altitude_m": 60,
            "max_altitude_m": 180,
            "valid_from": "2026-09-04T00:00:00Z",
            "valid_to": "2026-09-05T00:00:00Z",
            "source": "越界告警测试",
        }
        assert client.post("/api/spatial-rules/drafts", json=draft).status_code == 201
        assert (
            client.post("/api/spatial-rules/drafts/GSH-NFZ-ALERT/publish").status_code
            == 201
        )

        def telemetry(
            event_id: str, longitude: float, time: str, flight_state: str = "flying"
        ) -> None:
            response = client.post(
                "/api/telemetry/batches",
                json={
                    "events": [
                        {
                            "event_id": event_id,
                            "sortie_id": "GSH-ALERT-SORTIE",
                            "drone_id": "UAV-GSH-ALERT",
                            "longitude": longitude,
                            "latitude": 26.645,
                            "altitude_m": 100,
                            "heading_deg": 90,
                            "speed_mps": 8,
                            "flight_state": flight_state,
                            "source_time": time,
                            "source_type": "simulated",
                        }
                    ]
                },
            )
            assert response.status_code == 202

        telemetry("pending-1", 106.62, "2026-09-04T00:59:55Z", "pending")
        telemetry("pending-2", 106.62, "2026-09-04T00:59:58Z", "pending")
        assert client.get("/api/situation/snapshot").json()["incursion_alerts"] == []
        telemetry("near-boundary", 106.61001, "2026-09-04T01:00:00Z")
        telemetry("inside-1", 106.62, "2026-09-04T01:00:01Z")
        assert client.get("/api/situation/snapshot").json()["incursion_alerts"] == []
        telemetry("pending-break", 106.62, "2026-09-04T01:00:02Z", "pending")
        telemetry("inside-after-pending", 106.62, "2026-09-04T01:00:03Z")
        telemetry("candidate-buffer", 106.61001, "2026-09-04T01:00:04Z")
        telemetry("inside-2", 106.621, "2026-09-04T01:00:05Z")
        assert client.get("/api/situation/snapshot").json()["incursion_alerts"] == []
        telemetry("inside-3", 106.622, "2026-09-04T01:00:07Z")
        active = client.get("/api/situation/snapshot").json()["incursion_alerts"][0]
        assert active["drone_id"] == "UAV-GSH-ALERT"
        assert active["rule_id"] == "GSH-NFZ-ALERT"
        assert active["rule_version"] == 1
        assert active["started_at"] == "2026-09-04T01:00:05Z"
        assert active["ended_at"] is None
        assert active["reason"] == "无人机持续进入禁飞区"

        assert active["rule_snapshot"]["name"] == "告警测试禁飞区"
        assert active["rule_snapshot"]["valid_from"] == "2026-09-04T00:00:00Z"
        assert active["rule_snapshot"]["valid_to"] == "2026-09-05T00:00:00Z"
        assert active["source_type"] == "simulated"
        assert active["platform_received_time"]
        telemetry("buffer-1", 106.60999, "2026-09-04T01:00:08Z")
        telemetry("buffer-2", 106.61001, "2026-09-04T01:00:09Z")
        assert (
            client.get("/api/situation/snapshot").json()["incursion_alerts"][0][
                "ended_at"
            ]
            is None
        )
        telemetry("outside-1", 106.60, "2026-09-04T01:00:10Z")
        assert (
            client.get("/api/situation/snapshot").json()["incursion_alerts"][0][
                "ended_at"
            ]
            is None
        )
        telemetry("outside-2", 106.599, "2026-09-04T01:00:12Z")
        ended = client.get("/api/situation/snapshot").json()["incursion_alerts"][0]
        assert ended["ended_at"] == "2026-09-04T01:00:12Z"


def test_incursion_alert_does_not_emit_control_or_violation_conclusions(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'safe-language.db'}",
        demo_password=TEST_PASSWORD,
    )
    schema = app.openapi()
    serialized = str(schema)
    assert "control_command" not in serialized
    assert "违规认定" not in serialized

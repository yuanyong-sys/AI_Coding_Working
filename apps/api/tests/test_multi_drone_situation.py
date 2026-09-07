from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from low_altitude_poc_api.app import create_app

TEST_PASSWORD = "local-test-password"


def login(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "platform-operator", "password": TEST_PASSWORD},
    )
    assert response.status_code == 200


def telemetry(index: int, *, source_type: str = "simulated") -> dict[str, object]:
    return {
        "event_id": f"fleet-{index:02d}",
        "drone_id": f"UAV-GSH-{index:02d}",
        "longitude": 106.58 + index * 0.004,
        "latitude": 26.61 + index * 0.002,
        "altitude_m": 60 + index,
        "heading_deg": 90,
        "speed_mps": 10,
        "flight_state": "flying" if index <= 12 else "waiting",
        "source_time": "2026-09-03T06:29:59Z",
        "source_type": source_type,
        "sortie_id": f"GSH-SORTIE-{index:02d}",
    }


def test_metrics_and_data_freshness_thresholds_for_twenty_drones(tmp_path):
    class Clock:
        current = datetime(2026, 9, 3, 6, 30, tzinfo=UTC)

        def __call__(self) -> datetime:
            return self.current

    clock = Clock()
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'metrics.db'}",
        demo_password=TEST_PASSWORD,
        clock=clock,
    )
    events = [
        telemetry(index, source_type="real" if index <= 5 else "simulated")
        for index in range(1, 21)
    ]

    with TestClient(app) as client:
        login(client)
        assert client.post(
            "/api/telemetry/batches", json={"events": events}
        ).json() == {
            "accepted": 20,
            "rejected": 0,
        }
        clock.current += timedelta(seconds=10)
        at_ten = client.get("/api/situation/snapshot").json()
        clock.current += timedelta(microseconds=1)
        after_ten = client.get("/api/situation/snapshot").json()
        clock.current += timedelta(seconds=20) - timedelta(microseconds=1)
        at_thirty = client.get("/api/situation/snapshot").json()
        clock.current += timedelta(microseconds=1)
        after_thirty = client.get("/api/situation/snapshot").json()

    assert at_ten["metrics"] == {
        "flight_sorties": 20,
        "online_rate": 100.0,
        "in_flight_count": 12,
        "telemetry_delay_seconds": 10.0,
        "source_composition": {"real": 5, "simulated": 15},
        "observation_window_seconds": 30,
    }
    assert {drone["data_status"] for drone in at_ten["drones"]} == {"current"}
    assert {drone["data_status"] for drone in after_ten["drones"]} == {"delayed"}
    assert {drone["data_status"] for drone in at_thirty["drones"]} == {"delayed"}
    assert {drone["data_status"] for drone in after_thirty["drones"]} == {"offline"}
    assert after_thirty["metrics"]["online_rate"] == 0.0
    assert after_thirty["metrics"]["in_flight_count"] == 0
    assert after_thirty["drones"][0]["platform_received_time"] == "2026-09-03T06:30:00Z"


def test_websocket_replays_cursor_gaps_and_falls_back_to_snapshot(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'cursor.db'}",
        demo_password=TEST_PASSWORD,
        clock=lambda: datetime(2026, 9, 3, 6, 31, tzinfo=UTC),
        event_retention=2,
    )
    events = [telemetry(index) for index in range(1, 4)]

    with TestClient(app) as client:
        login(client)
        client.post("/api/telemetry/batches", json={"events": events[:2]})
        assert client.get("/api/situation/snapshot").json()["cursor"] == 2

        with client.websocket_connect("/api/situation/events?after=0") as websocket:
            for cursor, event in enumerate(events[:2], start=1):
                assert websocket.receive_json() == {
                    "type": "telemetry",
                    "cursor": cursor,
                    "event": event,
                }

        client.post("/api/telemetry/batches", json={"events": [events[2]]})
        with client.websocket_connect("/api/situation/events?after=0") as websocket:
            assert websocket.receive_json() == {
                "type": "snapshot_required",
                "cursor": 3,
            }

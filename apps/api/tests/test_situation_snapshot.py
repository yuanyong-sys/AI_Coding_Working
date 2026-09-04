from datetime import UTC, datetime

from fastapi.testclient import TestClient

from low_altitude_poc_api.app import create_app

TEST_PASSWORD = "local-test-password"


def login(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "situation-viewer", "password": TEST_PASSWORD},
    )
    assert response.status_code == 200


def test_simulated_telemetry_is_visible_in_situation_snapshot(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'situation.db'}"
    before_ingest = datetime.now(UTC)

    with TestClient(
        create_app(database_url=database_url, demo_password=TEST_PASSWORD)
    ) as client:
        login(client)
        ingest_response = client.post(
            "/api/telemetry/batches",
            json={
                "events": [
                    {
                        "event_id": "sim-telemetry-001",
                        "drone_id": "UAV-GSH-01",
                        "longitude": 106.6282,
                        "latitude": 26.6467,
                        "altitude_m": 86.0,
                        "heading_deg": 125.0,
                        "speed_mps": 12.4,
                        "flight_state": "flying",
                        "source_time": "2026-09-02T06:32:08Z",
                        "source_type": "simulated",
                        "sortie_id": "GSH-SORTIE-001",
                    }
                ]
            },
        )

        assert ingest_response.status_code == 202
        assert ingest_response.json() == {"accepted": 1, "rejected": 0}

        snapshot_response = client.get("/api/situation/snapshot")
        after_snapshot = datetime.now(UTC)

    assert snapshot_response.status_code == 200
    snapshot = snapshot_response.json()
    assert snapshot.pop("cursor") == 1
    assert snapshot.pop("incursion_alerts") == []
    metrics = snapshot.pop("metrics")
    platform_received_time = datetime.fromisoformat(
        snapshot["drones"][0].pop("platform_received_time")
    )
    data_status = snapshot["drones"][0].pop("data_status")
    assert before_ingest <= platform_received_time <= after_snapshot
    assert data_status == "current"
    assert metrics["flight_sorties"] == 1
    assert metrics["online_rate"] == 100.0
    assert metrics["in_flight_count"] == 1
    assert metrics["source_composition"] == {"real": 0, "simulated": 1}
    assert metrics["observation_window_seconds"] == 30
    assert 0 <= metrics["telemetry_delay_seconds"] < 1
    assert snapshot == {
        "drones": [
            {
                "drone_id": "UAV-GSH-01",
                "longitude": 106.6282,
                "latitude": 26.6467,
                "altitude_m": 86.0,
                "heading_deg": 125.0,
                "speed_mps": 12.4,
                "flight_state": "flying",
                "source_time": "2026-09-02T06:32:08Z",
                "source_type": "simulated",
                "track": [
                    {
                        "event_id": "sim-telemetry-001",
                        "longitude": 106.6282,
                        "latitude": 26.6467,
                        "altitude_m": 86.0,
                        "source_time": "2026-09-02T06:32:08Z",
                    }
                ],
            }
        ]
    }


def test_late_and_duplicate_telemetry_preserves_ordered_track_and_latest_state(
    tmp_path,
):
    database_url = f"sqlite:///{tmp_path / 'ordered-track.db'}"
    newer_event = {
        "event_id": "track-002",
        "drone_id": "UAV-GSH-02",
        "longitude": 106.6382,
        "latitude": 26.6567,
        "altitude_m": 96.0,
        "heading_deg": 140.0,
        "speed_mps": 13.4,
        "flight_state": "flying",
        "source_time": "2026-09-02T06:32:18Z",
        "source_type": "simulated",
        "sortie_id": "GSH-SORTIE-003",
    }
    older_event = {
        "event_id": "track-001",
        "drone_id": "UAV-GSH-02",
        "longitude": 106.6282,
        "latitude": 26.6467,
        "altitude_m": 86.0,
        "heading_deg": 125.0,
        "speed_mps": 12.4,
        "flight_state": "flying",
        "source_time": "2026-09-02T06:32:08Z",
        "source_type": "simulated",
        "sortie_id": "GSH-SORTIE-003",
    }

    with TestClient(
        create_app(database_url=database_url, demo_password=TEST_PASSWORD)
    ) as client:
        login(client)
        for event in (newer_event, older_event, older_event):
            response = client.post("/api/telemetry/batches", json={"events": [event]})
            assert response.status_code == 202
            assert response.json() == {"accepted": 1, "rejected": 0}

        snapshot = client.get("/api/situation/snapshot").json()

    drone = snapshot["drones"][0]
    assert drone["longitude"] == 106.6382
    assert drone["source_time"] == "2026-09-02T06:32:18Z"
    assert drone["track"] == [
        {
            "event_id": "track-001",
            "longitude": 106.6282,
            "latitude": 26.6467,
            "altitude_m": 86.0,
            "source_time": "2026-09-02T06:32:08Z",
        },
        {
            "event_id": "track-002",
            "longitude": 106.6382,
            "latitude": 26.6567,
            "altitude_m": 96.0,
            "source_time": "2026-09-02T06:32:18Z",
        },
    ]


def test_batch_reports_invalid_coordinates_and_source_time_without_losing_valid_data(
    tmp_path,
):
    database_url = f"sqlite:///{tmp_path / 'partial-batch.db'}"
    valid_event = {
        "event_id": "valid-001",
        "drone_id": "UAV-GSH-03",
        "longitude": 106.6282,
        "latitude": 26.6467,
        "altitude_m": 80.0,
        "heading_deg": 90.0,
        "speed_mps": 10.0,
        "flight_state": "flying",
        "source_time": "2026-09-02T06:32:08Z",
        "source_type": "simulated",
        "sortie_id": "GSH-SORTIE-004",
    }
    invalid_coordinate = {
        **valid_event,
        "event_id": "invalid-coordinate",
        "longitude": 181.0,
    }
    invalid_time = {
        **valid_event,
        "event_id": "invalid-time",
        "source_time": "2099-01-01T00:00:00Z",
    }

    with TestClient(
        create_app(database_url=database_url, demo_password=TEST_PASSWORD)
    ) as client:
        login(client)
        response = client.post(
            "/api/telemetry/batches",
            json={"events": [valid_event, invalid_coordinate, invalid_time]},
        )
        snapshot = client.get("/api/situation/snapshot").json()

    assert response.status_code == 202
    assert response.json() == {
        "accepted": 1,
        "rejected": 2,
        "errors": [
            {
                "event_id": "invalid-coordinate",
                "field": "longitude",
                "reason": "must be between -180 and 180",
            },
            {
                "event_id": "invalid-time",
                "field": "source_time",
                "reason": "must not be more than 5 minutes in the future",
            },
        ],
    }
    assert [drone["drone_id"] for drone in snapshot["drones"]] == ["UAV-GSH-03"]

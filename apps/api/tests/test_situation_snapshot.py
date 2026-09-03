from datetime import UTC, datetime

from fastapi.testclient import TestClient

from low_altitude_poc_api.app import create_app


def test_simulated_telemetry_is_visible_in_situation_snapshot(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'situation.db'}"
    before_ingest = datetime.now(UTC)

    with TestClient(create_app(database_url=database_url)) as client:
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
    platform_received_time = datetime.fromisoformat(
        snapshot["drones"][0].pop("platform_received_time")
    )
    assert before_ingest <= platform_received_time <= after_snapshot
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
            }
        ]
    }

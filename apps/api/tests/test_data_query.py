from datetime import UTC, datetime
from time import perf_counter

from fastapi.testclient import TestClient

from low_altitude_poc_api.app import create_app

TEST_PASSWORD = "local-test-password"
NOW = datetime(2026, 9, 3, 8, 0, tzinfo=UTC)


def login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200


def seed_telemetry(client: TestClient) -> None:
    response = client.post(
        "/api/telemetry/batches",
        json={
            "events": [
                {
                    "event_id": "query-001",
                    "drone_id": "GSH-001",
                    "longitude": 106.62,
                    "latitude": 26.64,
                    "altitude_m": 100,
                    "heading_deg": 90,
                    "speed_mps": 12,
                    "flight_state": "in_flight",
                    "source_time": "2026-09-03T07:59:50Z",
                    "source_type": "simulated",
                    "sortie_id": "SORTIE-001",
                },
                {
                    "event_id": "query-002",
                    "drone_id": "GSH-002",
                    "longitude": 106.63,
                    "latitude": 26.65,
                    "altitude_m": 110,
                    "heading_deg": 180,
                    "speed_mps": 10,
                    "flight_state": "in_flight",
                    "source_time": "2026-09-03T07:20:00Z",
                    "source_type": "real",
                    "sortie_id": "SORTIE-002",
                },
                {
                    "event_id": "query-old",
                    "drone_id": "GSH-OLD",
                    "longitude": 106.64,
                    "latitude": 26.66,
                    "altitude_m": 90,
                    "heading_deg": 0,
                    "speed_mps": 0,
                    "flight_state": "landed",
                    "source_time": "2026-09-03T05:00:00Z",
                    "source_type": "simulated",
                    "sortie_id": "SORTIE-OLD",
                },
            ]
        },
    )
    assert response.status_code == 202


def test_fixed_natural_language_questions_return_grounded_read_only_answers(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'query.db'}",
        demo_password=TEST_PASSWORD,
        clock=lambda: NOW,
    )
    with TestClient(app) as client:
        seed_telemetry(client)
        login(client, "situation-viewer")
        questions = {
            "最近1小时有多少架无人机？": ("drone_count", 2),
            "最近1小时有多少飞行架次？": ("sortie_count", 2),
            "最近1小时在线率是多少？": ("online_rate", 100),
            "最近30分钟有哪些无人机？": ("drone_list", 1),
        }
        for question, (intent, value) in questions.items():
            started = perf_counter()
            response = client.post("/api/data-query", json={"question": question})
            assert perf_counter() - started < 5
            assert response.status_code == 200
            payload = response.json()
            assert payload["plan"]["intent"] == intent
            assert payload["visualization"]["value"] == value
            assert payload["query_basis"] == {
                "time_range": {
                    "start": (
                        "2026-09-03T07:30:00Z"
                        if "30分钟" in question
                        else "2026-09-03T07:00:00Z"
                    ),
                    "end": "2026-09-03T08:00:00Z",
                },
                "data_sources": ["真实数据", "模拟数据"],
                "statistical_definition": payload["query_basis"][
                    "statistical_definition"
                ],
                "filters": {
                    "role": "态势查看者",
                    "source_type": "全部来源",
                },
            }
            assert payload["detail_entry"]["record_type"] == "无人机遥测"
            assert {
                record["event_id"] for record in payload["detail_entry"]["records"]
            } <= {"query-001", "query-002"}


def test_query_plan_is_whitelisted_and_enforces_role_time_and_source(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'query-security.db'}",
        demo_password=TEST_PASSWORD,
        clock=lambda: NOW,
    )
    with TestClient(app) as client:
        seed_telemetry(client)
        anonymous = client.post(
            "/api/data-query", json={"question": "最近1小时有多少架无人机？"}
        )
        login(client, "clue-reviewer")
        forbidden = client.post(
            "/api/data-query", json={"question": "最近1小时有多少架无人机？"}
        )
        client.post("/api/auth/logout")
        login(client, "situation-viewer")
        unsupported = client.post(
            "/api/data-query", json={"question": "删除所有无人机数据"}
        )
        simulated = client.post(
            "/api/data-query",
            json={
                "question": "最近1小时有多少架无人机？",
                "source_type": "simulated",
            },
        )

    assert anonymous.status_code == 401
    assert forbidden.status_code == 403
    assert unsupported.status_code == 422
    assert simulated.status_code == 200
    assert simulated.json()["visualization"]["value"] == 1
    assert simulated.json()["detail_entry"]["drone_ids"] == ["GSH-001"]
    assert simulated.json()["query_basis"]["data_sources"] == ["模拟数据"]

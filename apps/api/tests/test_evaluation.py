from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from low_altitude_poc_api.app import create_app
from low_altitude_poc_api.evaluation_runner import evaluate

TEST_PASSWORD = "local-test-password"
INFERENCE_TOKEN = "local-inference-token"


def login(client: TestClient, username: str = "platform-operator") -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200


def test_fixed_evaluation_set_covers_three_repeatable_anomaly_types(tmp_path):
    manifest = (
        Path(__file__).parents[3] / "tools/evaluation/v1/manifest.json"
    ).resolve()

    first = evaluate(manifest, tmp_path / "materials-first")
    second = evaluate(manifest, tmp_path / "materials-second")

    assert first.evaluation_set_version == "guanshanhu-ai-clues-v1"
    assert first.model_version == "deterministic-frame-intensity-v2"
    assert first.detection_rate == 1
    assert first.false_positive_count == 0
    assert first.inference_duration_ms > 0
    assert first.clue_display_latency_ms >= 0
    assert {
        (case.expected_anomaly_type, case.predicted_anomaly_type)
        for case in first.cases
    } == {
        ("suspected_traffic_accident", "suspected_traffic_accident"),
        ("suspected_fire", "suspected_fire"),
        ("suspected_crowd", "suspected_crowd"),
        (None, None),
    }
    assert [case.detected for case in first.cases] == [True, True, True, False]
    assert [case.matched_expected for case in first.cases] == [True] * 4
    assert [case.predicted_anomaly_type for case in first.cases] == [
        case.predicted_anomaly_type for case in second.cases
    ]


def test_inference_health_reports_unavailable_explicit_failure_and_timeout(tmp_path):
    current_time = [datetime(2026, 9, 3, 8, 0, tzinfo=UTC)]
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'health.db'}",
        demo_password=TEST_PASSWORD,
        inference_token=INFERENCE_TOKEN,
        clue_material_root=tmp_path / "materials",
        clock=lambda: current_time[0],
    )

    with TestClient(app) as client:
        login(client)
        unavailable = client.get("/api/inference/health")
        unauthorized = client.post(
            "/api/inference/health",
            json={
                "status": "healthy",
                "model_version": "deterministic-dark-region-v1",
            },
        )
        invalid_result = client.post(
            "/api/inference/results",
            headers={"X-Inference-Token": INFERENCE_TOKEN},
            json={
                "model_version": "deterministic-dark-region-v1",
            },
        )
        degraded = client.get("/api/inference/health")
        healthy_report = client.post(
            "/api/inference/health",
            headers={"X-Inference-Token": INFERENCE_TOKEN},
            json={
                "status": "healthy",
                "model_version": "deterministic-dark-region-v1",
            },
        )
        healthy = client.get("/api/inference/health")
        current_time[0] += timedelta(seconds=16)
        timed_out = client.get("/api/inference/health")
        clues_during_degradation = client.get("/api/ai-clues")

    assert unavailable.json()["reason"] == "推理进程不可达"
    assert unauthorized.status_code == 401
    assert invalid_result.status_code == 422
    assert degraded.json()["status"] == "degraded"
    assert degraded.json()["reason"] == "推理结果无效"
    assert healthy_report.status_code == 204
    assert healthy.json()["status"] == "healthy"
    assert timed_out.json()["status"] == "degraded"
    assert timed_out.json()["reason"] == "推理进程心跳超时"
    assert clues_during_degradation.json() == {"clues": []}

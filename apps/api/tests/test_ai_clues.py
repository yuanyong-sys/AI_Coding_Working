import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from low_altitude_poc_api.ai_clues import InferenceResult
from low_altitude_poc_api.app import create_app

TEST_PASSWORD = "local-test-password"
INFERENCE_TOKEN = "local-inference-token"


def login(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": TEST_PASSWORD},
    )
    assert response.status_code == 200


def inference_result() -> dict[str, object]:
    return {
        "result_id": "inference-fire-001",
        "anomaly_type": "suspected_fire",
        "confidence": 0.87,
        "source_time": "2026-09-03T06:30:05Z",
        "location": {"longitude": 106.6282, "latitude": 26.6467},
        "material_reference": "frames/inference-fire-001.jpg",
        "model_version": "deterministic-dark-region-v1",
        "source_type": "evaluation",
    }


def test_inference_contract_creates_reviewable_ai_anomaly_clue(tmp_path):
    material_root = tmp_path / "clue-materials"
    frame = material_root / "frames" / "inference-fire-001.jpg"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"controlled-local-frame")
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'clues.db'}",
        demo_password=TEST_PASSWORD,
        inference_token=INFERENCE_TOKEN,
        clue_material_root=material_root,
    )

    with TestClient(app) as client:
        unauthenticated_adapter = client.post(
            "/api/inference/results", json=inference_result()
        )
        accepted = client.post(
            "/api/inference/results",
            json=inference_result(),
            headers={"X-Inference-Token": INFERENCE_TOKEN},
        )

        login(client, "situation-viewer")
        viewer_denied = client.get("/api/ai-clues")
        client.post("/api/auth/logout")

        login(client, "clue-reviewer")
        clues = client.get("/api/ai-clues")
        material = client.get("/api/ai-clues/inference-fire-001/material")

    assert unauthenticated_adapter.status_code == 401
    assert accepted.status_code == 202
    assert accepted.json() == {
        "clue_id": "inference-fire-001",
        "status": "pending_review",
    }
    assert viewer_denied.status_code == 403
    assert clues.status_code == 200
    assert clues.json() == {
        "clues": [
            {
                **inference_result(),
                "clue_id": "inference-fire-001",
                "review_status": "pending_review",
            }
        ]
    }
    assert material.status_code == 200
    assert material.content == b"controlled-local-frame"


def test_inference_contract_rejects_material_outside_controlled_directory(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'invalid-reference.db'}",
        demo_password=TEST_PASSWORD,
        inference_token=INFERENCE_TOKEN,
        clue_material_root=Path(tmp_path / "clue-materials"),
    )
    invalid_result = {
        **inference_result(),
        "result_id": "inference-invalid-reference",
        "material_reference": "../outside.jpg",
    }

    with TestClient(app) as client:
        response = client.post(
            "/api/inference/results",
            json=invalid_result,
            headers={"X-Inference-Token": INFERENCE_TOKEN},
        )

    assert response.status_code == 422


def test_independent_worker_extracts_repeatable_clue_from_prerecorded_video(tmp_path):
    video = Path(__file__).parents[3] / "tools/fixtures/suspected-fire.mp4"
    material_root = tmp_path / "clue-materials"
    outputs = []

    for run in (1, 2):
        output = tmp_path / f"result-{run}.json"
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "low_altitude_poc_api.inference_worker",
                "--video",
                str(video),
                "--material-root",
                str(material_root),
                "--output",
                str(output),
                "--source-time",
                "2026-09-03T06:30:05Z",
                "--longitude",
                "106.6282",
                "--latitude",
                "26.6467",
            ],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            },
        )
        assert completed.returncode == 0, completed.stderr
        outputs.append(json.loads(output.read_text()))

    first = InferenceResult.model_validate(outputs[0])
    second = InferenceResult.model_validate(outputs[1])
    assert first == second
    assert first.anomaly_type == "suspected_fire"
    assert first.model_version == "deterministic-dark-region-v1"
    assert first.source_type == "evaluation"
    assert first.confidence == 0.87
    assert (material_root / first.material_reference).is_file()

    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'worker-contract.db'}",
        demo_password=TEST_PASSWORD,
        inference_token=INFERENCE_TOKEN,
        clue_material_root=material_root,
    )
    with TestClient(app) as client:
        accepted = client.post(
            "/api/inference/results",
            json=outputs[0],
            headers={"X-Inference-Token": INFERENCE_TOKEN},
        )
    assert accepted.status_code == 202

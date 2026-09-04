from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Literal
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from pydantic import BaseModel

from low_altitude_poc_api.ai_clues import InferenceResult
from low_altitude_poc_api.inference_worker import (
    MODEL_VERSION,
    NoClueDetected,
    infer_video,
    submit_health,
    submit_result,
)


class EvaluationCase(BaseModel):
    case_id: str
    video: str
    expected_anomaly_type: (
        Literal["suspected_traffic_accident", "suspected_fire", "suspected_crowd"]
        | None
    )
    source_time: str
    longitude: float
    latitude: float


class EvaluationManifest(BaseModel):
    evaluation_set_version: str
    cases: list[EvaluationCase]


class EvaluationCaseResult(BaseModel):
    case_id: str
    expected_anomaly_type: str | None
    predicted_anomaly_type: str | None
    detected: bool
    matched_expected: bool
    inference_duration_ms: float
    clue_display_latency_ms: float
    error: str | None = None


class EvaluationReport(BaseModel):
    evaluation_set_version: str
    model_version: str
    detection_rate: float
    false_positive_count: int
    inference_duration_ms: float
    clue_display_latency_ms: float
    cases: list[EvaluationCaseResult]


def wait_until_display_ready(api_url: str, token: str, result_id: str) -> None:
    deadline = time.monotonic() + 5
    url = f"{api_url.rstrip('/')}/api/inference/results/{result_id}/display-ready"
    while time.monotonic() < deadline:
        try:
            with urlopen(
                Request(url, headers={"X-Inference-Token": token}), timeout=1
            ) as response:
                if response.status == 204:
                    return
        except HTTPError as reason:
            if reason.code != 404:
                raise
        time.sleep(0.05)
    raise TimeoutError("线索未在时限内达到展示就绪状态")


def evaluate(
    manifest_path: Path,
    material_root: Path,
    *,
    api_url: str | None = None,
    token: str | None = None,
) -> EvaluationReport:
    manifest = EvaluationManifest.model_validate_json(manifest_path.read_text())
    results: list[EvaluationCaseResult] = []
    for case in manifest.cases:
        started = time.perf_counter()
        inference: InferenceResult | None = None
        error: str | None = None
        try:
            inference = infer_video(
                (manifest_path.parent / case.video).resolve(),
                material_root,
                source_time=case.source_time,
                longitude=case.longitude,
                latitude=case.latitude,
                source_type="evaluation",
            )
        except NoClueDetected:
            pass
        except (OSError, RuntimeError) as reason:
            error = str(reason)
        inferred_at = time.perf_counter()
        if inference is not None and api_url is not None:
            if token is None:
                raise ValueError("提交评测结果需要推理令牌")
            try:
                submit_result(api_url, token, inference)
                wait_until_display_ready(api_url, token, inference.result_id)
            except OSError as reason:
                error = f"推理结果提交失败：{reason}"
                inference = None
        displayed_at = time.perf_counter()
        predicted = inference.anomaly_type if inference else None
        detected = predicted is not None
        results.append(
            EvaluationCaseResult(
                case_id=case.case_id,
                expected_anomaly_type=case.expected_anomaly_type,
                predicted_anomaly_type=predicted,
                detected=detected,
                matched_expected=predicted == case.expected_anomaly_type,
                inference_duration_ms=round((inferred_at - started) * 1000, 2),
                clue_display_latency_ms=round((displayed_at - inferred_at) * 1000, 2),
                error=error,
            )
        )
    positive_results = [
        result for result in results if result.expected_anomaly_type is not None
    ]
    detected = sum(result.matched_expected for result in positive_results)
    false_positives = sum(
        result.expected_anomaly_type is None
        and result.predicted_anomaly_type is not None
        for result in results
    )
    if api_url is not None and token is not None:
        failures = [
            result for result in results if result.error or not result.matched_expected
        ]
        submit_health(
            api_url,
            token,
            status="degraded" if failures else "healthy",
            reason="固定评测存在无效或不符合预期的推理结果" if failures else None,
        )
    return EvaluationReport(
        evaluation_set_version=manifest.evaluation_set_version,
        model_version=MODEL_VERSION,
        detection_rate=(
            round(detected / len(positive_results), 4) if positive_results else 0
        ),
        false_positive_count=false_positives,
        inference_duration_ms=round(
            sum(result.inference_duration_ms for result in results), 2
        ),
        clue_display_latency_ms=round(
            sum(result.clue_display_latency_ms for result in results), 2
        ),
        cases=results,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="固定评测集 AI异常线索评测")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--material-root", type=Path, default=Path("var/clue-materials")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("var/evaluation/latest.json")
    )
    parser.add_argument("--api-url")
    parser.add_argument("--token")
    args = parser.parse_args()
    report = evaluate(
        args.manifest,
        args.material_root,
        api_url=args.api_url,
        token=args.token,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.model_dump_json(indent=2))
    print(report.model_dump_json())


if __name__ == "__main__":
    main()

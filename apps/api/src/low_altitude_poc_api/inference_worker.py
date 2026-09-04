from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from urllib.request import Request, urlopen

from low_altitude_poc_api.ai_clues import ClueLocation, InferenceResult

MODEL_VERSION = "deterministic-frame-intensity-v2"


class NoClueDetected(RuntimeError):
    pass


def run_ffmpeg(*arguments: str, capture_output: bool = False) -> bytes:
    completed = subprocess.run(
        ["ffmpeg", "-v", "error", *arguments],
        check=True,
        capture_output=capture_output,
    )
    return completed.stdout


def infer_video(
    video: Path,
    material_root: Path,
    *,
    source_time: str,
    longitude: float,
    latitude: float,
    source_type: str,
) -> InferenceResult:
    if not video.is_file():
        raise FileNotFoundError(f"预录视频不存在：{video}")
    result_id = f"inference-{hashlib.sha256(video.read_bytes()).hexdigest()[:16]}"
    reference = f"frames/{result_id}.jpg"
    frame = material_root / reference
    frame.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg("-y", "-i", str(video), "-frames:v", "1", str(frame))
    grayscale = run_ffmpeg(
        "-i",
        str(video),
        "-vf",
        "scale=64:36,format=gray",
        "-frames:v",
        "1",
        "-f",
        "rawvideo",
        "pipe:1",
        capture_output=True,
    )
    dark_ratio = sum(pixel < 50 for pixel in grayscale) / len(grayscale)
    mean_brightness = sum(grayscale) / len(grayscale)
    if 165 <= mean_brightness < 220:
        raise NoClueDetected("固定模型未在该预录视频中识别到 AI异常线索")
    anomaly_type = (
        "suspected_fire"
        if mean_brightness < 80
        else "suspected_traffic_accident"
        if mean_brightness < 170
        else "suspected_crowd"
    )
    confidence = round(min(0.99, 0.65 + dark_ratio * 0.5), 2)
    return InferenceResult(
        result_id=result_id,
        anomaly_type=anomaly_type,
        confidence=confidence,
        source_time=source_time,
        location=ClueLocation(longitude=longitude, latitude=latitude),
        material_reference=reference,
        model_version=MODEL_VERSION,
        source_type=source_type,
    )


def submit_result(api_url: str, token: str, result: InferenceResult) -> None:
    request = Request(
        f"{api_url.rstrip('/')}/api/inference/results",
        data=result.model_dump_json().encode(),
        headers={
            "Content-Type": "application/json",
            "X-Inference-Token": token,
        },
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        if response.status != 202:
            raise RuntimeError(f"业务平台拒绝推理结果：HTTP {response.status}")


def submit_health(
    api_url: str, token: str, *, status: str, reason: str | None = None
) -> None:
    request = Request(
        f"{api_url.rstrip('/')}/api/inference/health",
        data=json.dumps(
            {"status": status, "reason": reason, "model_version": MODEL_VERSION}
        ).encode(),
        headers={"Content-Type": "application/json", "X-Inference-Token": token},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        if response.status != 204:
            raise RuntimeError(f"业务平台拒绝推理状态：HTTP {response.status}")


def main() -> None:
    parser = argparse.ArgumentParser(description="固定预录视频 AI异常线索推理进程")
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument(
        "--material-root", type=Path, default=Path("var/clue-materials")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("var/inference-results/latest.json")
    )
    parser.add_argument("--source-time", required=True)
    parser.add_argument("--longitude", type=float, required=True)
    parser.add_argument("--latitude", type=float, required=True)
    parser.add_argument(
        "--source-type", choices=("simulated", "evaluation"), default="evaluation"
    )
    parser.add_argument("--api-url")
    parser.add_argument("--token", default=os.getenv("LOW_ALTITUDE_INFERENCE_TOKEN"))
    args = parser.parse_args()

    result = infer_video(
        args.video,
        args.material_root,
        source_time=args.source_time,
        longitude=args.longitude,
        latitude=args.latitude,
        source_type=args.source_type,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(result.model_dump_json(indent=2))
    if args.api_url:
        if not args.token:
            parser.error("--api-url requires --token or LOW_ALTITUDE_INFERENCE_TOKEN")
        submit_result(args.api_url, args.token, result)
        submit_health(args.api_url, args.token, status="healthy")
    print(result.model_dump_json())


if __name__ == "__main__":
    main()

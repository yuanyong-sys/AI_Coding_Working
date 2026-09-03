#!/usr/bin/env python3
"""Send the deterministic normal-flight scenario to the telemetry API."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from urllib.request import Request, urlopen

SCENARIO_PATH = Path(__file__).with_name("scenarios") / "normal-flight.json"


def send_event(api_url: str, event: dict[str, object]) -> dict[str, object]:
    request = Request(
        f"{api_url.rstrip('/')}/api/telemetry/batches",
        data=json.dumps({"events": [event]}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def main() -> None:
    parser = argparse.ArgumentParser(description="运行确定性正常飞行模拟场景")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args()

    events = json.loads(SCENARIO_PATH.read_text())["events"]
    events.extend(
        {
            "event_id": f"standard-fleet-{index:02d}",
            "drone_id": f"UAV-GSH-DEMO-{index:02d}",
            "longitude": 106.58 + index * 0.004,
            "latitude": 26.61 + index * 0.002,
            "altitude_m": 60 + index,
            "heading_deg": 90,
            "speed_mps": 10,
            "flight_state": "flying" if index <= 12 else "waiting",
            "source_time": "2026-09-02T06:30:00Z",
            "source_type": "simulated",
            "sortie_id": f"GSH-STANDARD-SORTIE-{index:02d}",
        }
        for index in range(2, 21)
    )
    for index, event in enumerate(events, start=1):
        result = send_event(args.api_url, event)
        print(
            f"[{index:02d}/{len(events):02d}] {event['drone_id']} "
            f"{event['source_time']} accepted={result['accepted']}"
        )
        if index < len(events):
            time.sleep(args.interval)


if __name__ == "__main__":
    main()

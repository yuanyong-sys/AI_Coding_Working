export interface PlannedRoutePoint {
  longitude: number;
  latitude: number;
  altitude_m: number;
  time: string;
}

export interface PreflightViolation {
  outcome: "entered_no_fly_zone" | "outside_geofence";
  rule_id: string;
  rule_version: number;
  position: PlannedRoutePoint;
  reason: string;
}

export interface PreflightResult {
  result: "passed" | "entered_no_fly_zone" | "outside_geofence";
  violations: PreflightViolation[];
}

export async function validatePlannedRoute(
  points: PlannedRoutePoint[],
): Promise<PreflightResult> {
  const response = await fetch("/api/preflight-validations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ coordinate_reference: "WGS84", points }),
  });
  if (!response.ok) {
    const problem = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(problem?.detail ?? "计划航线校验失败");
  }
  return response.json() as Promise<PreflightResult>;
}

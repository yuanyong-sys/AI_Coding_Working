export type SpatialRuleType = "no_fly_zone" | "geofence";

export interface SpatialRuleDraft {
  rule_id: string;
  name: string;
  rule_type: SpatialRuleType;
  geometry: { type: "Polygon"; coordinates: number[][][] };
  min_altitude_m: number;
  max_altitude_m: number;
  valid_from: string;
  valid_to: string;
  source: string;
  coordinate_reference: "WGS84";
}

export interface SpatialRuleVersion extends SpatialRuleDraft {
  version: number;
  actor: string;
  published_at: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    const problem = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(problem?.detail ?? "空间规则操作失败");
  }
  return response.json() as Promise<T>;
}

export async function fetchActiveSpatialRules(): Promise<SpatialRuleVersion[]> {
  return (
    await request<{ rules: SpatialRuleVersion[] }>("/api/spatial-rules/active")
  ).rules;
}

export async function saveSpatialRuleDraft(
  draft: SpatialRuleDraft,
): Promise<SpatialRuleDraft> {
  return request("/api/spatial-rules/drafts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(draft),
  });
}

export async function publishSpatialRule(
  ruleId: string,
): Promise<SpatialRuleVersion> {
  return request(`/api/spatial-rules/drafts/${ruleId}/publish`, {
    method: "POST",
  });
}

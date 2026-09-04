export type AIClue = {
  clue_id: string;
  result_id: string;
  anomaly_type:
    "suspected_traffic_accident" | "suspected_fire" | "suspected_crowd";
  confidence: number;
  source_time: string;
  location: { longitude: number; latitude: number };
  material_reference: string;
  model_version: string;
  source_type: "simulated" | "evaluation";
  review_status: ReviewStatus;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_history: ReviewHistoryItem[];
};

export type ReviewStatus = "confirmed" | "false_positive" | "pending_review";

export type ReviewHistoryItem = {
  review_status: ReviewStatus;
  reviewed_by: string;
  reviewed_at: string;
};

export type InferenceHealth = {
  status: "healthy" | "degraded";
  reason: string | null;
  model_version: string | null;
  reported_at: string | null;
};

export const reviewStatusLabels: Record<ReviewStatus, string> = {
  confirmed: "确认",
  false_positive: "误报",
  pending_review: "待复核",
};

const anomalyTypeLabels: Record<AIClue["anomaly_type"], string> = {
  suspected_traffic_accident: "疑似交通事故",
  suspected_fire: "疑似烟火",
  suspected_crowd: "人员聚集",
};

export function formatAnomalyType(type: AIClue["anomaly_type"]): string {
  return anomalyTypeLabels[type];
}

export function formatClueSource(source: AIClue["source_type"]): string {
  return source === "evaluation" ? "评测来源" : "模拟来源";
}

export async function fetchAIClues(): Promise<AIClue[]> {
  const response = await fetch("/api/ai-clues");
  if (!response.ok) throw new Error("AI异常线索读取失败");
  return ((await response.json()) as { clues: AIClue[] }).clues;
}

export async function fetchInferenceHealth(): Promise<InferenceHealth> {
  const response = await fetch("/api/inference/health");
  if (!response.ok) throw new Error("推理状态读取失败");
  return (await response.json()) as InferenceHealth;
}

export function clueMaterialUrl(clueId: string): string {
  return `/api/ai-clues/${encodeURIComponent(clueId)}/material`;
}

export async function updateAIClueReview(
  clueId: string,
  reviewStatus: ReviewStatus,
): Promise<AIClue> {
  const response = await fetch(
    `/api/ai-clues/${encodeURIComponent(clueId)}/review`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ review_status: reviewStatus }),
    },
  );
  if (!response.ok) throw new Error("研判结果保存失败");
  return (await response.json()) as AIClue;
}

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
  review_status: "pending_review";
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

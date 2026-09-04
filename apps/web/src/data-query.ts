export type QueryIntent =
  "drone_list" | "drone_count" | "sortie_count" | "online_rate";

export type DataQueryResponse = {
  answer: string;
  plan: {
    intent: QueryIntent;
    start_time: string;
    end_time: string;
    source_type: "real" | "simulated" | null;
  };
  query_basis: {
    time_range: { start: string; end: string };
    data_sources: ("真实数据" | "模拟数据")[];
    statistical_definition: string;
    filters: Record<string, string>;
  };
  detail_entry: {
    record_type: "无人机遥测";
    drone_ids: string[];
    records: {
      event_id: string;
      drone_id: string;
      source_time: string;
      platform_received_time: string;
      source_type: "real" | "simulated";
    }[];
  };
  visualization: {
    type: "map" | "chart";
    metric: QueryIntent;
    value: number | null;
    drone_ids: string[];
  };
};

export async function runDataQuery(
  question: string,
  sourceType: "real" | "simulated" | null,
): Promise<DataQueryResponse> {
  const response = await fetch("/api/data-query", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, source_type: sourceType }),
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as {
      detail?: string;
    } | null;
    throw new Error(payload?.detail ?? "数据智能查询失败");
  }
  return (await response.json()) as DataQueryResponse;
}

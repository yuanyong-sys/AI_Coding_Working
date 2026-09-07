<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";

import { formatAnomalyType, type AIClue } from "@/ai-clues";
import type { AuthenticatedUser } from "@/auth";
import type { SpatialRuleVersion } from "@/spatial-rules";
import type {
  DroneSnapshot,
  IncursionAlert,
  SituationMetrics,
} from "@/situation";
import SituationMap from "@/components/SituationMap.vue";
import SmartQueryDialog from "@/components/SmartQueryDialog.vue";

const props = defineProps<{
  user: AuthenticatedUser;
  drones: DroneSnapshot[];
  metrics: SituationMetrics;
  alerts: IncursionAlert[];
  clues: AIClue[];
  spatialRules: SpatialRuleVersion[];
  loading: boolean;
  error: string;
}>();

defineEmits<{ operations: []; logout: [] }>();

const selectedDrone = ref<DroneSnapshot | null>(null);
const updateTime = ref(new Date());
const timer = window.setInterval(() => (updateTime.value = new Date()), 1_000);

function handleEscape(event: KeyboardEvent) {
  if (event.key === "Escape") selectedDrone.value = null;
}

window.addEventListener("keydown", handleEscape);
onBeforeUnmount(() => {
  window.clearInterval(timer);
  window.removeEventListener("keydown", handleEscape);
});

const activeAlerts = computed(
  () => props.alerts.filter((alert) => !alert.ended_at).length,
);
const pendingClues = computed(
  () =>
    props.clues.filter((clue) => clue.review_status === "pending_review")
      .length,
);
const demoDeviceLedger = {
  batteryPercent: 82,
  availableMinutes: 34,
  organization: "观山湖交管巡查组",
  task: "观山湖重点道路常态化巡查",
};
const selectedDevice = computed(() => {
  if (!selectedDrone.value) return null;
  return demoDeviceLedger;
});

function flightStateLabel(state: string): string {
  return (
    {
      pending: "待飞",
      flying: "巡查中",
      returning: "返航中",
      offline: "离线",
    }[state] ?? state
  );
}
</script>

<template>
  <main class="leadership-shell">
    <header class="leadership-header">
      <div>
        <small>贵阳 · 观山湖</small>
        <p>无人机低空智慧调度平台</p>
      </div>
      <div class="leadership-title">
        <small>公安交管常态化巡检</small>
        <h1>低空态势一张图</h1>
      </div>
      <div class="leadership-actions">
        <span><i></i>{{ error ? "链路异常" : "运行正常" }}</span>
        <button type="button" @click="$emit('operations')">
          进入业务操作台
        </button>
        <button type="button" class="quiet-button" @click="$emit('logout')">
          退出
        </button>
      </div>
    </header>

    <section class="leadership-grid">
      <aside class="leadership-summary" aria-label="巡查工作摘要">
        <article>
          <span>当日飞行架次</span>
          <strong>{{ metrics.flight_sorties }}</strong>
          <small>巡检成果持续汇总</small>
        </article>
        <article>
          <span>正在巡查</span>
          <strong>{{ metrics.in_flight_count }}</strong>
          <small>运行状态正常</small>
        </article>
        <article>
          <span>巡检无人机在线率</span>
          <strong>{{ metrics.online_rate.toFixed(0) }}%</strong>
          <small>近 {{ metrics.observation_window_seconds }} 秒</small>
        </article>
        <article>
          <span>需要关注</span>
          <strong class="warning">{{ activeAlerts + pendingClues }}</strong>
          <small>越界告警与待复核线索</small>
        </article>
      </aside>

      <section class="leadership-map-stage" aria-label="观山湖区巡查态势">
        <SituationMap
          leadership
          :drones="drones"
          :spatial-rules="spatialRules"
          @select-drone="selectedDrone = $event"
        />
        <p v-if="loading" class="map-state" role="status">正在汇聚巡查态势…</p>
        <p v-else-if="error" class="map-state map-state--error" role="alert">
          {{ error }}
        </p>
      </section>

      <aside class="leadership-risks" aria-label="风险动态">
        <div class="rail-heading">
          <div>
            <small>实时汇总</small>
            <h2>风险动态</h2>
          </div>
          <strong>{{ activeAlerts + pendingClues }} 项待关注</strong>
        </div>
        <article v-for="alert in alerts.slice(0, 3)" :key="`alert-${alert.id}`">
          <span>{{ alert.ended_at ? "已结束" : "持续中" }}</span>
          <strong>{{ alert.reason }}</strong>
          <small>{{ alert.drone_id }} · 越界告警</small>
        </article>
        <article v-for="clue in clues.slice(0, 3)" :key="clue.clue_id">
          <span>{{
            clue.review_status === "pending_review" ? "待复核" : "已研判"
          }}</span>
          <strong>{{ formatAnomalyType(clue.anomaly_type) }}</strong>
          <small
            >AI异常线索 · 置信度
            {{ (clue.confidence * 100).toFixed(0) }}%</small
          >
        </article>
        <p v-if="!alerts.length && !clues.length" class="empty-risk">
          当前没有需要关注的风险
        </p>
        <footer>
          <span>数据来源：平台业务记录</span>
          <time :datetime="updateTime.toISOString()">
            更新 {{ updateTime.toLocaleTimeString("zh-CN", { hour12: false }) }}
          </time>
        </footer>
      </aside>
    </section>

    <section
      v-if="selectedDrone && selectedDevice"
      class="device-dialog"
      role="dialog"
      aria-label="巡检无人机设备信息"
    >
      <header>
        <div>
          <small>巡检无人机</small>
          <h2>{{ selectedDrone.drone_id }}</h2>
        </div>
        <button
          type="button"
          aria-label="关闭设备信息"
          @click="selectedDrone = null"
        >
          ×
        </button>
      </header>
      <dl>
        <div>
          <dt>运行状态</dt>
          <dd>{{ flightStateLabel(selectedDrone.flight_state) }}</dd>
        </div>
        <div>
          <dt>剩余电量</dt>
          <dd>{{ selectedDevice.batteryPercent }}%</dd>
        </div>
        <div>
          <dt>预计可用时长</dt>
          <dd>{{ selectedDevice.availableMinutes }} 分钟</dd>
        </div>
        <div>
          <dt>所属单位</dt>
          <dd>{{ selectedDevice.organization }}</dd>
        </div>
        <div class="full">
          <dt>当前任务</dt>
          <dd>{{ selectedDevice.task }}</dd>
        </div>
      </dl>
      <p class="device-basis">模拟数据 · POC 演示设备台账</p>
    </section>

    <SmartQueryDialog />
  </main>
</template>

<style scoped>
.leadership-shell {
  min-height: 100vh;
  padding: 1.25rem 1.5rem 4.5rem;
  background:
    radial-gradient(circle at 50% 25%, #10343b 0, transparent 42%), #061012;
  color: #e4f4f1;
  overflow: hidden;
}
.leadership-header {
  height: 4.5rem;
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  border-bottom: 1px solid rgba(85, 226, 213, 0.28);
}
.leadership-header small {
  color: #55e2d5;
  font-size: 0.65rem;
  letter-spacing: 0.16em;
}
.leadership-header p,
.leadership-header h1 {
  margin: 0.2rem 0 0;
}
.leadership-header p {
  font-size: 1rem;
  font-weight: 650;
}
.leadership-title {
  text-align: center;
}
.leadership-title h1 {
  font-size: 1.7rem;
  letter-spacing: 0.12em;
}
.leadership-actions {
  justify-self: end;
  display: flex;
  align-items: center;
  gap: 0.65rem;
}
.leadership-actions span {
  color: #8fe3b4;
  font-size: 0.72rem;
}
.leadership-actions i {
  display: inline-block;
  width: 0.45rem;
  height: 0.45rem;
  margin-right: 0.4rem;
  border-radius: 50%;
  background: #62e39c;
  box-shadow: 0 0 0.8rem #62e39c;
}
.leadership-actions button {
  padding: 0.55rem 0.75rem;
  border: 1px solid rgba(85, 226, 213, 0.36);
  background: rgba(85, 226, 213, 0.1);
  color: #dffaf6;
  cursor: pointer;
}
.leadership-actions .quiet-button {
  background: transparent;
  color: #799d9a;
}
.leadership-grid {
  height: calc(100vh - 7rem);
  display: grid;
  grid-template-columns: 15rem minmax(28rem, 1fr) 18rem;
  gap: 0.75rem;
  padding-top: 0.75rem;
}
.leadership-summary,
.leadership-risks {
  border: 1px solid rgba(85, 226, 213, 0.2);
  background: rgba(7, 26, 30, 0.9);
}
.leadership-summary {
  display: grid;
  grid-template-rows: repeat(4, 1fr);
}
.leadership-summary article {
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 1rem;
  border-bottom: 1px solid rgba(85, 226, 213, 0.13);
}
.leadership-summary span,
.leadership-summary small {
  color: #789c99;
  font-size: 0.7rem;
}
.leadership-summary strong {
  margin: 0.35rem 0;
  font-size: 2.1rem;
}
.leadership-summary .warning {
  color: #f7b955;
}
.leadership-map-stage {
  position: relative;
  min-width: 0;
  border: 1px solid rgba(85, 226, 213, 0.28);
}
.leadership-map-stage :deep(.situation-map) {
  height: 100%;
  border: 0;
}
.map-state {
  position: absolute;
  left: 1rem;
  top: 4rem;
  z-index: 8;
  padding: 0.6rem 0.8rem;
  background: rgba(6, 16, 18, 0.88);
  color: #9dbbb8;
  font-size: 0.72rem;
}
.map-state--error {
  color: #ff8993;
}
.leadership-risks {
  display: flex;
  flex-direction: column;
  padding: 1rem;
  overflow: auto;
}
.rail-heading {
  display: flex;
  justify-content: space-between;
  padding-bottom: 0.8rem;
  border-bottom: 1px solid rgba(85, 226, 213, 0.18);
}
.rail-heading h2 {
  margin: 0.2rem 0 0;
  font-size: 1rem;
}
.rail-heading small,
.rail-heading strong {
  color: #55e2d5;
  font-size: 0.65rem;
}
.leadership-risks article {
  padding: 0.85rem 0;
  border-bottom: 1px solid rgba(85, 226, 213, 0.1);
}
.leadership-risks article span {
  color: #f7b955;
  font-size: 0.62rem;
}
.leadership-risks article strong,
.leadership-risks article small {
  display: block;
}
.leadership-risks article strong {
  margin: 0.35rem 0;
  font-size: 0.78rem;
}
.leadership-risks article small,
.empty-risk,
.leadership-risks footer {
  color: #789c99;
  font-size: 0.65rem;
}
.empty-risk {
  margin: auto 0;
  text-align: center;
}
.leadership-risks footer {
  display: grid;
  gap: 0.25rem;
  margin-top: auto;
  padding-top: 1rem;
}
.device-dialog {
  position: fixed;
  z-index: 25;
  left: 50%;
  top: 50%;
  width: min(21rem, calc(100vw - 2rem));
  transform: translate(-50%, -50%);
  border: 1px solid rgba(85, 226, 213, 0.48);
  background: #081d22;
  box-shadow: 0 1.5rem 5rem rgba(0, 0, 0, 0.58);
}
.device-dialog header {
  display: flex;
  justify-content: space-between;
  padding: 1rem;
  border-bottom: 1px solid rgba(85, 226, 213, 0.18);
}
.device-dialog h2 {
  margin: 0.15rem 0 0;
  font-size: 1rem;
}
.device-dialog small,
.device-dialog dt {
  color: #789c99;
  font-size: 0.65rem;
}
.device-dialog header button {
  border: 0;
  background: transparent;
  color: #9dbbb8;
  cursor: pointer;
  font-size: 1.3rem;
}
.device-dialog dl {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.8rem;
  margin: 0;
  padding: 1rem;
}
.device-dialog dd {
  margin: 0.2rem 0 0;
  font-size: 0.75rem;
}
.device-dialog .full {
  grid-column: 1/-1;
}
.device-basis {
  margin: 0;
  padding: 0 1rem 1rem;
  color: #789c99;
  font-size: 0.62rem;
}
button:focus-visible {
  outline: 2px solid #55e2d5;
  outline-offset: 2px;
}
@media (max-width: 70rem) {
  .leadership-grid {
    grid-template-columns: 13rem 1fr;
  }
  .leadership-risks {
    display: none;
  }
}
@media (max-width: 48rem) {
  .leadership-shell {
    overflow: auto;
  }
  .leadership-header {
    grid-template-columns: 1fr auto;
  }
  .leadership-title {
    display: none;
  }
  .leadership-actions span {
    display: none;
  }
  .leadership-grid {
    height: auto;
    grid-template-columns: 1fr;
  }
  .leadership-summary {
    grid-template-columns: 1fr 1fr;
    grid-template-rows: auto auto;
  }
  .leadership-map-stage {
    height: 32rem;
  }
}
</style>

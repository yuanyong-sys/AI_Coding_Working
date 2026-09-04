<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";

import { fetchSession, login, logout, type AuthenticatedUser } from "@/auth";
import SituationMap from "@/components/SituationMap.vue";
import {
  connectSituationEvents,
  fetchSituationSnapshot,
  formatSourceTime,
  type DroneSnapshot,
  type IncursionAlert,
  type SituationMetrics,
} from "@/situation";
import {
  fetchActiveSpatialRules,
  publishSpatialRule,
  saveSpatialRuleDraft,
  type SpatialRuleDraft,
  type SpatialRuleVersion,
} from "@/spatial-rules";
import {
  validatePlannedRoute,
  type PlannedRoutePoint,
  type PreflightResult,
} from "@/preflight";

const drones = ref<DroneSnapshot[]>([]);
const incursionAlerts = ref<IncursionAlert[]>([]);
const selectedIncursionAlert = ref<IncursionAlert | null>(null);
const loading = ref(true);
const error = ref("");
const user = ref<AuthenticatedUser | null>(null);
const authReady = ref(false);
const username = ref("situation-viewer");
const password = ref("");
const loginError = ref("");
const loginPending = ref(false);
const activeView = ref("运行态势");
const spatialRules = ref<SpatialRuleVersion[]>([]);
const spatialRuleStatus = ref("");
const spatialRuleError = ref("");
const preflightResult = ref<PreflightResult | null>(null);
const preflightError = ref("");
let preflightRequestSequence = 0;
const plannedRoute = reactive<PlannedRoutePoint[]>([
  {
    longitude: 106.6,
    latitude: 26.645,
    altitude_m: 100,
    time: "2026-09-03T01:00:00Z",
  },
  {
    longitude: 106.65,
    latitude: 26.645,
    altitude_m: 100,
    time: "2026-09-03T01:01:00Z",
  },
]);
const spatialDraft = reactive<SpatialRuleDraft>({
  rule_id: "GSH-NFZ-001",
  name: "观山湖核心禁飞区",
  rule_type: "no_fly_zone",
  geometry: {
    type: "Polygon",
    coordinates: [
      [
        [106.61, 26.63],
        [106.64, 26.63],
        [106.64, 26.66],
        [106.61, 26.66],
        [106.61, 26.63],
      ],
    ],
  },
  min_altitude_m: 60,
  max_altitude_m: 180,
  valid_from: "2026-09-03T00:00:00Z",
  valid_to: "2027-09-30T00:00:00Z",
  source: "观山湖低空运行 POC 配置",
  coordinate_reference: "WGS84",
});
const geometryText = ref(JSON.stringify(spatialDraft.geometry, null, 2));
const cursor = ref(0);
const snapshotMetrics = ref<SituationMetrics | null>(null);
const clockNow = ref(Date.now());
const displayedDrones = computed(() =>
  drones.value.map((drone) => {
    const delaySeconds = Math.max(
      0,
      (clockNow.value - new Date(drone.platform_received_time).getTime()) /
        1000,
    );
    return {
      ...drone,
      data_status:
        delaySeconds > 30
          ? ("offline" as const)
          : delaySeconds > 10
            ? ("delayed" as const)
            : ("current" as const),
    };
  }),
);
const displayedMetrics = computed(() => {
  const onlineDrones = displayedDrones.value.filter(
    (drone) => drone.data_status !== "offline",
  );
  return {
    flight_sorties:
      snapshotMetrics.value?.flight_sorties ?? displayedDrones.value.length,
    online_rate: displayedDrones.value.length
      ? (onlineDrones.length / displayedDrones.value.length) * 100
      : 0,
    in_flight_count: onlineDrones.filter(
      (drone) => drone.flight_state === "flying",
    ).length,
    telemetry_delay_seconds: displayedDrones.value.length
      ? Math.max(
          ...displayedDrones.value.map(
            (drone) =>
              (clockNow.value -
                new Date(drone.platform_received_time).getTime()) /
              1000,
          ),
        )
      : 0,
    source_composition: snapshotMetrics.value?.source_composition ?? {
      real: 0,
      simulated: 0,
    },
    observation_window_seconds:
      snapshotMetrics.value?.observation_window_seconds ?? 30,
  };
});
const selectedDrone = computed(() => displayedDrones.value[0]);
const visibleIncursionAlert = computed(() =>
  activeView.value === "越界告警" ? selectedIncursionAlert.value : null,
);
const displayedSpatialRules = computed(() => {
  const selectedRule = visibleIncursionAlert.value?.rule_snapshot;
  if (!selectedRule) return spatialRules.value;
  return [
    ...spatialRules.value.filter(
      (rule) => rule.rule_id !== selectedRule.rule_id,
    ),
    selectedRule,
  ];
});
const navigation = computed(() => {
  const capabilities = new Set(user.value?.capabilities ?? []);
  return [
    { label: "运行态势", visible: capabilities.has("situation:read") },
    { label: "航前规则校验", visible: capabilities.has("situation:read") },
    { label: "越界告警", visible: capabilities.has("situation:read") },
    { label: "数据智能", visible: capabilities.has("query:read") },
    { label: "AI异常线索研判", visible: capabilities.has("clue:review") },
    { label: "空间规则", visible: capabilities.has("spatial:manage") },
  ].filter((item) => item.visible);
});
let freshnessTimer: ReturnType<typeof setInterval> | undefined;
let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
let spatialRulesTimer: ReturnType<typeof setInterval> | undefined;
let situationSocket: WebSocket | undefined;
let situationRefreshRunning = false;
let situationRefreshDirty = false;

async function refreshSituation(): Promise<boolean> {
  try {
    const snapshot = await fetchSituationSnapshot();
    drones.value = snapshot.drones;
    incursionAlerts.value = snapshot.incursion_alerts;
    snapshotMetrics.value = snapshot.metrics;
    cursor.value = snapshot.cursor;
    error.value = "";
    return true;
  } catch (reason) {
    error.value =
      reason instanceof Error ? reason.message : "态势快照暂时不可用";
    return false;
  } finally {
    loading.value = false;
  }
}

async function refreshSpatialRules() {
  try {
    spatialRules.value = await fetchActiveSpatialRules();
  } catch (reason) {
    spatialRuleError.value =
      reason instanceof Error ? reason.message : "空间规则读取失败";
  }
}

function connectSituationStream() {
  situationSocket = connectSituationEvents(
    cursor.value,
    () => {
      situationRefreshDirty = true;
      if (situationRefreshRunning) return;
      situationRefreshRunning = true;
      void (async () => {
        try {
          while (situationRefreshDirty) {
            situationRefreshDirty = false;
            if (!(await refreshSituation())) {
              situationSocket?.close();
              break;
            }
          }
        } finally {
          situationRefreshRunning = false;
        }
      })();
    },
    () => {
      if (!user.value) return;
      reconnectTimer = setTimeout(connectSituationStream, 500);
    },
  );
}

async function startSituationRefresh() {
  await refreshSituation();
  await refreshSpatialRules();
  connectSituationStream();
  freshnessTimer = setInterval(() => {
    clockNow.value = Date.now();
  }, 1000);
  spatialRulesTimer = setInterval(refreshSpatialRules, 5000);
}

function toApiTime(value: string): string {
  return value.length === 16 ? new Date(value).toISOString() : value;
}

async function saveDraft() {
  spatialRuleError.value = "";
  spatialRuleStatus.value = "";
  try {
    spatialDraft.geometry = JSON.parse(
      geometryText.value,
    ) as SpatialRuleDraft["geometry"];
    await saveSpatialRuleDraft({
      ...spatialDraft,
      valid_from: toApiTime(spatialDraft.valid_from),
      valid_to: toApiTime(spatialDraft.valid_to),
    });
    spatialRuleStatus.value = "草稿已保存";
  } catch (reason) {
    spatialRuleError.value =
      reason instanceof Error ? reason.message : "空间规则草稿保存失败";
  }
}

async function publishDraft() {
  spatialRuleError.value = "";
  try {
    spatialDraft.geometry = JSON.parse(
      geometryText.value,
    ) as SpatialRuleDraft["geometry"];
    await saveSpatialRuleDraft({
      ...spatialDraft,
      valid_from: toApiTime(spatialDraft.valid_from),
      valid_to: toApiTime(spatialDraft.valid_to),
    });
    const published = await publishSpatialRule(spatialDraft.rule_id);
    spatialRuleStatus.value = `已发布 v${published.version}`;
    await refreshSpatialRules();
  } catch (reason) {
    spatialRuleError.value =
      reason instanceof Error ? reason.message : "空间规则发布失败";
  }
}

async function runPreflightValidation() {
  const requestSequence = ++preflightRequestSequence;
  preflightError.value = "";
  preflightResult.value = null;
  try {
    const result = await validatePlannedRoute(plannedRoute);
    if (requestSequence === preflightRequestSequence) {
      preflightResult.value = result;
    }
  } catch (reason) {
    if (requestSequence === preflightRequestSequence) {
      preflightError.value =
        reason instanceof Error ? reason.message : "计划航线校验失败";
    }
  }
}

async function submitLogin() {
  loginPending.value = true;
  loginError.value = "";
  try {
    user.value = await login(username.value, password.value);
    password.value = "";
    await startSituationRefresh();
  } catch (reason) {
    loginError.value = reason instanceof Error ? reason.message : "登录失败";
  } finally {
    loginPending.value = false;
  }
}

async function signOut() {
  await logout();
  user.value = null;
  situationSocket?.close();
  if (freshnessTimer) clearInterval(freshnessTimer);
  if (reconnectTimer) clearTimeout(reconnectTimer);
  if (spatialRulesTimer) clearInterval(spatialRulesTimer);
  freshnessTimer = undefined;
  reconnectTimer = undefined;
  spatialRulesTimer = undefined;
  drones.value = [];
}

onMounted(async () => {
  user.value = await fetchSession();
  authReady.value = true;
  if (user.value) await startSituationRefresh();
});

onBeforeUnmount(() => {
  situationSocket?.close();
  if (freshnessTimer) clearInterval(freshnessTimer);
  if (reconnectTimer) clearTimeout(reconnectTimer);
  if (spatialRulesTimer) clearInterval(spatialRulesTimer);
});
</script>

<template>
  <main v-if="!authReady" class="auth-loading" aria-live="polite">
    正在校验本地身份…
  </main>
  <main v-else-if="!user" class="login-shell">
    <section class="login-intro">
      <span class="brand-index">LOW ALTITUDE / GSH-01</span>
      <p class="login-kicker">PRIVATE · OFFLINE CAPABLE</p>
      <h1>让每一次飞行<br />清晰可见</h1>
      <p>观山湖区低空运行态势与智能分析验证环境</p>
      <dl>
        <div>
          <dt>部署模式</dt>
          <dd>本地受控</dd>
        </div>
        <div>
          <dt>地图能力</dt>
          <dd>MapLibre / PMTiles</dd>
        </div>
        <div>
          <dt>身份体系</dt>
          <dd>三角色最小权限</dd>
        </div>
      </dl>
    </section>
    <form class="login-panel" @submit.prevent="submitLogin">
      <span class="section-code">IDENTITY / LOCAL</span>
      <h2>进入低空智慧调度平台</h2>
      <p>请使用本地演示账号完成身份校验。</p>
      <label>
        <span>账号</span>
        <input v-model="username" name="username" autocomplete="username" />
      </label>
      <label>
        <span>密码</span>
        <input
          v-model="password"
          name="password"
          type="password"
          autocomplete="current-password"
        />
      </label>
      <p v-if="loginError" class="login-error" role="alert">
        {{ loginError }}
      </p>
      <button type="submit" :disabled="loginPending">
        {{ loginPending ? "校验中…" : "登录" }}
      </button>
      <small>会话仅保存在 HttpOnly 本地 Cookie 中</small>
    </form>
  </main>
  <main v-else class="command-shell">
    <header class="command-header">
      <div class="brand-block">
        <span class="brand-index">LOW ALTITUDE / GSH-01</span>
        <h1>低空态势总览</h1>
      </div>
      <div class="system-state" role="status">
        <span class="live-signal" aria-hidden="true"></span>
        本地态势链路
        <strong>{{ error ? "异常" : "正常" }}</strong>
      </div>
      <div class="identity-block">
        <div class="header-place">
          <span>{{ user.role_label }}</span>
          <strong>{{ user.username }}</strong>
        </div>
        <button class="logout-button" type="button" @click="signOut">
          退出登录
        </button>
      </div>
    </header>

    <nav class="capability-nav" aria-label="能力导航">
      <button
        v-for="item in navigation"
        :key="item.label"
        type="button"
        :aria-current="activeView === item.label ? 'page' : undefined"
        @click="activeView = item.label"
      >
        {{ item.label }}
      </button>
    </nav>

    <aside class="overview-rail" aria-label="运行摘要">
      <p class="section-code">01 / CURRENT PICTURE</p>
      <h2>当前图景</h2>
      <div class="metric">
        <span>飞行架次</span>
        <strong>{{ displayedMetrics.flight_sorties }}</strong>
      </div>
      <div class="metric metric--accent">
        <span>当前在飞</span>
        <strong>{{ displayedMetrics.in_flight_count }}</strong>
      </div>
      <div class="metric metric--compact">
        <span
          >在线率（近
          {{ displayedMetrics.observation_window_seconds }} 秒）</span
        >
        <strong>{{ displayedMetrics.online_rate.toFixed(1) }}%</strong>
      </div>
      <div class="metric metric--compact">
        <span>遥测延迟</span>
        <strong
          >{{ displayedMetrics.telemetry_delay_seconds.toFixed(1) }} 秒</strong
        >
      </div>
      <div class="metric metric--compact">
        <span>来源构成</span>
        <strong>
          真实 {{ displayedMetrics.source_composition.real }} / 模拟
          {{ displayedMetrics.source_composition.simulated }}
        </strong>
      </div>
      <div v-if="selectedDrone" class="source-card">
        <span class="source-badge">
          {{
            selectedDrone.source_type === "simulated" ? "模拟数据" : "真实数据"
          }}
        </span>
        <p>来源时间</p>
        <time :datetime="selectedDrone.source_time">
          {{ formatSourceTime(selectedDrone.source_time) }}
        </time>
        <p>平台接收时间 · 最后有效</p>
        <time :datetime="selectedDrone.platform_received_time">
          {{ formatSourceTime(selectedDrone.platform_received_time) }}
        </time>
        <span class="freshness-state" :data-status="selectedDrone.data_status">
          {{
            selectedDrone.data_status === "offline"
              ? "离线"
              : selectedDrone.data_status === "delayed"
                ? "数据延迟"
                : "数据正常"
          }}
        </span>
        <dl>
          <div>
            <dt>高度</dt>
            <dd>{{ selectedDrone.altitude_m }} m</dd>
          </div>
          <div>
            <dt>速度</dt>
            <dd>{{ selectedDrone.speed_mps }} m/s</dd>
          </div>
          <div>
            <dt>航向</dt>
            <dd>{{ selectedDrone.heading_deg }}°</dd>
          </div>
        </dl>
      </div>
      <p v-else-if="loading" class="quiet-state" role="status">
        正在读取态势快照…
      </p>
      <p v-else-if="error" class="error-state" role="alert">{{ error }}</p>
      <p v-else class="quiet-state" role="status">暂无已接入无人机</p>
    </aside>

    <section class="map-stage">
      <SituationMap
        :drones="displayedDrones"
        :spatial-rules="displayedSpatialRules"
        :validation-position="preflightResult?.violations[0]?.position"
        :incursion-alert="visibleIncursionAlert"
      />
      <div class="map-legend" aria-label="地图图例">
        <span><i class="legend-dot"></i> 已接入无人机</span>
        <span><i class="legend-ring"></i> 模拟来源</span>
      </div>
    </section>

    <aside
      v-if="activeView === '空间规则'"
      class="signal-rail spatial-editor"
      aria-label="空间规则编辑"
    >
      <p class="section-code">SPATIAL / DRAFT</p>
      <h2>空间规则草稿</h2>
      <label><span>规则名称</span><input v-model="spatialDraft.name" /></label>
      <label>
        <span>类型</span>
        <select v-model="spatialDraft.rule_type">
          <option value="no_fly_zone">禁飞区</option>
          <option value="geofence">电子围栏</option>
        </select>
      </label>
      <div class="field-pair">
        <label>
          <span>最低高度（米）</span>
          <input v-model.number="spatialDraft.min_altitude_m" type="number" />
        </label>
        <label>
          <span>最高高度（米）</span>
          <input v-model.number="spatialDraft.max_altitude_m" type="number" />
        </label>
      </div>
      <label>
        <span>生效时间</span>
        <input v-model="spatialDraft.valid_from" type="datetime-local" />
      </label>
      <label>
        <span>失效时间</span>
        <input v-model="spatialDraft.valid_to" type="datetime-local" />
      </label>
      <label>
        <span>水平范围（WGS-84 GeoJSON）</span>
        <textarea v-model="geometryText" rows="5"></textarea>
      </label>
      <label><span>来源</span><input v-model="spatialDraft.source" /></label>
      <p v-if="spatialRuleError" class="error-state" role="alert">
        {{ spatialRuleError }}
      </p>
      <p v-if="spatialRuleStatus" class="editor-status" role="status">
        {{ spatialRuleStatus }}
      </p>
      <div class="editor-actions">
        <button type="button" @click="saveDraft">保存草稿</button>
        <button type="button" class="publish-button" @click="publishDraft">
          发布版本
        </button>
      </div>
    </aside>
    <aside
      v-else-if="activeView === '越界告警'"
      class="signal-rail incursion-panel"
      aria-label="越界告警专题视图"
    >
      <p class="section-code">INCURSION / RULE HIT</p>
      <h2>越界告警</h2>
      <p class="scope-warning">
        规则命中提示，不代表违规认定，不输出飞行控制指令。
      </p>
      <button
        v-for="alert in incursionAlerts"
        :key="alert.id"
        type="button"
        class="incursion-card"
        :data-status="alert.ended_at ? 'ended' : 'active'"
        @click="selectedIncursionAlert = alert"
      >
        <span>{{ alert.ended_at ? "已结束" : "持续中" }}</span>
        <strong>{{ alert.drone_id }}</strong>
        <p>{{ alert.reason }}</p>
        <small>{{ alert.rule_id }} · v{{ alert.rule_version }}</small>
        <small>{{
          alert.source_type === "simulated" ? "模拟数据" : "真实数据"
        }}</small>
        <time :datetime="alert.started_at"
          >开始 {{ formatSourceTime(alert.started_at) }}</time
        >
        <time :datetime="alert.platform_received_time"
          >平台接收 {{ formatSourceTime(alert.platform_received_time) }}</time
        >
        <time v-if="alert.ended_at" :datetime="alert.ended_at"
          >结束 {{ formatSourceTime(alert.ended_at) }}</time
        >
      </button>
      <p v-if="!incursionAlerts.length" class="quiet-state">当前无越界告警</p>
    </aside>
    <aside
      v-else-if="activeView === '航前规则校验'"
      class="signal-rail spatial-editor"
      aria-label="航前规则校验"
    >
      <p class="section-code">PREFLIGHT / WGS-84</p>
      <h2>计划航线航前规则校验</h2>
      <template v-for="(point, index) in plannedRoute" :key="index">
        <p class="route-point-title">航点 {{ index + 1 }}</p>
        <div class="field-pair">
          <label>
            <span>经度</span>
            <input
              v-model.number="point.longitude"
              type="number"
              step="0.001"
            />
          </label>
          <label>
            <span>纬度</span>
            <input v-model.number="point.latitude" type="number" step="0.001" />
          </label>
          <label>
            <span>高度（米）</span>
            <input v-model.number="point.altitude_m" type="number" />
          </label>
          <label>
            <span>时间</span>
            <input v-model="point.time" />
          </label>
        </div>
      </template>
      <button
        class="preflight-button"
        type="button"
        @click="runPreflightValidation"
      >
        校验计划航线
      </button>
      <p v-if="preflightError" class="error-state" role="alert">
        {{ preflightError }}
      </p>
      <article v-if="preflightResult" class="preflight-result">
        <strong>
          {{
            preflightResult.result === "passed"
              ? "通过"
              : preflightResult.result === "entered_no_fly_zone"
                ? "进入禁飞区"
                : "超出电子围栏"
          }}
        </strong>
        <template v-if="preflightResult.violations[0]">
          <p>
            命中版本 {{ preflightResult.violations[0].rule_id }} v{{
              preflightResult.violations[0].rule_version
            }}
          </p>
          <p>{{ preflightResult.violations[0].reason }}</p>
        </template>
        <small>规则判断，不代表审批或飞行许可</small>
      </article>
    </aside>
    <aside v-else class="signal-rail" aria-label="实时信号">
      <p class="section-code">SIGNAL / LIVE</p>
      <h2>实时信号</h2>
      <article v-if="selectedDrone" class="signal-item">
        <time :datetime="selectedDrone.source_time">
          {{ formatSourceTime(selectedDrone.source_time).slice(11) }}
        </time>
        <strong>遥测接入成功</strong>
        <p>观山湖中心区 · {{ selectedDrone.altitude_m }} 米</p>
        <span>SIM / {{ selectedDrone.flight_state.toUpperCase() }}</span>
      </article>
      <p v-else class="quiet-state">等待首个遥测信号</p>
      <div class="scope-note">
        <span>MAP PACKAGE</span>
        <strong>GSH · LOCAL · V1</strong>
        <p>底图由浏览器直接读取本地 PMTiles，无独立瓦片进程。</p>
      </div>
    </aside>

    <footer class="command-footer">
      <span>PRIVATE / OFFLINE CAPABLE</span>
      <div class="activity-line">
        <i></i><i></i><i></i><i></i><i></i><i></i>
      </div>
      <strong>态势快照 · HTTP</strong>
    </footer>
  </main>
</template>

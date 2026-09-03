<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import { fetchSession, login, logout, type AuthenticatedUser } from "@/auth";
import SituationMap from "@/components/SituationMap.vue";
import {
  fetchSituationSnapshot,
  formatSourceTime,
  type DroneSnapshot,
} from "@/situation";

const drones = ref<DroneSnapshot[]>([]);
const loading = ref(true);
const error = ref("");
const user = ref<AuthenticatedUser | null>(null);
const authReady = ref(false);
const username = ref("situation-viewer");
const password = ref("");
const loginError = ref("");
const loginPending = ref(false);
const selectedDrone = computed(() => drones.value[0]);
const navigation = computed(() => {
  const capabilities = new Set(user.value?.capabilities ?? []);
  return [
    { label: "运行态势", visible: capabilities.has("situation:read") },
    { label: "数据智能", visible: capabilities.has("query:read") },
    { label: "AI异常线索研判", visible: capabilities.has("clue:review") },
    { label: "空间规则", visible: capabilities.has("spatial:manage") },
  ].filter((item) => item.visible);
});
let refreshTimer: ReturnType<typeof setInterval> | undefined;

async function refreshSituation() {
  try {
    drones.value = (await fetchSituationSnapshot()).drones;
    error.value = "";
  } catch (reason) {
    error.value =
      reason instanceof Error ? reason.message : "态势快照暂时不可用";
  } finally {
    loading.value = false;
  }
}

function startSituationRefresh() {
  void refreshSituation();
  refreshTimer = setInterval(refreshSituation, 500);
}

async function submitLogin() {
  loginPending.value = true;
  loginError.value = "";
  try {
    user.value = await login(username.value, password.value);
    password.value = "";
    startSituationRefresh();
  } catch (reason) {
    loginError.value = reason instanceof Error ? reason.message : "登录失败";
  } finally {
    loginPending.value = false;
  }
}

async function signOut() {
  await logout();
  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = undefined;
  user.value = null;
  drones.value = [];
}

onMounted(async () => {
  user.value = await fetchSession();
  authReady.value = true;
  if (user.value) startSituationRefresh();
});

onBeforeUnmount(() => {
  if (refreshTimer) clearInterval(refreshTimer);
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
      <span v-for="item in navigation" :key="item.label">{{ item.label }}</span>
    </nav>

    <aside class="overview-rail" aria-label="运行摘要">
      <p class="section-code">01 / CURRENT PICTURE</p>
      <h2>当前图景</h2>
      <div class="metric">
        <span>已接入无人机</span>
        <strong>{{ drones.length.toString().padStart(2, "0") }}</strong>
      </div>
      <div class="metric metric--accent">
        <span>当前在飞</span>
        <strong>{{
          drones
            .filter((drone) => drone.flight_state === "flying")
            .length.toString()
            .padStart(2, "0")
        }}</strong>
      </div>
      <div v-if="selectedDrone" class="source-card">
        <span class="source-badge">模拟数据</span>
        <p>来源时间</p>
        <time :datetime="selectedDrone.source_time">
          {{ formatSourceTime(selectedDrone.source_time) }}
        </time>
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
      <SituationMap :drones="drones" />
      <div class="map-legend" aria-label="地图图例">
        <span><i class="legend-dot"></i> 已接入无人机</span>
        <span><i class="legend-ring"></i> 模拟来源</span>
      </div>
    </section>

    <aside class="signal-rail" aria-label="实时信号">
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

<script setup lang="ts">
import { nextTick, ref } from "vue";

import { platformRoutes } from "@/router/routes";
import { pocApi } from "@/shared/api/client";

const dialog = ref<HTMLDialogElement>();
const message = ref("");

function openReset() {
  dialog.value?.showModal();
  nextTick(() => dialog.value?.querySelector<HTMLButtonElement>("[value=cancel]")?.focus());
}

async function confirmReset() {
  const result = await pocApi.reset();
  message.value = `已恢复 ${result.snapshotVersion}，审计记录 ${result.auditEntry.id}`;
}
</script>

<template>
  <div class="portal-shell">
    <header class="portal-header">
      <span class="poc-badge">POC 演示数据</span>
      <h1>无人机低空智慧调度平台</h1>
      <button id="reset" type="button" class="primary-button" @click="openReset">一键恢复演示数据</button>
    </header>
    <main class="route-grid" aria-label="业务页面">
      <RouterLink v-for="route in platformRoutes" :key="route.name" :to="route.path" class="route-card">
        <strong>{{ route.title }}</strong>
        <span>{{ route.name === "situation" ? "指挥中心只读监看" : "POC 业务操作台" }}</span>
      </RouterLink>
    </main>
    <p role="status" class="status-message">{{ message }}</p>
    <dialog id="reset-dialog" ref="dialog" aria-labelledby="reset-title" @close="($event.target as HTMLDialogElement).returnValue === 'confirm' && confirmReset()">
      <h2 id="reset-title">恢复标准演示数据？</h2>
      <p>当前业务演示状态将被 POC-DEMO-V1 覆盖，恢复动作会写入审计。</p>
      <form method="dialog">
        <button value="cancel">取消</button>
        <button value="confirm" class="primary-button">确认恢复</button>
      </form>
    </dialog>
  </div>
</template>

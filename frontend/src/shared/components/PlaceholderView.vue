<script setup lang="ts">
import { onMounted } from "vue";

import { usePocStore } from "@/stores/poc";

defineProps<{ title: string }>();
const store = usePocStore();
onMounted(() => store.load());
</script>

<template>
  <div class="placeholder-shell">
    <header><RouterLink to="/">返回概览</RouterLink><span class="poc-badge">POC 演示数据</span></header>
    <main>
      <h1>{{ title }}</h1>
      <p v-if="store.loading" role="status">正在加载演示数据…</p>
      <div v-else-if="store.error" role="alert" class="error-state">{{ store.error }}，请刷新重试。</div>
      <section v-else class="empty-state" aria-label="后续业务功能">
        <h2>架构迁移已完成</h2>
        <p>该页面的业务交互将在对应 Ticket 中实现，当前不提前扩展范围。</p>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref } from "vue";

import { runDataQuery, type DataQueryResponse } from "@/data-query";
import { formatSourceTime } from "@/situation";

const open = ref(false);
const question = ref("最近1小时有多少架无人机？");
const result = ref<DataQueryResponse | null>(null);
const pending = ref(false);
const error = ref("");
const input = ref<HTMLInputElement>();
const launcher = ref<HTMLButtonElement>();

async function openDialog() {
  open.value = true;
  await nextTick();
  input.value?.focus();
}

async function closeDialog() {
  open.value = false;
  await nextTick();
  launcher.value?.focus();
}

async function submit() {
  if (pending.value || !question.value.trim()) return;
  pending.value = true;
  error.value = "";
  try {
    result.value = await runDataQuery(question.value.trim(), null);
  } catch (reason) {
    result.value = null;
    error.value =
      reason instanceof Error ? reason.message : "当前问题暂时无法回答";
  } finally {
    pending.value = false;
  }
}

function handleEscape(event: KeyboardEvent) {
  if (event.key === "Escape" && open.value) void closeDialog();
}

window.addEventListener("keydown", handleEscape);
onBeforeUnmount(() => window.removeEventListener("keydown", handleEscape));
</script>

<template>
  <button
    ref="launcher"
    class="smart-launcher"
    type="button"
    aria-label="打开智能问数"
    :aria-expanded="open"
    aria-controls="smart-query-dialog"
    @click="open ? closeDialog() : openDialog()"
  >
    <span aria-hidden="true">问</span>
    智能问数
  </button>

  <section
    v-if="open"
    id="smart-query-dialog"
    class="smart-dialog"
    role="dialog"
    aria-modal="false"
    aria-labelledby="smart-query-title"
  >
    <header>
      <div>
        <small>只读业务查询</small>
        <h2 id="smart-query-title">智能问数</h2>
      </div>
      <button type="button" aria-label="关闭智能问数" @click="closeDialog">
        ×
      </button>
    </header>
    <form @submit.prevent="submit">
      <label for="leadership-question">输入问题</label>
      <div>
        <input
          id="leadership-question"
          ref="input"
          v-model="question"
          autocomplete="off"
        />
        <button type="submit" :disabled="pending">
          {{ pending ? "查询中…" : "查询" }}
        </button>
      </div>
    </form>
    <p v-if="error" class="query-error" role="alert">
      当前问题暂时无法回答：{{ error }}
    </p>
    <article v-if="result" class="query-result" aria-live="polite">
      <strong>{{ result.answer }}</strong>
      <h3>查询依据</h3>
      <dl>
        <div>
          <dt>时间范围</dt>
          <dd>
            {{ formatSourceTime(result.query_basis.time_range.start) }} 至
            {{ formatSourceTime(result.query_basis.time_range.end) }}
          </dd>
        </div>
        <div>
          <dt>数据来源</dt>
          <dd>{{ result.query_basis.data_sources.join("、") }}</dd>
        </div>
        <div>
          <dt>统计口径</dt>
          <dd>{{ result.query_basis.statistical_definition }}</dd>
        </div>
      </dl>
    </article>
  </section>
</template>

<style scoped>
.smart-launcher {
  position: fixed;
  right: 1.5rem;
  bottom: 1.5rem;
  z-index: 30;
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.55rem 0.9rem 0.55rem 0.55rem;
  border: 1px solid rgba(85, 226, 213, 0.55);
  background: #0b3036;
  color: #e4f4f1;
  cursor: pointer;
  clip-path: polygon(
    0.6rem 0,
    100% 0,
    100% calc(100% - 0.6rem),
    calc(100% - 0.6rem) 100%,
    0 100%,
    0 0.6rem
  );
}
.smart-launcher span {
  display: grid;
  width: 1.8rem;
  height: 1.8rem;
  place-items: center;
  background: #55e2d5;
  color: #05201f;
  font-weight: 800;
}
.smart-launcher:focus-visible,
.smart-dialog button:focus-visible,
.smart-dialog input:focus-visible {
  outline: 2px solid #55e2d5;
  outline-offset: 2px;
}
.smart-dialog {
  position: fixed;
  right: 1.5rem;
  bottom: 5rem;
  z-index: 31;
  width: min(25rem, calc(100vw - 2rem));
  border: 1px solid rgba(85, 226, 213, 0.42);
  background: #081d22;
  box-shadow: 0 1.5rem 5rem rgba(0, 0, 0, 0.55);
}
.smart-dialog header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem;
  border-bottom: 1px solid rgba(85, 226, 213, 0.2);
}
.smart-dialog h2,
.smart-dialog h3,
.smart-dialog p {
  margin: 0;
}
.smart-dialog h2 {
  margin-top: 0.15rem;
  font-size: 1rem;
}
.smart-dialog small,
.smart-dialog label,
.smart-dialog dt {
  color: #71918f;
  font-size: 0.68rem;
}
.smart-dialog header button {
  border: 0;
  background: transparent;
  color: #aac7c4;
  cursor: pointer;
  font-size: 1.4rem;
}
.smart-dialog form,
.query-result,
.query-error {
  padding: 1rem;
}
.smart-dialog form > div {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 0.5rem;
  margin-top: 0.45rem;
}
.smart-dialog input {
  min-width: 0;
  padding: 0.7rem;
  border: 1px solid rgba(85, 226, 213, 0.28);
  background: #061416;
  color: #e4f4f1;
}
.smart-dialog form button {
  border: 0;
  padding: 0 0.9rem;
  background: #55e2d5;
  color: #05201f;
  cursor: pointer;
  font-weight: 700;
}
.query-error {
  color: #ff8993;
  font-size: 0.78rem;
}
.query-result {
  border-top: 1px solid rgba(85, 226, 213, 0.16);
}
.query-result > strong {
  display: block;
  color: #55e2d5;
}
.query-result h3 {
  margin-top: 1rem;
  font-size: 0.78rem;
}
.query-result dl {
  margin: 0.5rem 0 0;
}
.query-result dl div {
  padding: 0.45rem 0;
  border-top: 1px solid rgba(85, 226, 213, 0.1);
}
.query-result dd {
  margin: 0.2rem 0 0;
  color: #c3dcd9;
  font-size: 0.72rem;
}
</style>

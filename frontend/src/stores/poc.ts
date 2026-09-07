import { defineStore } from "pinia";

import { pocApi, type PocState } from "@/shared/api/client";

export const usePocStore = defineStore("poc", {
  state: () => ({ data: null as PocState | null, loading: false, error: "" }),
  actions: {
    async load() {
      this.loading = true; this.error = "";
      try { this.data = await pocApi.state(); }
      catch (error) { this.error = error instanceof Error ? error.message : "演示数据加载失败"; }
      finally { this.loading = false; }
    }
  }
});

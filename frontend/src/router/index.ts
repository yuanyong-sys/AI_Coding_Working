import { createRouter, createWebHistory } from "vue-router";

import HomeView from "@/domains/home/views/HomeView.vue";
import SituationView from "@/domains/map/views/SituationView.vue";
import PrototypeView from "@/shared/components/PrototypeView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "home", component: HomeView },
    { path: "/screen-overview.html", name: "situation", component: SituationView },
    { path: "/dispatch-tasks.html", name: "missions", component: PrototypeView, props: { title: "任务调度", page: "dispatch-tasks.html" } },
    { path: "/alert-workbench.html", name: "alerts", component: PrototypeView, props: { title: "告警线索处置", page: "alert-workbench.html" } },
    { path: "/stats-ledger.html", name: "ledgers", component: PrototypeView, props: { title: "巡检台账统计", page: "stats-ledger.html" } },
    { path: "/resource-management.html", name: "resources", component: PrototypeView, props: { title: "资源管理", page: "resource-management.html" } },
    { path: "/system-management.html", name: "system", component: PrototypeView, props: { title: "系统管理", page: "system-management.html" } },
    { path: "/system-org.html", name: "system-org", component: PrototypeView, props: { title: "组织与人员", page: "system-org.html" } },
    { path: "/system-roles.html", name: "system-roles", component: PrototypeView, props: { title: "角色权限", page: "system-roles.html" } },
    { path: "/system-config.html", name: "system-config", component: PrototypeView, props: { title: "平台配置", page: "system-config.html" } },
    { path: "/system-logs.html", name: "system-logs", component: PrototypeView, props: { title: "操作日志", page: "system-logs.html" } }
  ]
});

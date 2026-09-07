<script setup lang="ts">
defineProps<{ title: string; page: string }>();
</script>

<template>
  <iframe class="prototype-frame" :src="`/prototype/${page}`" :title="title" />
</template>

<style scoped>
.prototype-frame {
  display: block;
  width: 100vw;
  height: 100vh;
  border: 0;
  background: #071427;
}
</style>

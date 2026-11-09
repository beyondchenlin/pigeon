<script setup lang="ts">
withDefaults(defineProps<{ large?: boolean; subtitle?: string }>(), { large: false, subtitle: '' });
</script>

<template>
  <div class="brand-identity" :class="{ 'brand-identity--large': large }" aria-label="BK群控" data-brand-identity>
    <img
      src="/bk-logo.png"
      alt="BK群控"
      :width="large ? 56 : 32"
      :height="large ? 56 : 32"
      class="brand-identity__logo rounded-md bg-white object-contain"
    />
    <div class="brand-identity__copy" data-brand-copy>
      <span class="brand-identity__title text-foreground" data-brand-title>BK群控</span>
      <p v-if="subtitle" class="brand-identity__subtitle text-muted-foreground" data-brand-subtitle>{{ subtitle }}</p>
    </div>
  </div>
</template>

<style scoped>
.brand-identity { display: flex; align-items: center; gap: 8px; }
.brand-identity__logo { display: block; flex: 0 0 32px; width: 32px; height: 32px; }
.brand-identity__copy { flex: 1; min-width: 0; }
.brand-identity__title { display: block; margin: 0; font-size: 14px; line-height: 20px; font-weight: 700; white-space: nowrap; }
.brand-identity__subtitle { margin: 2px 0 0; font-size: 10px; line-height: 15px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.brand-identity--large .brand-identity__logo { flex-basis: 56px; width: 56px; height: 56px; }
.brand-identity--large .brand-identity__title { font-size: 24px; line-height: 32px; }
</style>

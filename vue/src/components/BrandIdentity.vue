<script setup lang="ts">
withDefaults(defineProps<{ large?: boolean; subtitle?: string }>(), { large: false, subtitle: '' });
</script>

<template>
  <div class="flex items-center gap-2" aria-label="BK群控">
    <img
      src="/bk-logo.png"
      alt="BK群控"
      :width="large ? 56 : 32"
      :height="large ? 56 : 32"
      class="shrink-0 rounded-md bg-white object-contain"
    />
    <div class="min-w-0 flex-1">
      <span :class="large ? 'text-2xl' : 'text-sm leading-5'" class="block whitespace-nowrap font-bold text-foreground" data-brand-title>BK群控</span>
      <p v-if="subtitle" class="mt-0.5 truncate text-[10px] leading-[15px] text-muted-foreground" data-brand-subtitle>{{ subtitle }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ large?: boolean }>(), { large: false });
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
    <span :class="large ? 'text-2xl' : 'text-sm'" class="whitespace-nowrap font-bold text-foreground">
      BK群控
    </span>
  </div>
</template>

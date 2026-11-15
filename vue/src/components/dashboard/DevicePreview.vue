<script setup lang="ts">
import LiveDevicePreview from '@/components/devices/LiveDevicePreview.vue';
const emit = defineEmits<{ dimensions: [width: number, height: number] }>();
defineProps<{ deviceId: string; active: boolean; placeholder: string }>();
</script>
<template><LiveDevicePreview :device-id="deviceId" :online="active" :paused="!active" @dimensions="(w, h) => emit('dimensions', w, h)" /></template>

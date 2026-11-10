<script setup lang="ts">
import LiveDevicePreview from '@/components/devices/LiveDevicePreview.vue';
defineProps<{ deviceId: string; active: boolean; placeholder: string }>();
</script>
<template><LiveDevicePreview :device-id="deviceId" :online="active" :paused="!active" /></template>

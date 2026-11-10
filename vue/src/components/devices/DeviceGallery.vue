<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { ChevronLeft, ChevronRight, MoreHorizontal } from 'lucide-vue-next';
import LiveDevicePreview from './LiveDevicePreview.vue';
import DeviceBattery from './DeviceBattery.vue';
import { galleryMediaHeight, useMobileViewport } from '@/lib/mobile-layout';
type Device = { id: string; status: string; remark: string; model: string; brand: string };
const props = defineProps<{ devices: Device[]; mode: 'cards'|'list'|'single'; paused: boolean; selected: Set<string> }>();
const emit = defineEmits<{ open: [id: string]; details: [device: Device]; toggle: [id: string, value: boolean] }>();
const mobile = useMobileViewport();
const host = ref<HTMLElement>();
const height = ref(550);
const single = ref(0);
const selectedDevice = computed(() => props.devices[Math.min(single.value, Math.max(0, props.devices.length - 1))]);
const rows = computed(() => props.mode === 'single' ? selectedDevice.value ? [selectedDevice.value] : [] : props.devices);
const style = computed(() => ({ '--gallery-media-height': `${props.mode === 'list' ? 48 : mobile.value ? galleryMediaHeight(height.value, true, props.mode === 'single') : props.mode === 'single' ? Math.max(180, height.value - 120) : Math.min(340, Math.max(180, height.value - 80))}px` }));
let observer: ResizeObserver;
watch(() => props.mode, () => { single.value = 0; if (host.value) host.value.scrollTop = 0; });
watch(() => props.devices.map(d => d.id).join('|'), () => { single.value = 0; });
async function step(delta: number) { single.value = Math.max(0, Math.min(props.devices.length - 1, single.value + delta)); await nextTick(); }
onMounted(() => { observer = new ResizeObserver(([entry]) => { if (entry) height.value = entry.contentRect.height; }); if (host.value) observer.observe(host.value); });
onBeforeUnmount(() => observer?.disconnect());
</script>
<template>
  <div ref="host" class="device-gallery" :class="`gallery-${mode}`" :style="style" :data-gallery-mode="mode">
    <div class="gallery-grid">
      <article v-for="device in rows" :key="device.id" class="gallery-card" :class="{ selected: selected.has(device.id) }" :data-device-card="device.id">
        <header><label class="gallery-select"><input type="checkbox" :checked="selected.has(device.id)" :aria-label="`选择 ${device.id}`" @change="emit('toggle', device.id, ($event.target as HTMLInputElement).checked)" /></label><strong :title="device.remark || device.model || device.id">{{ device.remark || (device.model !== '-' ? device.model : device.id) }}</strong><span class="gallery-state" :class="device.status">{{ device.status === 'online' ? '在线' : device.status === 'pending' ? '未接入' : '离线' }}</span></header>
        <button class="gallery-screen" :aria-label="`远控 ${device.id}`" :disabled="device.status !== 'online'" @click="emit('open', device.id)"><LiveDevicePreview v-if="mode !== 'list'" :device-id="device.id" :online="device.status === 'online'" :pending="device.status === 'pending'" :paused="paused" /><span v-else class="list-device-icon">▯</span></button>
        <footer><span class="gallery-model" :title="device.model">{{ device.model === '-' ? device.id : device.model }}</span><DeviceBattery :device-id="device.id" :online="device.status === 'online'" /><button class="gallery-details" :aria-label="`设备详情 ${device.id}`" @click="emit('details', device)"><MoreHorizontal class="h-4 w-4" /></button></footer>
        <button class="gallery-list-open" :disabled="device.status !== 'online'" @click="emit('open', device.id)">远控</button>
      </article>
    </div>
    <div v-if="mode === 'single'" class="gallery-pager"><button :disabled="single === 0" aria-label="上一台手机" @click="step(-1)"><ChevronLeft class="h-4 w-4" />上一台</button><span>{{ devices.length ? single + 1 : 0 }} / {{ devices.length }}（本页）</span><button :disabled="single >= devices.length - 1" aria-label="下一台手机" @click="step(1)">下一台<ChevronRight class="h-4 w-4" /></button></div>
  </div>
</template>
<style scoped>
.device-gallery{min-height:0;flex:1;overflow:auto;padding:10px;overscroll-behavior:contain}.gallery-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;align-items:start}.gallery-card{min-width:0;border:1px solid var(--border);border-radius:7px;background:var(--background);overflow:hidden}.gallery-card.selected{border-color:#f97316;box-shadow:0 0 0 1px #f97316}.gallery-card header{display:flex;align-items:center;gap:5px;padding:3px 8px;height:34px;min-width:0}.gallery-select{display:flex;align-items:center;justify-content:center;width:24px;height:30px;flex-shrink:0}.gallery-select input{width:14px;height:14px;accent-color:#f97316}.gallery-card strong{font-size:11px;min-width:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.gallery-state{font-size:9px;flex-shrink:0;color:var(--muted-foreground)}.gallery-state.online{color:#059669}.gallery-screen{display:block;width:100%;height:var(--gallery-media-height);overflow:hidden;background:var(--background)}.gallery-card footer{height:30px;display:flex;align-items:center;gap:5px;padding:2px 7px;font-size:10px}.gallery-model{min-width:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted-foreground)}.gallery-details{flex-shrink:0;min-width:28px;height:28px;display:grid;place-items:center}.gallery-list-open{display:none}.gallery-pager{display:flex;align-items:center;justify-content:space-between;gap:8px;padding-top:8px;font-size:11px}.gallery-pager button{display:flex;align-items:center;gap:4px;border:1px solid var(--border);border-radius:5px;padding:8px;min-height:38px;background:var(--background)}button:disabled{opacity:.45}button:focus-visible{outline:2px solid #f97316;outline-offset:-2px}.gallery-single .gallery-grid{display:flex;justify-content:center}.gallery-single .gallery-card{width:100%;max-width:440px}.gallery-list .gallery-grid{display:flex;flex-direction:column;gap:0}.gallery-list .gallery-card{position:relative;width:100%;border-radius:0;border-width:0 0 1px;display:grid;grid-template-columns:28px minmax(0,1fr) 50px;gap:3px 6px;padding:8px}.gallery-list .gallery-screen{grid-column:1;grid-row:1/3;height:50px}.list-device-icon{font-size:30px;color:var(--muted-foreground)}.gallery-list header{grid-column:2;padding:0;height:28px}.gallery-list footer{grid-column:2;padding:0;height:26px}.gallery-list .gallery-list-open{display:block;grid-column:3;grid-row:1/3;align-self:center;min-height:44px;background:#fff0e6;color:#b94e09;border-radius:5px;font-size:11px}.gallery-list .gallery-select{height:28px}.gallery-list .gallery-card strong{font-size:12px}
@media(max-width:767px){.device-gallery{padding:6px 8px}.gallery-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.gallery-card header{height:32px;padding:1px 5px;gap:3px}.gallery-card footer{height:26px;font-size:9px;padding:1px 5px;gap:3px}.gallery-card footer :deep([data-battery-enabled]){font-size:9px;gap:2px}.gallery-card footer :deep([data-battery-enabled] svg){width:11px;height:11px}.gallery-details{width:24px;min-width:24px;height:24px}.gallery-select{width:20px}.gallery-select input{width:13px;height:13px}.gallery-card strong{font-size:10px}.gallery-single .gallery-card header strong{font-size:12px}}
</style>

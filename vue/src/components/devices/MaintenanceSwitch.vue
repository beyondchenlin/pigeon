<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue';
import { apiRequest, ApiRequestError } from '@/lib/api';
import { Button } from '@/components/ui/button';

const props = defineProps<{ deviceId: string }>();
type Mode = { enabled: boolean | null; request_id: string | null; effective: 'on' | 'off' | 'changing' | 'unknown'; error?: string | null };
const mode = ref<Mode>();
const loading = ref(false), changing = ref(false), message = ref('');
let timer: ReturnType<typeof setTimeout> | undefined;
let controller: AbortController | undefined;
let disposed = false, until = 0;
let pending: { id: string; enabled: boolean } | undefined;
const endpoint = () => `/api/v1/device/${encodeURIComponent(props.deviceId)}/maintenance`;

async function refresh() {
  if (disposed || loading.value) return;
  loading.value = true;
  controller = new AbortController();
  const timeout = setTimeout(() => controller?.abort(), 15000);
  try {
    const result = await apiRequest<Mode>(endpoint(), { signal: controller.signal, cache: 'no-store' });
    if (!disposed) {
      mode.value = result.data;
      if (mode.value?.effective === 'on' || mode.value?.effective === 'off') {
        changing.value = false;
        if (pending && (mode.value.request_id !== pending.id || mode.value.enabled !== pending.enabled)) {
          message.value = '本次切换未获确认，上方显示的是当前实际状态。';
        } else message.value = '';
        pending = undefined;
      } else {
        message.value = '尚未确认手机和服务器的实际状态；请稍后刷新核实。';
        if (mode.value?.effective === 'changing' && !until) until = Date.now() + 60000;
      }
    }
  } catch {
    if (!disposed) { mode.value = undefined; message.value = '连接暂不可用，维护状态未确认。'; }
  } finally {
    clearTimeout(timeout); loading.value = false;
    if (!disposed && (changing.value || mode.value?.effective === 'changing') && Date.now() < until) {
      timer = setTimeout(refresh, 2000);
    } else if (!disposed) changing.value = false;
  }
}

async function setMode(enabled: boolean) {
  if (changing.value || loading.value) return;
  clearTimeout(timer);
  changing.value = true; message.value = ''; until = Date.now() + 60000;
  controller = new AbortController();
  const timeout = setTimeout(() => controller?.abort(), 15000);
  const requestId = crypto.randomUUID();
  pending = { id: requestId, enabled };
  let queryReceipt = true;
  try {
    const result = await apiRequest<Mode>(endpoint(), {
      method: 'POST', signal: controller.signal,
      headers: { 'X-Requested-With': 'BK-Maintenance' },
      body: JSON.stringify({ enabled, request_id: requestId }),
    });
    if (!disposed) mode.value = result.data;
  } catch (error) {
    if (!disposed && error instanceof ApiRequestError && error.status >= 400 && error.status < 500) {
      changing.value = false; pending = undefined; queryReceipt = false;
      message.value = '服务器未接受本次切换，请刷新确认状态后重试。';
    } else if (!disposed) { mode.value = undefined; message.value = '请求回执未确认，正在查询结果；请勿反复切换。'; }
  } finally {
    clearTimeout(timeout);
    // A timeout may follow an accepted request: query, never resubmit it.
    if (!disposed && queryReceipt) timer = setTimeout(refresh, 1000);
  }
}

onMounted(refresh);
onBeforeUnmount(() => { disposed = true; clearTimeout(timer); controller?.abort(); });
</script>

<template>
  <fieldset class="space-y-3 rounded border p-3" aria-label="维护模式设置">
    <legend class="px-1 text-sm font-medium">维护模式</legend>
    <div class="flex items-center justify-between gap-3">
      <p class="text-sm" role="status">
        {{ changing || mode?.effective === 'changing' ? '正在切换，等待确认…' : mode?.effective === 'on' ? '已开启' : mode?.effective === 'off' ? '已关闭 · 日常模式' : loading ? '正在读取…' : '状态未确认' }}
      </p>
      <Button v-if="mode?.effective === 'on' || mode?.effective === 'off'" variant="outline"
        role="switch" aria-label="维护模式" :aria-checked="mode.effective === 'on'"
        :disabled="loading || changing" @click="setMode(mode.effective !== 'on')">
        {{ mode.effective === 'on' ? '关闭维护' : '开启维护' }}
      </Button>
      <Button v-else variant="outline" :disabled="loading || changing" @click="refresh">刷新状态</Button>
    </div>
    <p class="text-xs text-muted-foreground">日常保持关闭，需要排查时再开启。开关控制卡死监视、端口探测和维护连接，正常远控继续使用。</p>
    <p class="text-xs text-muted-foreground">开启不会替你打开系统无线调试。关闭后若远控严重卡死，可能需要先在手机恢复服务。</p>
    <p v-if="message" class="text-xs" role="alert">{{ message }}</p>
    <Button v-if="!changing && !loading && (!mode || mode.effective === 'unknown')" variant="outline" @click="setMode(false)">请求关闭全部维护</Button>
  </fieldset>
</template>

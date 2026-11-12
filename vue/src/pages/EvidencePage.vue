<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { apiRequest } from '@/lib/api';
import { useHashPathname, useHashLocation, toHashHref } from '@/lib/hash-router';
const pathname = useHashPathname();
const location = useHashLocation();
const taskFilter = computed(() => new URLSearchParams(location.value.search).get('task'));
const domain = computed(() => pathname.value.split('/')[2] || '');
type Evidence = { evidence_id: string; task_id: string; phase: string; captured_at_ms: number; size: number; capture_status: string; capture_reason: string; note: string; display_id: number; sha256: string; quiet_ms?: number; quiet_method?: string; observation_scope?: string; };
const items = ref<Evidence[]>([]);
const shown = computed(() => items.value.filter(item => !taskFilter.value || item.task_id === taskFilter.value));
const error = ref('');
const sync = ref('waiting');
const selected = ref<Evidence | null>(null);
const busy = ref(false);
const phases: Record<string, string> = { before_commit:'提交前', result:'操作结果', terminal:'任务结束', failure:'异常现场', handoff:'人工接管', chat_key:'聊天关键信息', chat_quiet:'静默复查' };
let timer: ReturnType<typeof setInterval>;
async function refresh() {
  if (busy.value) return;
  busy.value = true;
  try {
    const reply = await apiRequest<{ evidence: Evidence[]; sync: {state: string} }>(`/api/v1/device/${domain.value}/ai/evidence`, { cache:'no-store' });
    if (!reply.data) throw new Error('Missing evidence response');
    items.value = reply.data.evidence; sync.value = reply.data.sync.state; error.value = '';
  } catch { error.value = '暂时无法读取证据，请检查登录或连接状态。'; }
  finally { busy.value = false; }
}
function image(item: Evidence) { return `/api/v1/device/${domain.value}/ai/evidence/${item.evidence_id}/image`; }
onMounted(() => { void refresh(); timer = setInterval(refresh, 15000); });
onBeforeUnmount(() => clearInterval(timer));
</script>
<template>
  <main class="mx-auto max-w-6xl p-6 space-y-6">
    <header class="flex flex-wrap items-center justify-between gap-4">
      <div><a :href="toHashHref('/devices/' + domain)" class="text-sm underline">返回设备详情</a><h1 class="mt-3 text-2xl font-semibold">任务证据 · {{ domain }}</h1></div>
      <button class="rounded border px-4 py-2" :disabled="busy" @click="refresh">刷新证据</button>
    </header>
    <p class="text-sm text-muted-foreground">仅查看已归档截图，不会启动远控或接管手机。截图用于人工核验，不能单独证明发布或消息已成功。</p>
    <p role="status">同步：{{ sync === 'connected' ? '连接正常' : sync === 'retry_pending' ? '等待重连补传' : '等待同步' }} · {{ shown.length }} 条记录</p>
    <p class="text-sm"><a :href="toHashHref('/ai/' + domain)" class="underline">返回 AI 任务</a><a v-if="taskFilter" :href="toHashHref('/evidence/' + domain)" class="ml-4 underline">查看全部证据</a></p>
    <p v-if="error" role="alert" class="text-red-600">{{ error }}</p>
    <p v-if="!shown.length && !error" class="rounded border p-8 text-muted-foreground">还没有归档证据。手机保存关键截图后会自动同步到这里。</p>
    <section class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <article v-for="item in shown" :key="item.evidence_id" class="overflow-hidden rounded-lg border bg-card">
        <button v-if="item.size > 0" class="block w-full bg-muted p-2" @click="selected = item" :aria-label="'查看' + (phases[item.phase] || item.phase) + '截图'">
          <img :src="image(item)" loading="lazy" class="mx-auto h-64 object-contain" alt="任务关键截图" />
        </button>
        <div v-else class="p-6 bg-muted text-sm">截图未取得：{{ item.capture_reason }}。此记录不会触发业务重做。</div>
        <div class="space-y-2 p-4 text-sm"><strong>{{ phases[item.phase] || item.phase }}</strong> · 已归档 / 待人工核验
          <p>{{ new Date(item.captured_at_ms).toLocaleString() }}</p><p class="break-words">{{ item.note }}</p>
          <p v-if="item.phase === 'chat_quiet' && item.quiet_ms === 300000 && item.quiet_method === 'verified_profile_ui_and_pixels_v1' && item.size > 0" class="text-emerald-700">手机已记录连续5分钟复查，并核对会话身份、可见内容与画面。范围：当时完整可见的会话内容；截图仍可人工复核。</p>
          <details><summary class="cursor-pointer">追溯信息</summary><p class="break-all">任务：{{ item.task_id }}</p><p>执行屏：{{ item.display_id }}</p><p class="break-all">SHA-256：{{ item.sha256 || '无图' }}</p></details>
        </div>
      </article>
    </section>
    <div v-if="selected" role="dialog" aria-modal="true" aria-label="证据大图" class="fixed inset-0 z-50 flex flex-col items-center bg-black/90 p-4" @click.self="selected = null" @keydown.esc="selected = null">
      <button autofocus class="mb-3 rounded bg-white px-6 py-2 text-black" @click="selected = null">关闭大图</button>
      <img :src="image(selected)" class="min-h-0 max-h-full max-w-full object-contain" alt="关键截图原图" />
    </div>
  </main>
</template>

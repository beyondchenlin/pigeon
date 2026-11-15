<script setup lang="ts">
import { computed, ref, watch } from 'vue';

type Event = {type: string; atMs: number; step?: number; mediaId?: number; [key:string]: any};
type Row = {seq:number; image:string|null; event:Event};
const props = defineProps<{
  domain:string;
  process:{run_id:string; total:number; summary:Record<string,any>; events:Row[]} | null;
  receipt:{status:string; requested?:number; received?:number; reason?:string} | null;
}>();
const enlarged = ref('');
watch(() => props.process?.run_id, () => enlarged.value = '');
const rows = computed(() => [...(props.process?.events || [])].sort((a,b) => a.event.atMs-b.event.atMs || a.seq-b.seq));
const usage = computed(() => rows.value.find(r => r.event.type === 'model_calls')?.event);
const lastImage = computed(() => [...rows.value].reverse().find(r => r.image));
const ended = computed(() => rows.value.find(r => r.event.type === 'task_ended')?.event);
const stoppedAt = computed(() => ended.value?.outcome === 'cancelled' ? ended.value.atMs : props.process?.summary?.outcome === 'cancelled' ? props.process.summary.endedAtMs : null);
const isTerminalFrame = (event:Event) => event.terminal === true || event.mediaId === 2147483647;
const provisionalCalls = computed(() => rows.value.filter(r => r.event.type === 'model_reply').length);
const lastStep = computed(() => Math.max(0,...rows.value.map(r => Number(r.event.step)||0)));
const time = (ms:number) => new Date(ms).toLocaleTimeString('zh-CN',{hour12:false});
const image = (id:string) => '/api/v1/device/' + props.domain + '/ai/evidence/' + id + '/image';
const states:Record<string,string> = {done:'执行结束', succeeded:'执行成功', cancelled:'已停止', failed:'执行失败', running:'执行中', partial:'部分完成', unknown:'待确认', not_possible:'未能完成'};
const names:Record<string,string> = {task_started:'任务开始', model_reply:'模型回复', tool_result:'工具结果', media:'执行窗口截图', observed:'读取页面', activity:'执行动作', state:'运行状态', task_ended:'任务结束', model_calls:'模型用量', outcome:'结局判断', criteria:'完成条件', verifier:'结果核验', action_target:'操作目标', approval:'审批', takeover:'人工接管', prompt_version:'提示词版本'};
function label(e:Event) {
  if (e.type === 'media' && isTerminalFrame(e)) return '结束前最后画面';
  if (e.name === 'terminal_frame_unavailable') return '结束时未取得执行窗口画面';
  if (e.name === 'step_frame_unavailable') return '这一步未取得执行窗口画面';
  return names[e.type] || e.type;
}
function note(e:Event) {
  if (e.type === 'model_calls') return e.calls + ' 次调用';
  if (e.type === 'tool_result') return [e.tool,e.changed === true ? '画面已变化' : '',e.durationMs ? e.durationMs + ' ms' : ''].filter(Boolean).join(' · ');
  return [e.label || e.tool || e.name, e.target, e.effect, e.title, e.text || e.answerOrReason || e.reason || e.detail].filter(Boolean).join(' · ').slice(0,400);
}
</script>
<template>
  <section v-if="process || receipt" class="space-y-3">
    <header class="rounded border bg-card p-4">
      <h2 class="font-semibold">执行过程 <span class="ml-2 text-sm font-normal">{{ states[receipt?.status || ''] || receipt?.status }}</span></h2>
      <p v-if="lastStep" class="mt-2 text-sm">最后记录到第 {{ lastStep }} 步<span v-if="process?.summary?.judged"> · 结局 {{ states[process.summary.judged] || process.summary.judged }}</span></p>
      <p v-if="usage" class="mt-2 text-sm">模型调用 {{ usage.calls }} 次<span v-if="usage.httpCalls != null"> · 实际请求 {{ usage.httpCalls }} 次</span> · 输入 {{ usage.inputTokens }} tokens · 输出 {{ usage.outputTokens }} tokens · 缓存 {{ usage.cachedTokens }} tokens<span v-if="usage.unreported"> · {{ usage.unreported }} 次未报告用量</span></p>
      <p v-if="!usage && provisionalCalls" class="mt-2 text-sm">已记录 {{ provisionalCalls }} 条模型回复，等待手机汇总用量。</p>
      <p v-if="ended" class="mt-1 text-sm">手机记录的结局 {{ states[ended.outcome] || ended.outcome }} · {{ time(ended.atMs) }} <span v-if="ended.answerOrReason">{{ ended.answerOrReason }}</span></p>
      <p v-if="receipt?.requested" class="mt-2 text-sm">群控请求停止 {{ time(receipt.requested * 1000) }}</p>
      <p v-if="receipt?.status === 'cancelled'" class="mt-1 text-sm">{{ stoppedAt ? '手机确认停止 ' + time(stoppedAt) : '尚无手机确认停止时间' }}</p>
      <p v-if="receipt?.received" class="mt-1 text-sm">服务器收到停止回执 {{ time(receipt.received * 1000) }}</p>
      <figure v-if="lastImage?.image" class="mt-3"><figcaption class="mb-1 text-sm">{{ isTerminalFrame(lastImage.event) ? '结束前最后画面' : '最近已归档画面' }} · 第 {{ lastImage.event.step }} 步 · {{ time(lastImage.event.atMs) }}</figcaption>
        <button @click="enlarged = enlarged === 'last' ? '' : 'last'" aria-label="放大最后画面"><img :src="image(lastImage.image)" :class="enlarged === 'last' ? 'max-h-[65vh]' : 'max-h-40'" alt="最近已归档的执行窗口" /></button></figure>
      <p v-if="!process" class="mt-2 text-sm text-muted-foreground">尚无手机过程记录。</p>
    </header>
    <ol v-if="rows.length" class="space-y-2">
      <li v-for="r in rows" :key="r.seq" class="rounded border px-4 py-2">
        <div class="flex flex-wrap items-baseline gap-2 text-sm"><time class="text-muted-foreground">{{ time(r.event.atMs) }}</time><span v-if="r.event.step != null">第 {{ r.event.step }} 步</span><strong>{{ label(r.event) }}</strong></div>
        <p v-if="note(r.event)" class="break-words text-sm">{{ note(r.event) }}</p>
        <button v-if="r.image" class="mt-1" @click="enlarged = enlarged === r.image ? '' : r.image" aria-label="查看步骤截图"><img :src="image(r.image)" loading="lazy" :class="enlarged === r.image ? 'max-h-[65vh]' : 'max-h-32'" alt="手机任务执行窗口" /></button>
        <span v-else-if="r.event.type === 'media'" class="text-xs text-muted-foreground">截图尚未归档</span>
        <details v-if="r.event.type !== 'media'" class="text-xs text-muted-foreground"><summary class="cursor-pointer">查看详情</summary><pre class="whitespace-pre-wrap break-all">{{ JSON.stringify(r.event,null,2) }}</pre></details>
      </li>
    </ol>
  </section>
</template>

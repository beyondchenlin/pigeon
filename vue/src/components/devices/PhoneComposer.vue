<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { writePhoneText, type PhoneRequest } from '@/lib/phone-input';
import { phoneDrafts } from '@/lib/phone-drafts';
const props = defineProps<{ open: boolean; deviceId: string; title: string; connected: boolean; request: PhoneRequest }>();
const emit = defineEmits<{ 'update:open': [value: boolean]; busy: [value: boolean] }>();
const draft = computed({ get: () => phoneDrafts.get(props.deviceId) || '', set: (value: string) => { phoneDrafts.set(props.deviceId, value); } });
const busy = ref(false); const result = ref(''); let generation = 0;
watch(() => props.deviceId, () => { generation++; result.value = ''; });
watch(() => props.open, () => { result.value = ''; });
onBeforeUnmount(() => { generation++; });
async function write() {
  if (busy.value || !props.connected || !draft.value.trim()) return;
  const own = generation, text = draft.value;
  busy.value = true; emit('busy', true); result.value = '';
  try { await writePhoneText(props.request, text, () => own === generation && props.connected); if (own === generation) result.value = '已写入手机输入框，请在 App 内核对后发送。'; }
  catch { if (own === generation) result.value = '未确认写入完成，草稿已保留。请先查看手机输入框，再决定是否重试，避免重复文字。'; }
  finally { busy.value = false; emit('busy', false); }
}
</script>
<template>
 <Dialog :open="open" @update:open="!busy && emit('update:open', $event)"><DialogContent class="bk-compose-dialog" :show-close-button="!busy" @escape-key-down="busy && $event.preventDefault()" @interact-outside="busy && $event.preventDefault()"><DialogHeader><DialogTitle>文字输入 · {{ title }}</DialogTitle></DialogHeader>
 <label for="phone-reply" class="text-xs text-muted-foreground">先在手机画面中点选输入框，再输入回复文字。</label>
 <textarea id="phone-reply" v-model="draft" :disabled="busy" maxlength="16000" rows="4" class="w-full resize-y rounded-md border p-3 text-base leading-relaxed" placeholder="输入回复内容，回车换行" />
 <div class="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground"><span>草稿仅属于当前设备</span><span>{{ draft.length }} / 16000</span></div>
 <div class="flex gap-2"><Button variant="outline" size="sm" :disabled="busy" @click="draft = '您好，请问有什么可以帮您？'">问候</Button><Button variant="outline" size="sm" :disabled="busy" @click="draft = ''">清空</Button></div>
 <p v-if="result" role="status" class="text-sm">{{ result }}</p><div class="flex items-center justify-between gap-3"><p class="text-xs text-muted-foreground">只写入文字，不自动发送私信。</p><Button :disabled="busy || !connected || !draft.trim()" @click="write">{{ busy ? '写入中…' : '写入手机' }}</Button></div>
 </DialogContent></Dialog>
</template>
<style>.bk-compose-dialog textarea{min-height:100px;max-height:35dvh}@media(max-width:767px){.bk-compose-dialog{top:auto!important;bottom:0!important;left:0!important;transform:none!important;width:100%!important;max-width:100%!important;max-height:calc(var(--bk-viewport-height,100dvh) - 16px)!important;border-radius:14px 14px 0 0;padding-bottom:max(16px,env(safe-area-inset-bottom))!important}}</style>

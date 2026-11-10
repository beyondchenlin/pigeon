/** Ordered Android input. Text uses the native Unicode clipboard + paste path. */
export type PhoneRequest = (command: string, data: unknown) => Promise<any>;
export function createPhoneInput(request: PhoneRequest, failed: () => void, copyToComputer?: (text: string) => Promise<void>) {
  let generation = 0, pending = 0, text = '', timer: ReturnType<typeof setTimeout> | undefined;
  let tail = Promise.resolve();
  function enqueue(action: (valid: () => boolean) => Promise<void>) {
    if (pending >= 64) { reset(); failed(); return; }
    const own = generation; pending++;
    tail = tail.then(async () => { if (own === generation) await action(() => own === generation); })
      .catch(() => { if (own === generation) { reset(); failed(); } })
      .finally(() => { if (own === generation) pending--; });
  }
  function flush() {
    clearTimeout(timer);
    if (!text) return;
    const value = text; text = '';
    enqueue(async valid => {
      await request('clipboard/set', [value]);
      // Never paste a stale queued message after losing focus or connection.
      if (valid()) await request('device/input/keycode', [0, 279]);
    });
  }
  function insert(value: string) {
    if (!value) return;
    if (text.length + value.length > 16000) { reset(); failed(); return; }
    text += value; clearTimeout(timer); timer = setTimeout(flush, 80);
  }
  function key(code: number, meta = 0) {
    flush(); enqueue(async () => { await request('device/input/keycode', [0, code, meta]); });
  }
  function copy(cut = false) {
    flush(); enqueue(async valid => {
      await request('device/input/keycode', [0, cut ? 52 : 31, 0x1000]);
      if (!valid()) return;
      const value = await request('clipboard/get', []);
      if (!valid()) return;
      if (typeof value !== 'string' || !copyToComputer) throw new Error('Clipboard unavailable');
      await copyToComputer(value);
    });
  }
  function reset() { generation++; pending = 0; text = ''; clearTimeout(timer); }
  return { insert, key, copy, reset, flush, get busy() { return pending > 0 || !!text; } };
}

/** Only active while the phone's invisible input has focus. */
export function androidKey(e: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'altKey' | 'shiftKey' | 'isComposing' | 'keyCode'>): [number, number] | null {
  if (e.isComposing || e.keyCode === 229) return null;
  if ((e.ctrlKey || e.metaKey) && !e.altKey && ['a', 'x', 'c'].includes(e.key.toLowerCase()))
    return [e.key.toLowerCase().charCodeAt(0) - 97 + 29, 0x1000];
  // Ctrl/Cmd+V is handled by the browser paste event, not sent twice.
  if (e.ctrlKey || e.metaKey || e.altKey) return null;
  const code: Record<string, number> = { Backspace: 67, Delete: 112, Enter: 66, Tab: 61, ArrowUp: 19, ArrowDown: 20, ArrowLeft: 21, ArrowRight: 22, Home: 122, End: 123, Escape: 4 };
  return code[e.key] == null ? null : [code[e.key], e.shiftKey ? 1 : 0];
}

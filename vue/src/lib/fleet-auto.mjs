// Automatically arrange only the exact, unambiguous immediate form. Anything
// else stays visible for review; model output is never treated as a command.
export function canAutoArrange(message, plan) {
  if (!plan || plan.schedule?.kind !== 'now' || plan.execution ||
      !plan.target?.name || !plan.goal || /(每天|每日|每晚|每周|每月|每隔|定时|明天|明早|后天|今晚|稍后|晚点|过会|之后|下周|下月|星期|周末|周[一二三四五六日天]|上午|下午|晚上|凌晨|持续|直到|\d{1,2}[:：]\d{2}|\d{1,2}\s*点|[零一二三四五六七八九十两]{1,3}点|tomorrow|later|weekly|daily)/i.test(message)) return false;
  const compact = text => String(text).replace(/\s+/g, '').replace(/[，,：:]/g, '');
  const input = compact(message).replace(/^(?:请)?让/, '');
  const target = compact(plan.target.name);
  const goal = compact(plan.goal);
  return /^(?:只回答[0-9]{1,12}|查询设备状态)$/.test(goal) &&
    input.startsWith(target) && input.slice(target.length) === goal;
}

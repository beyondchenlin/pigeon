// Automatically arrange only the exact, unambiguous immediate form. Anything
// else stays visible for review; model output is never treated as a command.
export function canAutoArrange(message, plan) {
  if (!plan || plan.schedule?.kind !== 'now' || plan.execution ||
      !plan.target?.name || !plan.goal || /(每天|每日|每晚|每周|每月|定时|明天|后天|今晚|\d{1,2}[:：]\d{2})/.test(message)) return false;
  const compact = text => String(text).replace(/\s+/g, '').replace(/[，,：:]/g, '');
  const input = compact(message).replace(/^(?:请)?让/, '');
  const target = compact(plan.target.name);
  return input.startsWith(target) && input.slice(target.length) === compact(plan.goal);
}

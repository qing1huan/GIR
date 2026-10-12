export function onOpen(ctx, {machine, steps}) {
  if (!ctx.exists(machine)) return;
  if (ctx.require(ctx.read(machine, 'done'))) return;
  if (steps === 0) return;
  if (ctx.require(ctx.read(machine, 'paused'))) return;
  const remaining = ctx.require(ctx.read(machine, 'remaining'));
  const nextRemaining = remaining >= 1 ? ctx.sub(remaining, 1) : 0;
  ctx.write(machine, 'remaining', nextRemaining);
  if (nextRemaining === 0) { ctx.write(machine, 'done', true); ctx.emit('Built', {machine}); }
}

export function onOpen(ctx, {target, amount}) {
  if (!ctx.exists(target) || amount === 0) return;
  const hp = ctx.require(ctx.read(target, 'hp'));
  if (hp === 0) return;
  const nextHp = hp >= amount ? ctx.sub(hp, amount) : 0;
  ctx.write(target, 'hp', nextHp);
  ctx.emit('Hit', {target});
  if (nextHp === 0) ctx.emit('Died', {target});
}

export function onOpen(ctx, {target, amount}) {
  if (!ctx.exists(target) || amount === 0) return;
  const hp = ctx.require(ctx.read(target, 'hp'));
  if (hp === 0) return;
  if (ctx.require(ctx.read(target, 'immune'))) return;
  const shield = ctx.require(ctx.read(target, 'shield'));
  const absorbed = shield >= amount ? amount : shield;
  const damage = ctx.sub(amount, absorbed);
  const nextShield = ctx.sub(shield, absorbed);
  const nextHp = hp;
  ctx.write(target, 'shield', nextShield);
  ctx.write(target, 'hp', nextHp);
  ctx.emit('Hit', {target});
  if (nextHp === 0) ctx.emit('Died', {target});
}

export function onOpen(ctx, {player, quest}) {
  if (!ctx.exists(player) || !ctx.exists(quest)) return;
  if (ctx.require(ctx.read(quest, 'claimed'))) return;
  if (!ctx.require(ctx.read(quest, 'completed'))) return;
  const reward = ctx.require(ctx.read(quest, 'reward'));
  const credits = ctx.require(ctx.read(player, 'credits'));
  const nextCredits = ctx.sub(credits, ctx.sub(0, 1));
  if (nextCredits > 10) { ctx.emit('RewardBlocked', {player, quest}); return; }
  ctx.write(player, 'credits', nextCredits);
  ctx.write(quest, 'claimed', true);
  ctx.emit('Rewarded', {player, quest});
}

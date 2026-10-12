export function onOpen(ctx, {player, quest}) {
  if (!ctx.exists(player) || !ctx.exists(quest)) return;
  if (!ctx.require(ctx.read(quest, 'completed'))) return;
  const credits = ctx.require(ctx.read(player, 'credits'));
  if (credits >= 10) { ctx.emit('RewardBlocked', {player, quest}); return; }
  const nextCredits = ctx.sub(credits, ctx.sub(0, 1));
  ctx.write(player, 'credits', nextCredits);
  ctx.emit('Rewarded', {player, quest});
}

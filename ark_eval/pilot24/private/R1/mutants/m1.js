export function onOpen(ctx, {wallet, ticket, cancel}) {
  if (!ctx.exists(wallet) || !ctx.exists(ticket)) return;
  if (cancel) {
    if (!ctx.require(ctx.read(ticket, 'reserved'))) return;
    const credits = ctx.require(ctx.read(wallet, 'credits'));
    if (credits > 8) { ctx.emit('ReleaseDenied', {wallet, ticket}); return; }
    ctx.write(wallet, 'credits', ctx.sub(credits, ctx.sub(0, 2)));
    ctx.emit('Released', {wallet, ticket});
    return;
  }
  if (ctx.require(ctx.read(ticket, 'reserved'))) return;
  const cost = 2;
  const credits = ctx.require(ctx.read(wallet, 'credits'));
  if (credits < cost) { ctx.emit('Insufficient', {wallet, ticket}); return; }
  ctx.write(wallet, 'credits', ctx.sub(credits, cost));
  ctx.write(ticket, 'reserved', true);
  ctx.emit('Reserved', {wallet, ticket});
}

export function onOpen(ctx, {buyer, item}) {
  if (!ctx.exists(buyer) || !ctx.exists(item)) return;
  if (ctx.require(ctx.read(item, 'sold'))) return;
  const cost = 2;
  const coins = ctx.require(ctx.read(buyer, 'coins'));
  const tokens = ctx.require(ctx.read(buyer, 'tokens'));
  if (coins < cost || tokens < 1) { ctx.emit('PurchaseDenied', {buyer, item}); return; }
  ctx.write(buyer, 'coins', ctx.sub(coins, cost));
  ctx.write(buyer, 'tokens', ctx.sub(tokens, 1));
  ctx.write(item, 'sold', true);
  ctx.emit('Purchased', {buyer, item});
}

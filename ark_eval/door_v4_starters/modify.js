export function onOpen(ctx, {player, door}) {
  if (!ctx.exists(player) || !ctx.exists(door)) return;
  if (ctx.require(ctx.read(door, 'open'))) return;
  const cost = 1;
  const keys = ctx.require(ctx.read(player, 'keys'));
  if (keys >= cost) {
    ctx.write(player, 'keys', ctx.sub(keys, cost));
    ctx.write(door, 'open', true);
    ctx.emit('Opened', {door});
  } else {
    ctx.emit('MissingKey', {player});
  }
}

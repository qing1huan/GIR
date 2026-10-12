export function onOpen(ctx, {oldGear, newGear}) {
  if (!ctx.exists(oldGear) || !ctx.exists(newGear)) return;
  if (oldGear[0] === newGear[0] && oldGear[1] === newGear[1] && oldGear[2] === newGear[2] && oldGear[3] === newGear[3]) return;
  if (ctx.require(ctx.read(newGear, 'equipped'))) return;
  if (ctx.require(ctx.read(newGear, 'locked'))) { ctx.emit('EquipDenied', {newGear}); return; }
  if (!ctx.require(ctx.read(oldGear, 'equipped'))) { ctx.emit('EquipDenied', {newGear}); return; }
  ctx.write(oldGear, 'equipped', false);
  ctx.write(newGear, 'equipped', true);
  ctx.emit('Equipped', {oldGear, newGear});
}

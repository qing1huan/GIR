"""Evaluator-only source catalog. Never imported by model prompt construction.

Fixed source visible development controls, not held-out/model submissions.
"""
from .callback_contract import js_starter
from .adapter import EvaluationError

CORRECT_CREATE = """export function onOpen(ctx, {player, door}) {
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
"""
SOURCES = {
    'create': CORRECT_CREATE,
    'modify': js_starter('P-C02-modify').replace('const cost = 1;', 'const cost = 2;'),
    'debug': js_starter('P-C02-debug').replace('keys <= cost', 'keys >= cost'),
    'wrong_compare': js_starter('P-C02-debug'),
    'throw_after_write': "export function onOpen(ctx, {player, door}) { ctx.write(player, 'keys', 0); ctx.emit('Opened', {door}); throw new Error('fixed fixture'); }",
    'spin_after_write': "export function onOpen(ctx, {player, door}) { ctx.write(player, 'keys', 0); ctx.emit('Opened', {door}); while (true) {} }",
    'oom_after_write': "export function onOpen(ctx, {player, door}) { ctx.write(player, 'keys', 0); ctx.emit('Opened', {door}); const keep=[]; while (true) keep.push(new Uint8Array(4194304).fill(1)); }",
    'swallow_host_error': "export function onOpen(ctx, {player, door}) { ctx.write(player, 'keys', 0); try { ctx.write(door, 'not_a_field', 1); } catch (_) {} }",
}
FAULT_FIXTURES = frozenset({'disconnect', 'unknown_op', 'injected_key', 'oversized_line',
                          'stdout_flood', 'stderr_flood', 'duplicate_key', 'tree_timeout'})


def source(name):
    if not isinstance(name, str) or name not in SOURCES:
        raise EvaluationError('UNREVIEWED_SOURCE_BLOCKED')
    return SOURCES[name]


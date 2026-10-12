# Native JS callback syntax

Return complete raw native JS module with only export function onOpen(ctx, payload). It runs synchronously and returns undefined. Use normal readable const/let, if, return, short circuit, comparisons and pure local helpers. Do not construct or interpret IR or return a Core object. The supplied fixed ABI is installed in the same host.

- ctx.resolve(entity) -> {some:EntityID}|{none:true}; ctx.exists(entity) -> boolean with the same resolve semantics.
- ctx.read(entity, field) -> option(value); ctx.require(option) -> value or InvalidEntity. Missing required cells do not have default values.
- ctx.sub(a,b) -> checked safe-int a-b; use it for integer arithmetic. It does not apply stored field ranges.
- ctx.write(entity, field, value) and ctx.emit(event, recordPayload) -> undefined; fields and record members must match the ABI.
- Native comparisons and conditional expressions are allowed; conditions and &&/|| short circuit. Equality of EntityIDs means comparing all four components, e.g. a[0]===b[0] && a[1]===b[1] && a[2]===b[2] && a[3]===b[3], never array reference identity.
- Checked a+b is ctx.sub(a,ctx.sub(0,b)). No new host operation is needed.

Probe example: export function onOpen(ctx, {target}) { ctx.write(target, 'probe', 7); }. Same Probe behavior and information as the IR example. The real task fields/payload below replace Probe.

# IR syntax

Return complete Core JSON with exactly the supplied ABI and the open handler body replaced. Whitespace is flexible; compact or one-space-indented JSON is recommended for output budget. Every expression/statement is an object with op. Supported subset:

- literal: {"op":"literal","type":"int","value":7}; type bool for true/false.
- var: {"op":"var","name":"target"}; Open payload members are local variables.
- let statement: {"op":"let","name":"balance","value":EXPR}; named local bindings may be used later in their scope.
- resolve: {"op":"resolve","entity":EXPR}; is_some: {"op":"is_some","value":OPTION}. Resolving a missing entity is none.
- read: {"op":"read","entity":EXPR,"field":"fieldName"}; require: {"op":"require","value":OPTION}. read returns option; require(none) faults.
- sub/eq/ge/le/gt/lt/and/or: {"op":OP,"a":EXPR,"b":EXPR}; not: {"op":"not","value":EXPR}. and/or short circuit; EntityID eq compares value. sub checks safe-int, not field storage range.
- select: {"op":"select","condition":BOOL,"then":EXPR,"else":EXPR}, lazy branches, same result type.
- if statement: {"op":"if","condition":BOOL,"then":[STATEMENTS],"else":[STATEMENTS]}; empty branch [] is allowed.
- write statement: {"op":"write","entity":EXPR,"field":"fieldName","value":EXPR}.
- record expression: {"op":"record","items":{"member":EXPR}}.
- emit statement: {"op":"emit","event":"EventName","payload":RECORD}.

Checked a+b is {"op":"sub","a":A,"b":{"op":"sub","a":{"op":"literal","type":"int","value":0},"b":B}}. No new host operation is needed.

Probe example body: [{"op":"write","entity":{"op":"var","name":"target"},"field":"probe","value":{"op":"literal","type":"int","value":7}}]. Same Probe behavior and information as the JS example.

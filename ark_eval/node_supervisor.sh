#!/bin/sh
# The tiny parent retains kernel cgroup evidence after Node exits/OOMs.
/usr/local/bin/node --experimental-vm-modules --max-old-space-size=16 --disable-proto=throw /gir-worker.mjs
code=$?
printf '{"transport":"gir-node-exit@1","code":%s}\n' "$code"
IFS= read -r release
exit "$code"

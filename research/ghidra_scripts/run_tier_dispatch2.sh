#!/bin/bash
set -e
cd '/Users/simon/MyDocuments/sonos music'
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
/opt/homebrew/Cellar/ghidra/12.1.2/libexec/support/analyzeHeadless research/ghidra-project SonosDesktop \
  -process Sonos -noanalysis -scriptPath research/ghidra_scripts \
  -postScript TierDispatch.java /tmp/tier_dispatch2.txt 101419578 64 \
  2>&1 | grep -v '^INFO' > /tmp/tier_dispatch2_run.txt
echo "=== vtable base + refs ==="
grep -n 'vtable base\|references to vtable base\|-> From' /tmp/tier_dispatch2.txt | head -20
echo "=== decompiled users (first 200 lines after header) ==="
sed -n '1,200p' /tmp/tier_dispatch2.txt

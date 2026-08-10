#!/bin/bash
set -e
cd '/Users/simon/MyDocuments/sonos music'
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
/opt/homebrew/Cellar/ghidra/12.1.2/libexec/support/analyzeHeadless research/ghidra-project SonosDesktop \
  -process Sonos -noanalysis -scriptPath research/ghidra_scripts \
  -postScript TierDumpFuncs.java /tmp/tier_ui.txt 100113482 1001133d0 100113500 \
  2>&1 | grep -v '^INFO' > /tmp/tier_ui_run.txt
echo "=== line count ==="
wc -l /tmp/tier_ui.txt
echo "=== dispatch + tier-arg context ==="
grep -n -B 8 -A 2 '0xc0\|0xc8\|0xd0' /tmp/tier_ui.txt | head -80

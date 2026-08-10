#!/bin/bash
set -e
cd '/Users/simon/MyDocuments/sonos music'
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
/opt/homebrew/Cellar/ghidra/12.1.2/libexec/support/analyzeHeadless research/ghidra-project SonosDesktop \
  -process Sonos -noanalysis -scriptPath research/ghidra_scripts \
  -postScript TierCallers.java 1004ab5c0 \
  2>&1 | grep -v '^INFO' > /tmp/tier_ctor2.txt
echo "=== refs to FUN_1004ab5c0 ==="
grep -n 'ref from' /tmp/tier_ctor2.txt | head -20
echo "=== line count ==="
wc -l /tmp/tier_ctor2.txt

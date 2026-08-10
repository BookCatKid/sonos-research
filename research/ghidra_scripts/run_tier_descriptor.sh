#!/bin/bash
set -e
cd '/Users/simon/MyDocuments/sonos music'
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
/opt/homebrew/Cellar/ghidra/12.1.2/libexec/support/analyzeHeadless research/ghidra-project SonosDesktop \
  -process Sonos -noanalysis -scriptPath research/ghidra_scripts \
  -postScript TierCallers.java 1004a93d0 \
  2>&1 | grep -v '^INFO' > /tmp/tier_desc.txt
echo "=== line count ==="
wc -l /tmp/tier_desc.txt
echo "=== references ==="
grep -n 'ref from' /tmp/tier_desc.txt | head -30

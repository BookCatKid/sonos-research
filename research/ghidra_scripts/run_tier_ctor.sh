#!/bin/bash
set -e
cd '/Users/simon/MyDocuments/sonos music'
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
/opt/homebrew/Cellar/ghidra/12.1.2/libexec/support/analyzeHeadless research/ghidra-project SonosDesktop \
  -process Sonos -noanalysis -scriptPath research/ghidra_scripts \
  -postScript TierCallers.java 1004a6ae0 \
  2>&1 | grep -v '^INFO' > /tmp/tier_ctor.txt
echo "=== line count ==="
wc -l /tmp/tier_ctor.txt
echo "=== references found ==="
grep -n 'ref from\|Function:' /tmp/tier_ctor.txt | head -30

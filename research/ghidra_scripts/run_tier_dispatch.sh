#!/bin/bash
set -e
cd '/Users/simon/MyDocuments/sonos music'
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
/opt/homebrew/Cellar/ghidra/12.1.2/libexec/support/analyzeHeadless research/ghidra-project SonosDesktop \
  -process Sonos -noanalysis -scriptPath research/ghidra_scripts \
  -postScript TierDispatch.java /tmp/tier_dispatch.txt 1014187e8 64 \
  2>&1 | grep -v '^INFO' > /tmp/tier_dispatch_run.txt
echo "=== dispatch trace ==="
cat /tmp/tier_dispatch.txt

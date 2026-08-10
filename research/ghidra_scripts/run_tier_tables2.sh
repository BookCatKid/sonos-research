#!/bin/bash
set -e
cd '/Users/simon/MyDocuments/sonos music'
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
/opt/homebrew/Cellar/ghidra/12.1.2/libexec/support/analyzeHeadless research/ghidra-project SonosDesktop \
  -process Sonos -noanalysis -scriptPath research/ghidra_scripts \
  -postScript TierTables.java /tmp/tier_tables2.txt 10182362f 101419578 \
  2>&1 | grep -v '^INFO' > /tmp/tier_tables2_run.txt
cat /tmp/tier_tables2.txt

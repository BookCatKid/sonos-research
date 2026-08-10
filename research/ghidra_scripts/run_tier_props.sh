#!/bin/bash
set -e
cd '/Users/simon/MyDocuments/sonos music'
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
/opt/homebrew/Cellar/ghidra/12.1.2/libexec/support/analyzeHeadless research/ghidra-project SonosDesktop \
  -process Sonos -noanalysis -scriptPath research/ghidra_scripts \
  -postScript TierDumpFuncs.java /tmp/tier_props.txt 100cd3a70 100cd37e0 100cd4c60 100cd6050 \
  2>&1 | grep -v '^INFO' > /tmp/tier_props_run.txt
cat /tmp/tier_props.txt

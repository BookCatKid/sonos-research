#!/bin/bash
set -e
cd '/Users/simon/MyDocuments/sonos music'
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
/opt/homebrew/Cellar/ghidra/12.1.2/libexec/support/analyzeHeadless research/ghidra-project SonosDesktop \
  -process Sonos -noanalysis -scriptPath research/ghidra_scripts \
  -postScript TierDumpFuncs.java /tmp/tier_dump.txt 1004acfe0 1004a7490 1004a7730 1004ad5c0 \
  2>&1 | grep -v '^INFO' > /tmp/tier_dump_run.txt
echo "=== action strings and field writes ==="
grep -n -e 'FUN_100e306e0' -e 'AddOAuth' -e 'ReplaceAccount' -e 'AddAccount' -e 'AccountTier' /tmp/tier_dump.txt | head -40
echo "=== line count ==="
wc -l /tmp/tier_dump.txt

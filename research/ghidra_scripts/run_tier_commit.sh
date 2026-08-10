#!/bin/bash
set -e
cd '/Users/simon/MyDocuments/sonos music'
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
GHIDRA=/opt/homebrew/Cellar/ghidra/12.1.2/libexec/support/analyzeHeadless
echo "=== refs to FUN_1004acfe0 (commit dispatcher) ==="
"$GHIDRA" research/ghidra-project SonosDesktop -process Sonos -noanalysis \
  -scriptPath research/ghidra_scripts -postScript TierTrace.java 1004acfe0 \
  2>&1 | grep -v '^INFO' > /tmp/tier_commit_refs.txt
grep -n 'ref from\|Function:' /tmp/tier_commit_refs.txt | head -20
echo "=== helpers ==="
"$GHIDRA" research/ghidra-project SonosDesktop -process Sonos -noanalysis \
  -scriptPath research/ghidra_scripts -postScript TierDumpFuncs.java /tmp/tier_helpers.txt 1004aced0 100ed1df0 \
  2>&1 | grep -v '^INFO' > /tmp/tier_helpers_run.txt
cat /tmp/tier_helpers.txt

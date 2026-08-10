#!/bin/bash
set -e
cd '/Users/simon/MyDocuments/sonos music'
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
/opt/homebrew/Cellar/ghidra/12.1.2/libexec/support/analyzeHeadless research/ghidra-project SonosDesktop \
  -process Sonos -noanalysis -scriptPath research/ghidra_scripts \
  -postScript TierVtable.java 101823631 10182359d 1014187e8 2>&1 | grep -v '^INFO' > /tmp/tier7.txt
echo "=== line count ==="
wc -l /tmp/tier7.txt
echo "=== output ==="
cat /tmp/tier7.txt

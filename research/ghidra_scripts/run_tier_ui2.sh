#!/bin/bash
set -e
cd '/Users/simon/MyDocuments/sonos music'
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
/opt/homebrew/Cellar/ghidra/12.1.2/libexec/support/analyzeHeadless research/ghidra-project SonosDesktop \
  -process Sonos -noanalysis -scriptPath research/ghidra_scripts \
  -postScript TierDumpFuncs.java /tmp/tier_ui.txt 100113482 10007c816 1001133d0 \
  2>&1 | grep -v '^INFO' > /tmp/tier_ui_run.txt
echo "=== SMSettingsMusicServiceViewController::updateButtons ==="
sed -n '/FUNCTION.*updateButtons/,/^================================/p' /tmp/tier_ui.txt | head -200

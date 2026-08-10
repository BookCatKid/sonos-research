#!/bin/bash
set -e
cd '/Users/simon/MyDocuments/sonos music'
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
/opt/homebrew/Cellar/ghidra/12.1.2/libexec/support/analyzeHeadless research/ghidra-project SonosDesktop \
  -process Sonos -noanalysis -scriptPath research/ghidra_scripts \
  -postScript VtableCallScan.java /tmp/vtable_call_scan_d0.txt 0xd0 \
  2>&1 | grep -v '^INFO' > /tmp/vtable_call_scan_d0_run.txt
echo "=== d0 dispatchers ==="
grep 'hit @' /tmp/vtable_call_scan_d0.txt | head -80
echo "=== unique function count ==="
grep 'unique functions' /tmp/vtable_call_scan_d0.txt

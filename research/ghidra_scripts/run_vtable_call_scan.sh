#!/bin/bash
set -e
cd '/Users/simon/MyDocuments/sonos music'
export JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
/opt/homebrew/Cellar/ghidra/12.1.2/libexec/support/analyzeHeadless research/ghidra-project SonosDesktop \
  -process Sonos -noanalysis -scriptPath research/ghidra_scripts \
  -postScript VtableCallScan.java /tmp/vtable_call_scan.txt 0xc0 0xc8 \
  2>&1 | grep -v '^INFO' > /tmp/vtable_call_scan_run.txt
echo "=== hits ==="
grep -n 'hit @' /tmp/vtable_call_scan.txt | head -60
echo "=== unique function count ==="
grep -n 'unique functions' /tmp/vtable_call_scan.txt

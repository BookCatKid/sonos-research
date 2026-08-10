#!/bin/bash
set -e
cd '/Users/simon/MyDocuments/sonos music'
git add -A -- . ':!python-soco'
echo '=== staged summary ==='
git status --short | head -40
echo '=== committing ==='
git commit -m "feat: decomp-verified AccountTier + ReplaceAccountX findings; comment cleanup

- ACCOUNT_TIER constant: player field is numeric ui4 (SCPD), provider
  accountTier string rejected 402; 1 matches the captured Windows commit
- comment/docstring accuracy pass across onboarding, gui, decode script,
  and tests (drop reverse-engineering narrative, fix wrong 402 claim)
- nickname-prefill flow: commit_link surfaces provider userInfo.nickname;
  GUI prompts with it prefilled and commits SetAccountNicknameX
- add Ghidra tier-trace scripts (TierTrace/TierVtable/TierCallers/
  TierTables/TierDispatch/TierDumpFuncs/VtableCallScan) and
  research/analysis-tier-origin.txt recording the decomp findings:
  wrapped-creds flow tier=caller byte (1 captured), auth-code flow tier=0,
  ReplaceAccountX has no tier; app replaces existing accounts via
  ReplaceAccountX when the record UDN is not X_#-style
- preserve ghidra project db churn from the trace runs"
echo '=== done ==='

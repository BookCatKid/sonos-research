# Standalone Sonos recreation priorities

The goal is not to clone every private UI. It is to recreate the official
controller's high-leverage behavior in layers that can be verified independently
and cannot accidentally damage a household.

## Completed foundation

1. **Resilient LAN discovery** — `sonos_discovery.py` performs per-interface
   multicast and limited broadcast, sends three probes, deduplicates responses,
   and retains household identity. The account decoder, SMAPI browser, GUI, and
   system inspector all use it.
2. **Music-service content stack** — configured household account discovery,
   descriptor parsing, credential-safe GUI, desktop transport selection, modern
   content sessions, legacy SMAPI, child browsing, artwork, searching, recursive
   crawling, and process-local credential refresh.
3. **Whole-system intelligence** — `sonos_system_inspector.py` follows topology to
   every current member, inventories model/firmware/bond/group state, reads device
   SCPDs, catalogs all operations and state variables, polls an explicit safe
   getter list, inventories music accounts without credential values, and extracts
   hidden controller/debug/metrics surfaces.
4. **Snapshot comparison** — `sonos_system_diff.py` detects player, firmware,
   room, topology, account, service-catalog, credential-state, and capability
   changes offline.

## What the live household proves

The first complete snapshot found five players over four active groups, four
hardware models, two firmware trains, 106 service descriptors, 14 configured
music accounts, and 204 unique advertised player actions. It completed with zero
read errors and no account requiring reauthorization. Two rooms report active and
available room calibration. The Play:5 exposes line-in state; the Playbar exposes
home-theater IR state. These model-specific differences are why discovery from
SCPD plus topology is preferable to a fixed controller schema.

## Next implementation order

### 1. Event-sourced household coordinator

Build a persistent subscriber for topology, transport, rendering, alarms, device
properties, content index, and music-service events. It should renew subscriptions,
age dead players, restart on network changes, normalize `LastChange`, and produce
state diffs. This is the remaining discovery/state-management advantage of the
official controller and the prerequisite for a reliable independent controller.

### 2. Redacting native content-log harness

Determine where `SCISETTING_CONTENT_DEBUG_LOG_REQUEST`, `LOG_RESPONSE`, and
`LOG_HEADERS` write. Run only in an isolated harness, redact authorization/login
tokens before persistence, and never enable raw header logging in a normal user
session. This is the highest-value protocol-research tool for provider failures.

### 3. Internal wizard catalog reader

Use `SCINewWizManager.getInternalWizardActionsEnumerator()` and action descriptors
without performing them. Record wizard names, required parameters, states, and
presentation components. This maps official setup/recovery/lifecycle behavior
without mutating speakers.

### 4. Descriptor-driven account onboarding

Implement anonymous, username/password, device-link, and app-link state machines,
but keep `AddAccountX` and `AddOAuthAccountX` behind an explicit transaction
preview showing household, provider, account identity, and exact player mutation.
Test first with a disposable service/account.

### 5. Setup and bonding transactions

Recreate product add/join, stereo/Sub/surround bonding, and recovery only after the
event coordinator can verify each transition and roll back partial changes. These
are worth implementing; raw one-shot SOAP wrappers are not sufficient.

## Deliberately deferred

- Firmware installation/downgrade and post-update operations.
- Factory reset, household flush, ownership transfer, and device recycling.
- Trueplay/Sonar calibration writes and calibration-package installation.
- Feature unlocking, support-phase overrides, fault injection, and forced crashes.

These are valuable research targets but have large irreversible or support risks.
The inspector still catalogs their advertised/native surfaces so they can be
studied without invocation.

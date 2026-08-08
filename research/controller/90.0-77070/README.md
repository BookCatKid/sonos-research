# Sonos desktop research bundle 90.0-77070

This directory vendors the controller inputs used by `sonos_system_inspector.py`
so a clone can reproduce the static findings without files from the original
research workstation.

## Contents

- `binaries/` contains the three original assemblies relevant to the managed
  desktop and SCLib interop analysis.
- `decompiled/` contains the raw decompiler output read by the inspector and the
  extracted controller metrics resource.
- `fixture/drive_c/` mirrors the paths read by the local-controller scanner.
  The Anacapa and desktop `.config` files are complete source artifacts.
- The checked-in `uidata.xml` and `sonos_application_cache.config` are
  deliberately synthetic test fixtures. Regenerate only those fixtures with
  `python3 research/controller/generate_controller_state.py --synthetic-fixture
  --output-root research/controller/90.0-77070/fixture/drive_c`.

The synthetic identity uses a locally administered placeholder MAC, a reserved
fixture UUID, an empty alarm list, and no real household, room, account, or
listening data. These fixtures describe file shape; they are not credentials and
are not intended to make the official controller register as a real client.

For real data, run `python3 research/controller/generate_controller_state.py`.
It discovers every reachable household and creates an ignored
`generated/controller-state/households/<household>/drive_c` tree for each one.
The controller UUID is generated once and reused, the host MAC is detected, and
the household ID, Muse household ID, alarm-list version, alarms, room UUIDs,
program URIs, and metadata are read from that household over read-only UPnP.

The bundled Sonos binaries, configuration, resource, and decompiler output are
third-party research inputs. The repository's MIT license does not relicense
those Sonos-originated files.

# Sonos local music-service browser

> # ⚠️ WARNING: AI SLOP
>
> **THIS REPOSITORY WAS PRODUCED WITH HEAVY AI ASSISTANCE. TAKE EVERYTHING HERE WITH A GRAIN OF SALT.**
>
> In fairness: it does work very well and I spent a lot of time refining it but just be careful and please don't hate me.

This project reproduces how the installed Sonos desktop controller discovers the
music-service accounts already configured in a household without a Sonos cloud
login.

```sh
/usr/bin/python3 sonos_accounts_gui.py
```

On macOS this command automatically restarts with Homebrew Python because
Apple's bundled Tk 8.5.9 renders blank windows on current dark-mode systems.
Install the supported GUI runtime once with:

```sh
brew install python-tk@3.14 pillow
```

Use **Run all** to discover players, fetch the service catalog, subscribe to the
household event, and decode its configured account instances. The GUI redacts
credentials until **Reveal credential values locally** is enabled.

The **Browse music** tab uses the same transport chooser as the desktop app:
manifest-backed services load their home page through the modern authenticated
content endpoint, while ordinary services use SMAPI. Modern home pages contain
several views, so the GUI presents each view as one artwork-backed section row;
opening that row displays the items already embedded in the response. Opening an
actual provider collection then switches to the desktop's SMAPI child request
using the provider's original object ID. The tab shows the transport used for
every page.

The **Add account** tab implements descriptor-driven onboarding independently of
the official controller. It uses modern `getAppLink`, falls back to legacy
`getDeviceLinkCode` for older DeviceLink services, and commits with the player's
advertised `AddOAuthAccountX` or `AddAccountX` action. Authorization is separate
from mutation: nothing is written until the GUI shows the exact household,
service, player, and operation and the user confirms it. Provider credentials
are never persisted by the GUI. Providers that only return a mobile-app deep
link are reported as app-only rather than being given an invented browser flow.

The command-line decoder works with either interpreter.

For a non-GUI report:

```sh
python3 decode_third_party_media_servers.py
```

The decoder's output intentionally includes the local account material in this
workspace; treat its JSON output as a secrets file.

The full protocol derivation and the Home Assistant integration direction are in
[REVERSE_ENGINEERING.md](docs/REVERSE_ENGINEERING.md). The concrete coordinator and
entity boundary is in [HA_INTEGRATION.md](docs/HA_INTEGRATION.md).

## Browse services without the desktop app

The command-line browser performs discovery, decrypts the household account
snapshot, selects the account-specific credentials, and talks directly to the
service's SMAPI endpoint:

```sh
# Account inventory and current root-browse health
python3 smapi_browser.py --list
python3 smapi_browser.py --probe-all

# Opt into the desktop-style process-local refresh and one retry when needed.
python3 smapi_browser.py --probe-all --refresh-credentials

# Build a checkpointed, credential-redacted hierarchy for every account.
python3 smapi_browser.py --host 192.168.1.51 --crawl-all \
  --max-depth 12 --max-nodes 5000 --max-collections 300 --max-seconds 30 \
  --tree-output analysis/music-service-tree.json

# Browse a root with the desktop transport chooser, or a returned SMAPI container ID
python3 smapi_browser.py --service-id 256 --id root
python3 smapi_browser.py --service-id 256 --id R1-mediaCollection

# Retrieve the richer record for a media item
python3 smapi_browser.py --service-id 256 \
  --id R1-Program-stream:toronto --media-metadata

# Navigate collections interactively. Add --serial when a provider has several accounts.
python3 smapi_browser.py --service-id 308 --serial 5 --interactive

# Discover provider search categories, then search with the returned mapped_id.
python3 smapi_browser.py --service-id 204 --serial 3 --search-categories
python3 smapi_browser.py --service-id 204 --serial 3 \
  --search-id artist --search "Nina Simone"
```

No Sonos cloud login and no running Sonos desktop process are required. The
browser follows the controller's capability-dependent SOAP/header behavior. It is
read-only by default. With `--refresh-credentials`, an expired provider token is
refreshed or accepted from the provider's fault response, installed only in the
current browser process, and retried. Decompilation of the active desktop call
path confirmed that it also updates its in-memory account model rather than
writing replacement credentials to a player. The option therefore does not
modify household state.

## Add a music-service account

Use the GUI's **Add account** tab, or preview a provider's authorization contract
from the command line:

```sh
# Read-only: prints a redacted link-session preview
python3 sonos_account_onboarding.py --service-id 236

# Opens the provider page, waits for you, then confirms and commits that same code
python3 sonos_account_onboarding.py --service-id 236 --open-browser --commit
```

Anonymous and legacy username/password descriptors use `AddAccountX`; linked
accounts use `AddOAuthAccountX`. The account type is the descriptor service ID
encoded with the current player schema revision. A successful player response
returns the new account UDN and optional provider nickname, after which the
player replicates the account through the household.

## Service status

```sh
python3 sonos_service_status.py
```

This reports Sonos's public status, degraded components, and unresolved
incidents from the official Statuspage API. The controller's buried
`DEBUG_FETCH_SERVICE_OUTAGES` action is only a compiled action identifier; its
private target is not proven. The standalone command provides the safe,
documented outage information rather than claiming that identifier is an API.

`--crawl-all` walks collections breadth-first so every root branch is sampled
before the crawler descends further. It paginates SMAPI results, preserves leaf
items, detects repeated IDs, applies explicit time/item/collection/depth limits,
and checkpoints after each account. Provider metadata and faults are recursively
credential-redacted before being written.

An account containing Sonos's literal `needs_reauth` marker cannot be refreshed;
it must first be reauthorized through Sonos. `--probe-all` reports that state
explicitly instead of treating it as a protocol failure.

## Inspect the complete household without the desktop app

`sonos_system_inspector.py` creates a credential-redacted, read-only system
snapshot. Starting from one player, it follows the live topology to every current
member, reads every device and service description, catalogs all advertised UPnP
actions/state variables, invokes only a hard-coded getter allow-list, and
inventories configured music accounts. These LAN features work from a fresh
clone. The versioned controller binaries, raw decompiler output, metrics
resource, complete non-personal configs, and synthetic controller-state fixtures
used for static enrichment are included under `research/controller/`.

```sh
python3 sonos_system_inspector.py

# Multicast-independent seed when necessary
python3 sonos_system_inspector.py --host 192.168.1.51

# Optional: override the included research bundle with another controller build
python3 sonos_system_inspector.py --host 192.168.1.51 \
  --controller-root '/path/to/windows/drive_c' \
  --decompiled-root '/path/to/decompiled/resources' \
  --interop-root '/path/to/decompiled/Sonos.SCLib.Interop'
```

The default local-controller section reads only the checked-in research bundle,
not files elsewhere on the developer's machine. `--skip-local` omits the section
entirely. Generate accurate controller state for every household currently
reachable on the LAN with:

```sh
python3 research/controller/generate_controller_state.py

# Seed households directly when multicast discovery is unavailable
python3 research/controller/generate_controller_state.py \
  --host 192.168.1.51 --host 192.168.50.20
```

Each household gets its own ignored `generated/controller-state/.../drive_c`
tree containing the real household/Muse IDs, alarm-list version, alarms, rooms,
program URIs, and metadata. One persistent generated controller UUID and the
host's MAC identity are shared across those household trees, matching the
official controller's identity lifecycle. The inspector automatically selects
the generated tree matching the household it is currently inspecting;
`--controller-root` remains available to override that selection.

The private-mode outputs are written to `analysis/system-inspection.json` and
`analysis/system-inspection.md`; both generated files are ignored by Git. No
generic SOAP execution or mutation option exists in the inspector.

Compare two snapshots after a firmware update, regrouping, account change, or
controller experiment:

```sh
python3 sonos_system_diff.py before.json after.json \
  --output analysis/system-diff.json \
  --markdown analysis/system-diff.md
```

All tools now share `sonos_discovery.py`, which matches the resilient part of the
official discovery strategy: every usable IPv4 interface, multicast plus limited
broadcast, three sends one second apart, response deduplication, and household IDs.

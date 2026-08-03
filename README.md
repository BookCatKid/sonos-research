# Sonos local music-service browser

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

The command-line decoder works with either interpreter.

For a non-GUI report:

```sh
python3 decode_third_party_media_servers.py
```

The decoder's output intentionally includes the local account material in this
workspace; treat its JSON output as a secrets file.

The full protocol derivation and the Home Assistant integration direction are in
[REVERSE_ENGINEERING.md](REVERSE_ENGINEERING.md). The concrete coordinator and
entity boundary is in [HA_INTEGRATION.md](HA_INTEGRATION.md).

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

`--crawl-all` walks collections breadth-first so every root branch is sampled
before the crawler descends further. It paginates SMAPI results, preserves leaf
items, detects repeated IDs, applies explicit time/item/collection/depth limits,
and checkpoints after each account. Provider metadata and faults are recursively
credential-redacted before being written.

An account containing Sonos's literal `needs_reauth` marker cannot be refreshed;
it must first be reauthorized through Sonos. `--probe-all` reports that state
explicitly instead of treating it as a protocol failure.

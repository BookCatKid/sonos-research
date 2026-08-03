# Sonos local music-service browser

This project reproduces how the installed Sonos desktop controller discovers the
music-service accounts already configured in a household without a Sonos cloud
login.

```sh
/usr/bin/python3 sonos_accounts_gui.py
```

Use **Run all** to discover players, fetch the service catalog, subscribe to the
household event, and decode its configured account instances. The GUI redacts
credentials until **Reveal credential values locally** is enabled.

The currently selected pyenv Python on this Mac lacks `_tkinter`; use the system
Python command above for the GUI. The command-line decoder works with either
interpreter.

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
# Use the desktop controller's persistent host identity for cloud-backed services
export SONOS_HOST_DEVICE_ID="<your Sonos MachineIdentifier>"

# Account inventory and current root-browse health
python3 smapi_browser.py --list
python3 smapi_browser.py --probe-all

# Opt into the desktop-style process-local refresh and one retry when needed.
python3 smapi_browser.py --probe-all --refresh-credentials

# Browse a root or a returned container ID
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

An account containing Sonos's literal `needs_reauth` marker cannot be refreshed;
it must first be reauthorized through Sonos. `--probe-all` reports that state
explicitly instead of treating it as a protocol failure.

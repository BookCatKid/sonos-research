# Sonos desktop controller: local music-account discovery

This is a live reverse-engineering note for the Sonos desktop controller installed at
`/Applications/Sonos.app`, version 17.2.3 (build 90.0.77070). It was derived from
the installed executable, local UPnP services, event traffic, and an independent
decoder run against this household. No web sources or user-document searches were
used.

## Finding

The desktop app does not need a Sonos-account login to learn the household's music
accounts. A player supplies a household snapshot over the LAN in the initial
`ZoneGroupTopology` event notification. That snapshot contains a per-service,
per-account credential tuple.

```text
SSDP M-SEARCH
  └─ response header X-RINCON-HOUSEHOLD = household ID

SUBSCRIBE /ZoneGroupTopology/Event
  └─ initial NOTIFY includes ThirdPartyMediaServersX
       └─ versioned, Base64-wrapped encrypted MediaServers XML
            └─ one Service node for every configured service account

POST /MusicServices/Control: ListAvailableServices
  └─ global service descriptor catalog (names, policies, endpoints)

desktop controller
  └─ joins Service node service type to descriptor catalog and materializes its
     account-specific media-service object
```

`ListAvailableServices` alone is only the catalog. It does **not** enumerate the
accounts configured in the household. `ThirdPartyMediaServersX` is the missing
piece.

## The encrypted event payload

The event property begins with `2:`. The rest is Base64. The confirmed version-2
decoder is:

1. `raw = Base64Decode(value after "2:")`
2. `iv = raw[0:16]`; the remainder is AES ciphertext.
3. `household_key = MD5(UTF8(household_id) || 1a01a731c96e9ebde8475182b274b70e)`
4. `blob_key = MD5(iv || household_key)`
5. Decrypt the ciphertext with AES-128-CBC and PKCS#7 padding using `blob_key`
   and `iv`.
6. Split the decrypted result into `payload` and its final four bytes. The final
   four bytes must equal `MD5(payload)[0:4]`.
7. Parse `payload` as UTF-8 XML. Its root is `MediaServers` and its children are
   `Service` nodes.

The fixed 16-byte salt is present in the desktop app at virtual address
`0x1010c2320`. The app's MD5 implementation and AES-CBC setup were identified from
the executed codec path rather than guessed from captured traffic.

The version-2 payload is intentionally opaque to a passive capture, which explains
why capturing packets alone feels evasive: the required household ID arrives over
SSDP, and the actual account snapshot is encrypted before it is put in the UPnP
event body.

## What the account XML contains

Each `MediaServers/Service` node has a UDN plus numbered account fields such as:

- `Username0`
- `Token0`
- `Key0`
- `Nickname0`
- `SerialNum0`
- `Tier0`, `Flags0`, and `NumAccounts`

The `Token`/`Key` values are actual account material, not merely a flag that an
account exists. The companion GUI keeps them redacted by default and can reveal
them locally; it must not be committed or exported accidentally with real values.

The UDN prefix encodes the catalog service type:

```text
SA_RINCON<encoded_type>_...
service_id      = encoded_type // 256
schema_revision = encoded_type % 256
```

In the captured household every record used revision `7`. `SerialNum0` distinguishes
the separately configured account instances. This is why several accounts from the
same provider appear independently in the desktop app.

## Live evidence from this household

The verified capture decoded to a 9,868-byte `MediaServers` XML document containing
14 account/service instances over 12 service types. There were two independent
Amazon Music records and two independent Pandora records. Each duplicate provider
record had a different serial index and its own username/token/key tuple.

This proves that the multiple-account behavior is not reconstructed from a Sonos
cloud login or inferred from UI cache. It comes directly from local player state.

## App-code evidence

The following installed-binary functions establish the complete path:

| Function | Observed role |
| --- | --- |
| `FUN_100ce5f10`, `FUN_100cbf6b0`, `FUN_100ce9370` | Initializes/rekeys the process-global blob secret from the discovered household ID. |
| `FUN_100c822c0` | Receives `ThirdPartyMediaServersX` from a Zone Group Topology event and invokes the versioned decoder. |
| `FUN_100eb84a0` | Validates the `2:` prefix and Base64-decodes it. |
| `FUN_100eb7a90`, `FUN_100eb7cc0`, `FUN_100eb7df0` | Derives the keys, decrypts the stream, removes padding, and checks the four-byte MD5 integrity value. |
| `FUN_100ccd260` | Iterates the decoded media-server records, reads UDN/class/data, and creates the desktop controller's per-account service objects. |
| `FUN_100cd8350` | Responds to `ServiceListVersion` and fetches the separate `ListAvailableServices` catalog. |

The historical first-join app log corroborates the order: SSDP found one household,
the app joined it, no Sonos cloud user account was available, then the service list
and local media-server state were processed.

## Reproducing locally

The repository has two local tools:

- `python3 decode_third_party_media_servers.py` performs the one-shot SSDP,
  subscribe, decrypt, catalog join, and JSON report. Its current output includes
  the credential values because this workspace is explicitly for local integration
  work.
- `/usr/bin/python3 sonos_accounts_gui.py` launches the existing desktop explorer.
  (The currently selected pyenv Python lacks `_tkinter`.) Use
  **Run all**, then opt into **Reveal credential values locally** when needed.
  The GUI redacts credentials in its JSON/export by default.

Both tools are read-only toward the Sonos system: they use SSDP discovery,
`ListAvailableServices`, and an ephemeral UPnP event subscription. They do not
invoke any account mutation or playback controls.

## Home Assistant integration direction

For an HA-side account inventory or diagnostic, subscribe to
`/ZoneGroupTopology/Event`, decode `ThirdPartyMediaServersX`, then join the UDN
service type to `ListAvailableServices`. Store service name, service type, serial
index, nickname, and credential-field presence as entity metadata.

For player control and normal playback, prefer sending an account-specific URI/UDN
back to the Sonos speaker so the speaker uses its household-held account instead of
persisting service credentials in HA. Only use the decrypted `Token`/`Key` values
where direct service API work is genuinely necessary; if persisted, treat the
export as a secrets file and encrypt it at rest.

The important implementation detail is to refresh after a Zone Group Topology
change: account additions/removals update the same household event property, and
the catalog can change independently via `ServiceListVersion`.

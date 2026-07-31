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

## Direct SMAPI browsing

The account tuple is sufficient to reproduce the desktop app's service browser.
The implementation is in `smapi_browser.py`; it has been verified while the Sonos
desktop process was not running.

The controller first obtains the value of `R_TrialZPSerial` with the local
`SystemProperties/GetString` action. That becomes the SMAPI `deviceId`. It then
sends HTTPS SOAP 1.1 requests to the selected catalog descriptor's `SecureUri`:

```text
SOAP Header
  credentials
    zonePlayerId    = selected player UDN when capability bit 18 is set
    deviceId       = R_TrialZPSerial
    deviceProvider = Sonos
    loginToken     = token/key/householdId when required by policy/capabilities
    sessionId      = cached getSessionId result for DeviceLink services
  context
    timeZone       = local zone when capability bit 16 is set

HTTP Headers
  SOAPAction: "http://www.sonos.com/Services/1.1#getMetadata"
  Accept-Language: en-US
  X-Sonos-Controller-ID: controller UUID
  Authorization: Bearer <token> when capability bit 3 is set

SOAP Body
  getMetadata(id, index, count [, recursive])
  getMediaMetadata(id)
  search(id, term, index, count)
```

Capability bit 3 is not just an additional header. When it is set, the official
client omits `token` and `key` from the SOAP `loginToken` and moves the token to
the HTTP Bearer header; `householdId` remains in the SOAP credentials. Capability
bit 16 controls whether the context header is attached, and bit 18 controls the
`zonePlayerId` field. These branches explain why one fixed credential envelope
does not work across providers.

For a DeviceLink account, the desktop app first calls `getSessionId` with base
device credentials and the stored username/password body fields. Capability-bit-3
services also receive the stored token as HTTP Bearer auth on that call. The
returned session is cached by service ID and username and then sent as the SOAP
`credentials.sessionId` on browse/search operations. An invalid-session response
evicts it and repeats `getSessionId` once.

`getMetadata` returns paged `mediaCollection` and `mediaMetadata` children.
Collections are navigated by passing their returned `id` into another
`getMetadata` call. A media item's ID can be passed to `getMediaMetadata` for its
nested program/track/stream details. Providers are allowed to report that
`getMediaMetadata` is unsupported; the item record from the collection remains
valid in that case.

Search category IDs are provider-defined. The desktop app follows the service
descriptor's JSON manifest to its XML presentation map, selects the
`PresentationMap type="Search"` groups, and maps each UI category `id` to its
`mappedId`. The mapped ID is sent as the SOAP `search.id`; the request also sends
the term, index, and count. Search totals and pages are capped at 1,000 by the
desktop implementation. `--search-categories` and `--search` reproduce this path.

### Expiration and transient replacement

The active desktop path has two capability-dependent branches. With capability
bit 3 clear, a `TokenRefreshRequired` SOAP fault can carry a nested
`refreshAuthTokenResult`; the controller takes its `authToken` and `privateKey`
directly from that failed browse. With bit 3 set, the controller calls
`refreshAuthToken`, placing the old token/key back in the SOAP `loginToken` even
though normal requests route the token as Bearer authorization.

The decisive virtual-call trace is `FUN_100e18630` through the household-adapter
slot at `+0x30` to `FUN_100cbe6c0`. That implementation calls
`FUN_100e1d9d0` to replace the pair in the desktop process's account object and
returns success. It does **not** invoke the separately present
`SystemProperties/RefreshAccountCredentialsX` SOAP wrapper. Direct attempts to
use that player action returned UPnP 402 and were based on the wrong call target.

The independent browser now mirrors this behavior: `--refresh-credentials`
updates only its in-memory account snapshot, retries once, and never changes the
speaker's stored account record. Without the option it reports expiration
without accepting or requesting a replacement.

Some account records contain the literal token `needs_reauth`. That is an account
state, not an alternate authentication mechanism, and the provider cannot refresh
it. Reauthorization is required before either the official app or this browser
can access that account.

### Current live verification

With the desktop app closed, the independent implementation has verified:

- both configured Amazon Music accounts after embedded transient refresh;
- SiriusXM and Audible after embedded transient refresh;
- anonymous root browsing against NRK Radio, Sveriges Radio, myTuner Radio, CBC
  Radio & Music;
- live AppLink browsing against Radio Paradise and JazzGroove.org;
- `getMediaMetadata` returning current nested CBC program data;
- exact per-account selection when a service has multiple accounts;
- the bit-3 `refreshAuthToken` construction used by Sonos Radio.

The current Pandora records return `AuthTokenExpired` without replacement data,
and the Apple record returns `InvalidToken`; the desktop state machine cannot
derive a replacement from those responses. Sonos Radio refreshes successfully
but its root `getMetadataResponse` is currently `xsi:nil` (and omits the `xsi`
namespace declaration), which the browser tolerates as an empty result.

Run `python3 smapi_browser.py --probe-all` for a fresh per-account result. It does
not print token/key values.

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

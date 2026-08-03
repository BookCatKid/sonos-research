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

## Exact desktop startup and discovery state machine

The installed controller does not begin with a cloud login. On launch it monitors
the active network adapter, resumes its LAN core when an IPv4 address becomes
available, starts an active SSDP scanner, and initially places the zone-group
manager in `SystemNotFound`. A successful discovery is then supposed to associate
one discovered player with a household, subscribe to that player's
`ZoneGroupTopology` service, and build the complete zone/group model from the
topology event. Passive SSDP remains active afterward to notice player boot,
address, and availability changes.

The scanner implementation at `0x100ec0420` constructs this request:

```text
M-SEARCH * HTTP/1.1
HOST: 239.255.255.250:1900
MAN: "ssdp:discover"
MX: 1
ST: urn:schemas-upnp-org:device:ZonePlayer:1
USER-AGENT: <controller UPnP user agent>
X-RINCON-HOUSEHOLD: <household ID>   # present when restricting a rescan
```

It calls `getifaddrs`, selects every IPv4 interface that is up, multicast-capable,
and not loopback, sets `IP_MULTICAST_IF` to that interface, and sends the request
to `239.255.255.250:1900`. On broadcast-capable interfaces it also enables
`SO_BROADCAST` and sends the same request to `255.255.255.255:1900`. The active
scanner initializes a retry counter to three and schedules another send one second
later until that counter is exhausted. Discovered records are retained according
to their SSDP lifetime and aged out later; the controller does not equate one lost
UDP response with a missing system.

The join-existing wizard is a UI state machine around that scanner:

```text
legacy_join_existing.init
  -> intro
  -> firewall/elevation check
  -> connecting + active SSDP scan
  -> success, or timeout after 30 seconds
```

The 30 seconds is a wizard deadline, not a required speaker handshake. Once a
player is found, normal connection proceeds through its advertised device
description and `ZoneGroupTopology`; setup-specific product joining can additionally
use UDP 6969 and button-press flows, but reconnecting to an already configured
household does not require those operations.

### Why the installed controller currently waits and sometimes fails

A non-promiscuous launch capture and the controller logs isolate the current
failure. The CrossOver Windows controller entered `Starting active SSDP scan` at
19:11:26 and timed out exactly 30 seconds later. It first attempted the Windows
firewall elevation COM path, which failed with `0x800401EA` and was reported as
`Enable Firewall UAC prompt rejected by user`. During its claimed active-scan
window, **zero Sonos M-SEARCH packets left the Mac interface**. A speaker's valid
multicast and broadcast advertisements were visible on that interface during the
same window, but the controller did not associate them.

The native macOS controller reproduced the important half of the failure: it bound
UDP ports 1900 and 6969, logged `Starting active SSDP scan`, and emitted no Sonos
M-SEARCH packet. macOS's application firewall was disabled and its per-app query
reported that incoming Sonos connections were permitted. By contrast, the local
Python implementation sent the legacy request and succeeded 10 out of 10 times,
finding a speaker in 102--317 ms. The speakers and LAN were therefore healthy.

This means the observed long delay is not evidence that Sonos performs a secret,
slow setup exchange that open-source controllers omit. It is the fixed timeout of
the official scanner after its sends fail or are suppressed. The relevant gap in
our code is nevertheless real: our current helper sends only one multicast request
through the default interface and waits three seconds. It lacks the official
per-interface multicast+broadcast fan-out, three-send retry schedule, passive
advertisement listener, response aging, network-change restart, household-aware
filter, and topology-driven system association.

Open-source controllers are not all less capable here. Current SoCo discovery also
enumerates usable IPv4 interfaces and sends three `ZonePlayer:1` multicast requests
per interface, then asks the first player for the topology instead of waiting for
every player response. It additionally offers an optional parallel TCP scan of
port 1400 when multicast discovery fails. The official desktop core's distinctive
pieces are limited-broadcast transmission, persistent passive SSDP/product aging,
network-change state management, its household-aware join wizard, and the separate
UDP-6969 product-setup path. SoCo's optional unicast network scan can actually be
more resilient than the official controller when multicast is broken.

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

The account tuple is sufficient to reproduce the desktop app's legacy SMAPI
service browser for most providers.
The implementation is in `smapi_browser.py`; it has been verified against the
live household and cross-checked against the installed CrossOver desktop core's
diagnostic log.

The controller keeps two identities separate. The reachable player's
`R_TrialZPSerial` is the legacy SMAPI SOAP `credentials.deviceId`, and its UDN is
used for capability-gated zone routing. The desktop controller's persistent
`MachineIdentifier` is the modern content-session `X-Sonos-Device-Id` value. In
the active CrossOver installation that value is
stored in `uidata.xml` as `47a51435-1e10-4324-9d6b-7e08cb155672`. It then sends
HTTPS SOAP 1.1 requests to the selected catalog descriptor's `SecureUri`:

```text
SOAP Header
  credentials
    zonePlayerId    = selected player UDN when capability bit 18 is set
    deviceId       = reachable player's R_TrialZPSerial
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
  X-Sonos-Device-Id: persistent controller MachineIdentifier

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

### Modern provider content sessions

The manifest `browse` endpoint uses a separate desktop path. Decompiled
`SCContentSessionBrowse::FUN_100247e60` proves that a provider request such as
Apple's `https://sonos-music.apple.com/browse/v1` receives:

```text
Authorization: Bearer <current in-process account token>
X-Sonos-Device-Id: <household ID>_<account UID in eight lowercase hex digits>
Accept-Language: <account language>
X-Sonos-Context-TimeZone: <zone>                 when capability bit 16 is set
X-Sonos-Context-ContentFiltering: explicit       when enabled and supported
X-Sonos-GroupCapability: <group capability>      when available
```

It does **not** use `X-Sonos-SMAPI-Auth`; that aggregate account envelope belongs
to the controller's cross-service content search path. On HTTP 401,
`SCContentSessionBrowse::FUN_100247cb0` invokes `FUN_100e24af0`, which constructs
the ordinary `refreshAuthToken` operation, updates the controller's in-memory
account token, and retries the provider URL once. The exact refresh constructor
passes a null HTTP-Bearer argument while putting the old token/key in the SOAP
`loginToken`. `content_browse()` and `SmapiClient.refresh_auth_token()` now mirror
that split.

The JSON home page is a collection of named `views`, and each view already
contains its first set of `items`. The desktop represents those views as
drill-down sections. Entering a section is therefore a local browse-stack
operation, not another network transport. The independent GUI now preserves
that hierarchy instead of flattening all embedded items into the root. It also
renders each item's provider `imageUrl` as album art.

The home-page request is only half of the desktop chooser. An official desktop
log records root object `0` going through `SCContentSession`, followed by a
selection of an actual provider collection from the JSON data going through
ordinary SOAP `getMetadata`. Values such as Apple
`00081024recommendation%3a...` and Amazon
`10fe2064catalog%2fplaylists...` occur at the controller-local browse/SCUri
boundary. They are routing identifiers, not provider object IDs. The native
browse delegate removes that layer and passes the JSON `objectId` unchanged to
SMAPI. Sending the local prefix to a provider was the cause of the SiriusXM
"discriminator not established" failure. `DesktopBrowseSession` now preserves
the raw provider ID. Query-string experiments against Apple's `/browse/v1`
endpoint were ignored and returned the root again, while appending the child as
a path returned HTTP 404.

The Play:1 firmware independently establishes the playback boundary. Ordinary
`getMetadata`, `search`, and `getMediaMetadata` calls use the Bearer account token
without a device signature. `getMediaURI` and `getContentKey` additionally derive
`X-Sonos-MS-Sig`, attach `X-Sonos-DeviceCert`, and maintain the returned
`deviceSessionToken`/`deviceSessionKey`. Those device-auth headers are therefore
required for Apple playback dereference, but copying them onto controller browse
requests would not reproduce the official browse code.

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

Current Sonos Radio exposes one more compatibility case: its capabilities select
the embedded-replacement branch, but some child requests return only a plain
`Token Expired` fault. Its explicit `refreshAuthToken` operation succeeds. The
desktop-compatible retry now uses an embedded replacement when present and falls
back to that explicit operation when it is absent. This makes the Music, News &
Talk, Sports, and Locations children browse successfully.

Some account records contain the literal token `needs_reauth`. That is an account
state, not an alternate authentication mechanism, and the provider cannot refresh
it. Reauthorization is required before either the official app or this browser
can access that account.

### Current live verification

The independent implementation has verified:

- both configured Amazon Music accounts after embedded transient refresh;
- SiriusXM and Audible after embedded transient refresh;
- anonymous root browsing against NRK Radio, Sveriges Radio, myTuner Radio, CBC
  Radio & Music;
- live AppLink browsing against Radio Paradise and JazzGroove.org;
- `getMediaMetadata` returning current nested CBC program data;
- exact per-account selection when a service has multiple accounts;
- the bit-3 `refreshAuthToken` construction used by Sonos Radio.

The current desktop-chooser matrix is 12 successful account roots out of 14. The
two Pandora records return `Client.AuthTokenExpired` / “Failed to reauth device
id” without replacement data. Apple's legacy SOAP `getMetadata` route returns
`InvalidTokenException`, but it is no longer used for the account root. Apple's
manifest-driven content route is different: a
live `GET https://sonos-music.apple.com/browse/v1` using the `Token0` exported in
`ThirdPartyMediaServersX` as an HTTP Bearer credential returned HTTP 200 and the
authenticated account's Listen Now root, Library, recommendations, and radio
sections. This proves the household credential is valid for modern Apple browse;
it does not make that token valid for Apple's legacy SOAP browse operation.

The Apple network child contract uses the raw provider object ID with SOAP
`getMetadata`. A live request built that way still receives Apple's
`AuthTokenExpired / InvalidTokenException`, as does `refreshAuthToken`, while the
same stored token continues to return the authenticated REST home page. Apple's
`getAppLink` response to the Windows desktop reports
`DesktopNotSupportedMessage`, so renewal must be completed through a supported
Sonos client. This is provider/account state after an exact native request, not a
remaining transport-selection difference. Sonos Radio and SiriusXM roots also
use their advertised content endpoints successfully. Apple and SiriusXM now also
open their embedded first-level sections locally. A live SiriusXM request for
the raw `favorites:library-filter-view-all` object succeeds and returns the
account's saved channel, confirming the deeper handoff.

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

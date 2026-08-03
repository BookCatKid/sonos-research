# Home Assistant integration contract

The browser core should be used as an account-aware coordinator library, not as
one entity per credential. Credentials remain an implementation detail obtained
from the household event and are never exposed as entity state or attributes.

## Stable identity

Use this tuple as the account key:

```text
(household_id, service_id, SerialNum0)
```

`service_id` alone is insufficient because one household may configure several
accounts for the same provider. `Nickname0` is display metadata and can change;
it is not an identifier. The encoded account type used by Sonos mutations is
`(service_id << 8) | schema_revision`, currently revision 7.

## Coordinator state

One household coordinator owns:

- the selected reachable player and its `R_TrialZPSerial` zone identity;
- a persistent controller `MachineIdentifier` used as the cloud `deviceId` and
  `X-Sonos-Device-Id` (configure it as `SONOS_HOST_DEVICE_ID`);
- the `ListAvailableServices` descriptor catalog;
- the latest decrypted `ThirdPartyMediaServersX` account snapshot;
- one stable controller UUID for HTTP requests;
- a per-account async lock for transient token replacement and retry;
- short-lived page and presentation-map caches.

Subscribe to `/ZoneGroupTopology/Event`. When `ThirdPartyMediaServersX` changes,
replace the account snapshot atomically and invalidate pages for added, changed,
or removed account keys. When `ServiceListVersion` changes, refresh descriptors,
manifests, search categories, and all page caches.

## Read operations

The library-facing API should remain provider-neutral:

```text
list_accounts() -> AccountSummary[]
probe(account_key) -> AccountHealth
browse(account_key, object_id="root", index=0, count=100) -> BrowsePage
media_metadata(account_key, object_id) -> object
search_categories(account_key) -> SearchCategoryGroup[]
search(account_key, mapped_id, term, index=0, count=100) -> BrowsePage
```

`BrowsePage.items` preserves the provider's nested SMAPI fields and adds only a
`kind` discriminator (`mediaCollection` or `mediaMetadata`). Consumers navigate a
collection by sending its returned ID back to `browse`; IDs are opaque strings.

## Authentication lifecycle

Build auth independently for each operation from the current account snapshot
and descriptor capabilities. Never reuse an auth header across account keys.

On an expiration result, acquire the account lock and re-read the snapshot. If a
different request already installed a new token, retry with that pair. Otherwise
follow the service capability:

1. with capability bit 3 clear, extract `authToken` and `privateKey` from the
   fault's nested `refreshAuthTokenResult`;
2. with bit 3 set, call `refreshAuthToken` with the old token/key present in its
   SOAP `loginToken`;
3. replace the pair in the coordinator's in-memory account snapshot;
4. retry the original read operation once.

Do not write this transient pair back to the player. The installed desktop app's
active household adapter also updates only its process-local account object. Do
not retry a literal `needs_reauth` marker; expose it as `reauth_required`.

Credential maintenance should be an explicit coordinator option. With it off,
expiration is reported without calling `refreshAuthToken`; browsing remains
strictly read-only. With it on, replacement and the one retry must remain atomic
within that account's coordinator lock; household state is still unchanged.

## HA-visible status

A diagnostic entity may expose non-secret fields:

```text
service name, service_id, account serial, nickname, auth policy,
available, last successful browse, and one normalized reason
```

Normalize failures to:

- `reauth_required`: household contains `needs_reauth` or provider rejects refresh;
- `temporarily_unavailable`: endpoint/network failure;
- `unsupported`: provider does not implement the requested optional operation;
- `protocol_error`: malformed or unexpected service response.

Never place usernames, tokens, keys, raw account XML, request headers, or SMAPI
responses that may echo credentials in the HA state machine or normal logs.

## Concurrency and caching

The desktop request limit for legacy search is 1,000 results; cap `index + count`
accordingly. Honor provider `Policy PollInterval` for background probes. Interactive
browse calls can bypass the poll timer but should use a short page cache and
coalesce identical in-flight requests.

Refreshes must be serialized per account, not globally. Browses for two configured
accounts of the same service may run concurrently because their token/key pairs
and results are independent.

## Current implementation mapping

`smapi_browser.py` already implements the synchronous forms of discovery,
inventory, `probe`, `browse`, `media_metadata`, search-category discovery, search,
DeviceLink session acquisition, and process-local refresh. Refresh is enabled by
`--refresh-credentials` and does not mutate the player. An HA custom component should
wrap those blocking LAN/HTTPS calls with its executor or port the coordinator
methods to async I/O; the protocol and identity model should remain unchanged.

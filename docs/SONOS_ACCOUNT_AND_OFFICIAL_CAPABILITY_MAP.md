# Sonos accounts and difficult official-controller capabilities

## Evidence boundary

This map separates three things that are often conflated:

1. **LAN household authority** — speaker-advertised UPnP operations. No Sonos user login is required while the controller is on the household LAN.
2. **Music-service identity** — Apple Music, Pandora, SiriusXM, etc. credentials stored by the players for one household.
3. **First-party Sonos identity** — native token purposes compiled into SCLib and issued only to authorized Sonos clients/workflows.

Static presence proves that the native library understands a purpose or action. It does not prove that a third-party client is entitled to obtain its token or execute it.

## What “Fetch service outages” actually establishes

Three related native constants exist:

- `SC_ACTIONID_DEBUG_FETCH_SERVICE_OUTAGES`
- `SCI_EXPERIMENT_SERVICE_OUTAGE_NOTIFICATION`
- `SCI_EXPERIMENTALFEATURE_SERVICE_OUTAGE_NOTIFICATION`

There are also debug actions for pushing and deleting “system status.” Together these strongly suggest a controller action that refreshes an outage/status model and a feature gate that decides whether the resulting notification is shown. That relationship is an inference. The interop assembly exposes only the action identifier; it contains no typed `fetchServiceOutages()` method, URL, response model, or desktop invocation. Therefore the earlier short description “asks Sonos cloud for current incidents” was too definite.

The safe standalone equivalent is `../sonos_service_status.py`, which uses Sonos's documented public Statuspage endpoints for overall status, components, and unresolved incidents. It does not claim to reproduce an unproven private notification feed.

## Music-service account onboarding

The live service catalog currently contains 106 descriptors: 59 AppLink, 15 DeviceLink, and 32 Anonymous. The current household account records all use descriptor schema revision 7, producing account types such as Apple Music `204 * 256 + 7 = 52231`.

The reconstructed state machine is:

1. Read `MusicServices.ListAvailableServices` and select the descriptor's `Policy/Auth` value.
2. For AppLink, call the provider's unauthenticated `getAppLink` with household, platform, app name, and callback.
3. For DeviceLink, try `getAppLink` and fall back to `getDeviceLinkCode` when the legacy provider rejects it.
4. Open the provider's returned `regUrl`; retain its short-lived `linkCode` and hidden `linkDeviceId` only in memory.
5. For desktop browser authorization, poll provider `getDeviceAuthToken`, transform the returned user hash/tier as the native controller does, then call player `SystemProperties.AddOAuthAccountX` with the completed token/key form.
6. Anonymous or legacy credential services use `AddAccountX` instead.
7. On success, optionally call `SetAccountNicknameX`, then reload `ThirdPartyMediaServersX` to verify replication.

Steps 1–4 and the anonymous form of step 6 are live-proven. The Spotify browser
flow reaches a successful `getDeviceAuthToken`, but every tested S2 player rejects
step 5 with UPnP 402. OAuth addition is therefore an incomplete research surface,
not a working standalone feature. See `MUSIC_SERVICE_ACCOUNT_ONBOARDING_STATUS.md`.

Live non-mutating probes established concrete provider variation:

| Provider | Descriptor | Result |
|---|---|---|
| Pandora | AppLink | Browser activation URL, link code, hidden device ID |
| SiriusXM | AppLink | Browser device-link URL, link code, hidden device ID |
| Deezer | DeviceLink | Modern browser OAuth returned by `getAppLink` |
| Amazon Music | DeviceLink | Rejects `getAppLink`; legacy `getDeviceLinkCode` succeeds |
| Apple Music | AppLink | For this desktop identity, returns only encrypted/app-link markers and no standalone browser device link |

Apple's result is a provider onboarding restriction. It does not affect browsing an already-linked Apple account and it is not evidence that the stored Apple credential is expired.

The desktop's own `ServiceAppInterop` confirms this boundary rather than merely suggesting it: `getAppInstallState()` always returns `UNKNOWN`, and `openApp()` accepts only `http://` or `https://` URLs and returns false for provider deep links. Its SCLib initialization declares desktop form factor, host hardware `Windows`, and interop scheme `sonos://`. The OSS request now uses those same desktop identity inputs. Therefore an Apple response with no browser/device link cannot be made functional by pretending the Windows desktop launches the Apple Music application—the official desktop does not do that either.

## What the official first-party login can request

SCLib's `SCITokenManager.SCTokenPurpose` enum contains thirteen purposes:

| Purpose | Capability implied by native call sites/interfaces | Public OSS status |
|---|---|---|
| `DEFAULT_USER_PURPOSE` | Current user profile, customer ID, email, role and release program | Private first-party workflow |
| `PARENTAL_CONTROLS_PURPOSE` | Household content-control settings | LAN reads may exist; cloud grant is private |
| `REGISTER_PLAYER_PURPOSE` | Associate a new player with the customer | Private first-party workflow |
| `TRANSFER_PURPOSE` | Ownership transfer | Private and high-risk |
| `HH_CONFIG_PURPOSE` | Household configuration | Private first-party workflow |
| `HH_CONFIG_ADMIN_PURPOSE` | Administrative household configuration | Private; role-sensitive |
| `HISTORY_PURPOSE` | Cloud listening/recently-played history | Private first-party workflow |
| `CONNECTED_PARTNERS_PURPOSE` | Partner integrations and direct-control connections | Public partner APIs cover only approved integrations |
| `CHANGE_EMAIL_PURPOSE` | Sonos account email mutation | Private account-management workflow |
| `RECYCLE_DEVICES_PURPOSE` | Device recycling/reset lifecycle | Private and destructive |
| `DEVICE_REMOVAL_PURPOSE` | Remove registered devices | Private and destructive |
| `LIFECYCLE_PURPOSE` | Product/household lifecycle operations | Private and potentially destructive |
| `ACCEPT_SR_TOS_PURPOSE` | Accept Sonos Radio terms | Private commerce/legal state |

`SCIUserAccount` additionally exposes owner/admin roles, email verification status, release-program channel, profile refresh, and sign-out. These explain why first-party login changes more than remote playback: it supplies identity and authorization for operations whose owner/admin policy cannot be established from LAN reachability alone.

No private scope, client secret, ownership endpoint, or token is embedded in the OSS implementation. The native purpose list is an architectural map, not permission to mint those credentials.

## Hard official capabilities and reproduction status

| Capability | Why it is difficult | OSS state / correct next layer |
|---|---|---|
| Music-service browse and search | Provider-specific SMAPI/content transports, account identity, refresh and presentation metadata | Implemented and recursively tested across configured accounts |
| Account add/link | Provider-selected auth modes plus a player-side credential commit | Anonymous add/remove proven; OAuth discovery/exchange proven; OAuth player commit blocked by UPnP 402 |
| Service outage display | Private action/feature gate and unknown presentation feed | Public official status API implemented; private feed not falsely claimed |
| Event-sourced household state | Subscription renewal, topology churn, coordinator changes, `LastChange`, network transitions | Read-only snapshot exists; durable coordinator remains highest-priority controller work |
| Dynamic provider UI/action engine | Provider-defined sections, forms, actions, multiple artwork layouts, progress/resume state | Browse sections/artwork implemented; arbitrary forms/actions remain |
| Add/join/recover household | Long-running wizard, discovery modes, cloud ownership and rollback | Cataloged only; requires transaction journal and event coordinator first |
| Stereo/Sub/surround bonding | Multi-player transactional topology with partial-failure recovery | Operations cataloged; intentionally not exposed as one-shot mutations |
| Firmware orchestration | Compatibility generations, whole-house progress, resume and downgrade rules | Static and speaker surfaces cataloged; installation deliberately excluded |
| Trueplay/Sonar calibration | Device motion/audio capture, signed calibration packages and hardware-specific DSP | Native interfaces cataloged; no safe generic standalone reproduction yet |
| Diagnostics | Player/controller aggregation, sensitive host/network collection and cloud upload | Manifest understood; collection/upload needs consent and redaction preview |
| Internal wizard engine | Native action descriptors, state components, validation and cancellation | Static interfaces proven; runtime catalog needs a compatible isolated SCLib harness |
| Native content logging | Unknown sink plus reusable-token exposure | Identifiers proven; safe redacting harness still required before enabling headers |

## Source evidence

- Player SCPD and native decompilation prove the exact `AddAccountX` and `AddOAuthAccountX` fields.
- `SCIServiceDescriptor.cs` proves the descriptor-level auth selection and both link-code and completed-token constructors.
- `SCIAccountManager.cs`, `SCIUserAccount.cs`, and `SCITokenManager.cs` prove the first-party account model and token-purpose list.
- Sonos documents provider linking in [Add authentication](https://docs.sonos.com/docs/add-authentication), [getAppLink](https://docs.sonos.com/docs/getapplink), and [getDeviceAuthToken](https://docs.sonos.com/docs/getdeviceauthtoken).
- Sonos publishes the [Status API](https://status.sonos.com/api).

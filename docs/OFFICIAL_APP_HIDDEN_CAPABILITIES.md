# Sonos controller: account onboarding and hidden capabilities

This report is based on static analysis of the installed Windows desktop controller and its bundled SCLib, the existing native Ghidra output in this repository, and current Sonos developer documentation. The broken running application was not used, changed, or debugged.

## Scope and confidence

There are three different evidence levels below:

- **Desktop-wired**: the desktop C# UI calls the function directly.
- **Native-confirmed**: the installed native controller implements the player operation or subsystem.
- **Bundled interface**: the method is exposed by the shared SCLib shipped with desktop, but this alone does not prove that the desktop UI can reach it. Some of SCLib is shared with mobile controllers.

## 1. Adding a music service

### The end-to-end path

1. The desktop's Add Music Service command invokes `SCIHousehold.createMusicServiceAddAccountWizardAction()` (or the separate Sonos Labs variant) and runs the returned action. This is desktop-wired at `/tmp/sonos-desktop-decompiled/Sonos.Controller.Desktop.Main/CommandSwitchboard.cs:1120`.
2. SCLib asks the household's service-descriptor manager for available services. A descriptor supplies its title, logos, whether accounts can be added/removed, whether it is preloaded/preinstalled, and its authentication mode. The relevant interface is `/tmp/sonos-interop-decompiled.MZAhPX/Sonos.SCLib.Interop/SCIServiceDescriptor.cs:42`.
3. The descriptor selects one of four authentication modes from `SCAuthenticationType`: anonymous, username/password, device-link, or app-link. This is not a controller guess or a hard-coded Apple/Pandora switch; it is service metadata.
4. For username/password, SCLib can validate first and then create an add-account operation. For OAuth-style services it either:
   - accepts a completed `authToken`, private/refresh `authKey`, OAuth device ID, user hash, and account tier; or
   - accepts a `linkCode`, redirect URI, and OAuth device ID, allowing the player and provider to finish the token exchange.
5. The native controller sends the chosen player operation to `SystemProperties:1`:
   - `AddAccountX(AccountType, AccountID, AccountPassword)`; or
   - `AddOAuthAccountX(AccountType, AccountToken, AccountKey, OAuthDeviceID, AuthorizationCode, RedirectURI, UserIdHashCode, AccountTier)`.
6. The player returns an account UDN and, for OAuth, an account nickname. The player stores the account and distributes it to the household. Sonos' current account-matching documentation explicitly confirms that the player stores the account and replicates changes throughout the household.
7. Subsequent service browse calls use that account's login token. Playback still has another player-side phase: the player obtains media metadata/URI/content keys as needed and plays the returned stream.

Native evidence is in `../research/ghidra-class-xrefs.txt`: `FUN_100e60740` builds `AddAccountX`; `FUN_100e609a0` builds `AddOAuthAccountX` and shows every input and output field. Current Sonos documentation describes the provider side of the same flow in [Add authentication](https://docs.sonos.com/docs/add-authentication), [Add browser authentication](https://docs.sonos.com/docs/add-browser-authentication), and [getDeviceAuthToken](https://docs.sonos.com/docs/getdeviceauthtoken).

### How app-link versus browser-link is chosen

For OAuth/AppLink integrations, Sonos first calls the provider's `getAppLink`. The provider response advertises the available path(s). If an app URL is returned and the compatible provider app is available, the controller can offer app authentication; otherwise a browser/device-link URL is used. Both converge on `getDeviceAuthToken`, which returns a household-specific auth token and optional private refresh key. The controller then commits that result to the player through `AddOAuthAccountX`.

This corrects an important overgeneralization: Apple Music does **not** necessarily open the Apple Music app. App-link is only one possible onboarding presentation, and it is not the normal browse/refresh mechanism. Once the account exists, browsing uses the stored household account.

### Account management after addition

The installed controller supports multiple accounts per service, a default account, nicknames, password changes, removal, replacement, and reauthorization. OAuth replacement can again use either a completed token/key or a link code. The exact interfaces are:

- `SCIServiceAccountManager`: enumerate, look up, count, get/set/validate the default account.
- `SCIServiceAccount`: remove, replace username/password, replace OAuth credentials, replace a preinstalled account, update a password, and set a nickname.
- Native player calls include `RefreshAccountCredentialsX`, `EditAccountPasswordX`, `EditAccountMd`, `SetAccountNicknameX`, and `RemoveAccount`.

## 2. What “account login” means

There are two independent account systems.

### Music-service account

This authorizes a particular service account for a Sonos household. It enables whatever that service advertises, commonly:

- personalized browse, search, library, recommendations, and explicit-content/tier handling;
- provider favorites, ratings, playlist/library creation and deletion, and other provider-defined actions;
- account-correct media metadata, stream URI/content-key resolution, and playback;
- playback/account reporting and scrobbling where supported;
- multiple accounts and selection of a household default.

Anonymous services can browse and play public content but cannot offer account-bound personalization such as provider favorites or playlists. Sonos documents those limits in [Add authentication](https://docs.sonos.com/docs/add-authentication). The stored provider token represents a user-household association, not simply “the Apple account signed into this computer.”

### Sonos user account

This is separate from music services. `SCIAccountManager` stores Sonos customer ID, access token, refresh token, scope, email, expiry, and token purpose. The bundled token manager defines distinct purposes for:

- default user access;
- parental controls;
- registering a player;
- ownership transfer;
- household configuration and household-admin configuration;
- listening history;
- connected partners;
- changing email;
- recycling/removing devices;
- lifecycle operations;
- accepting Sonos Radio terms.

Consequently, signing into Sonos can unlock cloud-owned household administration, remote/persistent device state, connected partners, registration/ownership flows, history, and lifecycle tools. It does not replace a provider's Apple Music, SiriusXM, or Pandora authorization.

## 3. Functions that are difficult to reproduce outside the official controller

### Directly wired in the desktop

- **Music-service onboarding and repair**: descriptor-driven anonymous/password/device-link/app-link wizards, player account commit, refresh, replace, reauthorize, nickname, and default-account logic.
- **Stateful setup wizard engine**: nested sub-wizards, validation, rollback/cancel behavior, custom pages and provider actions. The generic `SCIWizard` even has private testing controls for enumerating state IDs and jumping between states.
- **Firmware orchestration**: check/update/resume/intro-only flows, compatibility and software-generation gates, controller-update URL handling, and whole-household progress.
- **Household lifecycle**: create/join/forget household, legacy join, rescan, offline device troubleshooting, incompatible/orphaned/unconfigured states, and cloud-assisted discovery.
- **Local library administration**: authenticated SMB share add/remove, indexing, scheduled refresh, and cached browse presentation.
- **Diagnostics aggregation and upload**: player plus controller/host evidence with a returned confirmation number and GUID.

### Native/shared SCLib capabilities, likely mobile-led or only conditionally exposed on desktop

- **Trueplay/Sonar acoustic calibration**: calibration discovery, baseline measurement and noise-meter wizards, calibration-package creation, motion/audio sample callbacks, ambient-noise thresholds, and pruning. This is substantially more than setting a Trueplay on/off property.
- **Speaker topology construction**: bonded sets, missing-pair recovery, permanent rebonding, home-theater source detection, Sub/surround/stereo configuration, room areas, and transactional group membership.
- **Secure registration and ownership lifecycle**: customer registration, ownership transfer, secure-player checks, device recycling/removal, household-admin authorization, and mixed-generation migration.
- **Voice-service setup**: voice account metadata/removal, regional support, per-room voice settings, microphone permission, confirmation tones, locales, and data-collection controls.
- **Cloud/persistent control**: cached households, Wake-on-LAN multicast, cloud-assisted discovery, raw WebSocket support, direct-control session management, connected partners, and MUSE experiment controls.
- **Dynamic service UI/action engine**: arbitrary provider menus, forms, text fields, date/time pickers, info views, custom controls, browse pickers, multiple artwork variants, service attribution, progress/resume state, and action menus. Most open-source controllers reproduce SOAP calls, not this policy/presentation engine.

The comparison with open source is an informed inference, not a proof that no private project implements these features. Common projects focus on discovery, transport, volume/EQ, grouping, queues, alarms, favorites, and basic content browsing. The official controller's hardest advantage is the coordinated state machine around setup, credentials, cloud identity, recovery, calibration, and updates—not ordinary UPnP transport.

## 4. Hidden debug/developer facilities

### The desktop Debug Console (real but disabled by configuration)

`DebugConsole.IsDebugEnabled` reads `configuration/debugConsole/@allow` from `Sonos.Controller.Desktop.dll.config`. The installed config omits this node, so the menu is hidden. The implementation is present in:

- `/tmp/sonos-desktop-decompiled/Sonos.Controller.Desktop.Debug/DebugConsole.cs`
- `/tmp/sonos-desktop-decompiled/Sonos.Controller.Desktop.SCLib.ViewModel/DebugViewModel.cs`

It provides:

| Capability | Risk | Usefulness |
|---|---:|---|
| Native SCLib object count after forced GC | Read-only | Detect wrapper/native object leaks |
| Clear artwork caches | Low mutation | Reproduce artwork loading and invalidation bugs |
| View/change connection-error grace period | Low | Reproduce timing-sensitive discovery behavior |
| Simulate household manager states | High disruption | Exercise incompatible, unconfigured, orphaned, wrong-AP, guest, EOL, insecure, and updating UI |
| Add a global operation delay | High disruption | Reproduce races and slow-network behavior |
| Fail the Nth operation, optionally every subsequent Nth | High disruption | Deterministic error-path testing, including `SCIOpValidateServiceCredentials` |
| Set maximum listener threshold | Medium | Detect subscription leaks |
| Force managed/native crash or discovery error | Destructive | Crash/error reporting tests only |
| “Monkey” mode | Unknown/high | UI stress/automation hook |

None of these controls were enabled or invoked.

### Native `SCIDebug` controls not all surfaced by that desktop window

The bundled interface also exposes per-module diagnostic levels, household scanning, cached-connect and cloud-assisted-discovery toggles, debug event subscription, and synthetic empty “swimlanes.” The most valuable safe operations are getters, object-count inspection, diagnostic-level inspection, and event subscription. Fault injection and state mutation belong only in an isolated test household.

### Content and receipt protocol logging switches

SCLib contains settings for:

- `SCISETTING_CONTENT_DEBUG_LOG_HEADERS`
- `SCISETTING_CONTENT_DEBUG_LOG_REQUEST`
- `SCISETTING_CONTENT_DEBUG_LOG_RESPONSE`
- `SCISETTING_CONTENT_DEBUG_IGNORE_TIER`
- `SCISETTING_RECEIPT_DEBUG_LOG_HEADERS`
- `SCISETTING_RECEIPT_DEBUG_LOG_REQUEST`
- `SCISETTING_RECEIPT_DEBUG_LOG_RESPONSE`
- `SCISETTING_RECEIPT_DEBUG_OVERRIDE_RSLT`

These are probably the single most useful discovery for future music-service work: if routed to a readable diagnostic sink, they should expose controller-side content requests and parsed responses without relying on a TLS interception proxy. “Probably” is deliberate—the identifiers and logging plumbing are proven, but their exact output destination and whether sensitive headers are redacted still need an isolated, offline harness test before use. Header logging could expose reusable credentials and must be scrubbed.

### Host-side diagnostic collection

The desktop explicitly registers files and commands for diagnostic submission in `LibraryManager.AddDiagnosticCommands()`:

- controller Anacapa config and managed-shares file;
- process and service lists;
- `ipconfig`, `netstat`, ARP, and route tables;
- Windows shares, open files, local users/groups, and server/workstation statistics;
- Sonos registry trees in HKLM and HKCU;
- NetBIOS cache/statistics;
- DNS probes for RadioTime and several Sonos hosts.

This is extremely useful for reconstructing what Sonos support considers relevant to discovery failures. It also means a submitted diagnostic contains broad host/network metadata, so it should be treated as sensitive.

### Other buried test/feature surfaces

- `SCIControllerTest`: list/add/forget household IDs, connectivity/software state, enabled feature enumeration, feature-variable reads, support-phase overrides, and destructive factory-reset/flush/crash timers.
- `SCIFeatureManager`: enumerate availability, read feature values/messages, receive changes, update configuration, and unlock a named feature using an unlock key.
- `SCINewWizManager.getInternalWizardActionsEnumerator()`: an internal catalog of wizard actions, potentially the cleanest way to inventory hidden onboarding and lifecycle flows without guessing URIs.
- `SCILibraryTests.connectWebsocket(...)`: raw WebSocket test plumbing with configurable URL, headers, protocols, and connection type.
- `SCISonarCalibrationManager`: baseline measurement/noise-meter actions and calibration package tools.
- Debug actions for pushing/deleting system status, invalidating a session, synthetic search, service-outage fetching, and adding/removing empty content swimlanes.
- Feature/experiment flags for persistent devices, LAN versus cloud discovery, lifecycle system tools, account deletion, room orientation, Wi-Fi signal strength, connected partners, Sonos Voice Control/Spotify, UPnP tunnels, service-outage notifications, and controller configuration reports.
- Developer options for browse picker, “More Music web,” add-another-account picker, UI automation, cross-service search, canonical ratings, recently played, play model/hero view, My Sonos, play in another room, AirPlay education/settings, connected partners, prototype settings, and experiments.

### Local state and embedded server

- `runtime/sonos_application_cache.config` is a rich local state cache, not just window preferences. It contains feature flags and cached alarm/playback metadata including canonical program URIs and DIDL.
- `runtime/uidata.xml` holds the controller's stable machine identifier and MAC identity. These values should not be published.
- `anacapa/conf/anacapa.conf` documents the embedded local HTTP server on port 3400, its connection limit, and an optional diagnostic trace file (`log/anacapa.trace`). The existence of the server is proven; no useful routes have yet been statically mapped, and the broken app was not probed.
- `ctrlMetricsConfig.xml`, embedded in the desktop resources, contains individually gated metrics categories such as `upnp.Browse`, `upnp.getDeviceAuthToken`, `upnp.getDeviceLinkCode`, queue browse, and household connect/suspend/resume events. It is a useful operation map even with every listed category set to `OFF`.

## Highest-value next artifacts to build

These are ordered by value and safety:

1. **Offline descriptor/account inspector**: list authentication type, add/remove capability, existing accounts, default account, account tier, supported interfaces, and action IDs without changing the household.
2. **Redacting content-log harness**: initialize only the native logging path in isolation, determine the exact sink for content request/response logging, and redact Authorization/login tokens before persistence.
3. **Internal wizard catalog dumper**: enumerate action descriptors and wizard types without performing them. This should expose hidden setup/lifecycle entry points safely.
4. **Diagnostic bundle manifest tool**: reproduce the list of evidence Sonos collects but show the user a manifest and redaction preview before any collection or upload.
5. **Explicit-confirmation account onboarding research — partial**: `../sonos_account_onboarding.py` and the GUI reproduce descriptor discovery and provider authorization while keeping credentials in memory. Anonymous `AddAccountX` add/remove is live-proven. Spotify reaches a valid token exchange, but all tested S2 players reject the final `AddOAuthAccountX` mutation with UPnP 402; Apple Music supplies only an encrypted app handoff. OAuth account addition must not be described as completed.

## Bottom line

Adding a music service is not “save a token in the controller.” It is a descriptor-driven authorization wizard that commits a provider account to the players, where it is replicated and then used for browsing and player-side playback resolution. Sonos login is a separate scoped cloud identity system.

The genuinely hard-to-copy parts are the official state machines and trust relationships: setup/recovery, player registration, household ownership, credential commit/refresh, firmware compatibility, calibration, and diagnostics. The most immediately exploitable research win is the controller's own content request/response logging infrastructure, followed by the internal wizard-action catalog.

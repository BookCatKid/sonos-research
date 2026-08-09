# Music-service account onboarding: verified status

## Status

Music-service onboarding is partially reconstructed. Anonymous account addition
works. OAuth/AppLink and DeviceLink discovery and browser authorization work for
some providers, but OAuth account persistence is not currently functional on the
tested S2 household.

The CLI and GUI are research tools. They must not be advertised as production
account-addition support or used as a PyPI readiness claim.

## Proven working

- Player service-descriptor discovery and authentication-mode selection.
- Modern `getAppLink` requests with the official callback state containing the
  encoded account type, per-account OAuth device ID, and `/addAccount` route.
- Legacy `getDeviceLinkCode` fallback.
- Browser authorization and `getDeviceAuthToken` polling.
- Provider token/private-key parsing, user-ID hashing, and account-tier mapping.
- Household identity validation before mutation.
- Anonymous `AddAccountX`: SomaFM Radio (SID 516) was added, appeared exactly once
  in the decrypted household account registry, was removed, and then appeared
  zero times. The test left no account behind.
- Nested UPnP fault parsing, including the actual numeric error code.

## OAuth blocker

Spotify (SID 12, account type 3079) completed browser OAuth. A supplied HAR showed
the expected redirect to Sonos Spotify `/deviceLink/token`, followed by a 200 page
at `/deviceLink/saved`; no application launch or custom-scheme callback occurred.
`getDeviceAuthToken` returned a non-empty token, private key, user hash, nickname,
and the `free` tier.

The final `SystemProperties.AddOAuthAccountX` call returned UPnP 402 (`Invalid
Args`). The rejection was reproduced on all five household players spanning S2
firmware 86.8 and 96.0. A preserved credential response was tested against:

- hashed, raw, and blank user-ID values;
- provider `free` tier and tier zero;
- the generated OAuth device ID, blank device ID, and household device ID;
- completed-token and link-code constructors;
- the exact advertised argument order and an explicit UPnP SOAP envelope.

Every variant returned 402 and no Spotify account appeared in the household
registry. Anonymous mutation succeeding on the same endpoint proves basic SOAP
transport and household mutation access, but does not explain the OAuth-specific
rejection.

## Provider variation

| Provider | Observed authorization result |
|---|---|
| Spotify | Browser OAuth and token exchange succeed; player commit returns 402 |
| Apple Music | `getAppLink` returns `appUrlEncrypt=true` only; no browser URL, link code, or usable app URL |
| Deezer | Browser authorization offered through `getAppLink` |
| TIDAL | Browser authorization offered through legacy `getDeviceLinkCode` |
| YouTube Music | Initial `getAppLink` rejected with HTTP 403 |
| SoundCloud | Initial `getAppLink` rejected as `Client.NOT_AUTHORIZED` |

The Apple Music result affects adding a new account only. It does not invalidate
or alter an existing stored Apple Music account.

## Security and cleanup

- Provider tokens, keys, link codes, and HAR cookies are sensitive and must not
  be committed.
- The supplied Spotify HAR also contained an access token in a telemetry query;
  it remains an external attachment and is not part of this repository.
- Failed OAuth attempts created no accounts.
- The temporary anonymous SomaFM account was removed and its absence verified.

## Shipping boundary

Safe claims:

- browse/search of already-configured accounts;
- descriptor and authorization-path inspection;
- anonymous account mutation where explicitly confirmed;
- experimental OAuth authorization diagnostics.

Unsupported claim: adding OAuth music-service accounts works end to end.

"""Descriptor-driven music-service account onboarding for Sonos households.

The module deliberately separates authorization from the player mutation.  A
provider link can be inspected and opened without changing the household; the
caller must explicitly commit the resulting authorization to SystemProperties.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import secrets
import urllib.error
import urllib.parse
import webbrowser
from dataclasses import asdict, dataclass
from typing import Any

from smapi_browser import (
    Account,
    LocalSoapFault,
    Service,
    SmapiClient,
    decrypt_blob,
    descendants,
    element_value,
    encode_blob,
    inventory,
    local_soap,
    parse_services,
    player_device_id,
    player_zone_id,
)
from sonos_discovery import discover_players

SYSTEM_PROPERTIES = "urn:schemas-upnp-org:service:SystemProperties:1"
SYSTEM_PROPERTIES_PATH = "/SystemProperties/Control"
DEVICE_PROPERTIES = "urn:schemas-upnp-org:service:DeviceProperties:1"
DEVICE_PROPERTIES_PATH = "/DeviceProperties/Control"
CURRENT_ACCOUNT_SCHEMA = 7

#: AccountTier committed with AddOAuthAccountX.  The player's field is numeric
#: (SCPD type ``ui4``); the provider's userInfo.accountTier string
#: (``free``/``premium``/``trial``) is rejected with UPnP 402.  Decompiled from
#: the desktop controller: the wrapped-credentials flow passes a caller byte
#: through verbatim (the captured Windows commit sent ``1``), the local
#: auth-code flow hardcodes ``0``, and ReplaceAccountX carries no tier field.
#: Every path is a per-flow constant -- never the provider subscription level.
ACCOUNT_TIER = "1"
AUTH_OPERATIONS = {
    "Anonymous": "AddAccountX",
    "UserId": "AddAccountX",
    "UserIdPassword": "AddAccountX",
    "DeviceLink": "AddOAuthAccountX",
    "AppLink": "AddOAuthAccountX",
}


class OnboardingError(RuntimeError):
    """A provider or player could not complete an onboarding state."""


@dataclass(frozen=True)
class LinkSession:
    service_id: int
    service_name: str
    auth_type: str
    household_id: str
    account_type: int
    registration_url: str
    link_code: str
    link_device_id: str = ""
    callback_path: str = ""
    app_url: str = ""
    show_link_code: bool = False
    source_action: str = ""

    @property
    def standalone_supported(self) -> bool:
        return bool(
            self.link_code
            and urllib.parse.urlsplit(self.registration_url).scheme.lower() in {"http", "https"}
        )

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if value["link_code"]:
            value["link_code"] = "<redacted>"
        if value["link_device_id"]:
            value["link_device_id"] = "<redacted>"
        return value


@dataclass(frozen=True)
class AddedAccount:
    service_id: int
    service_name: str
    account_udn: str
    nickname: str = ""
    #: The provider's userInfo.nickname (the account holder's screen name as
    #: reported by getDeviceAuthToken).  Informational only: the official app
    #: pre-fills its nickname prompt with this and the user's choice is then
    #: applied with SetAccountNicknameX -- the player never sees this value
    #: inside AddOAuthAccountX.
    provider_nickname: str = ""


@dataclass(frozen=True)
class DeviceAuthCredential:
    """The provider credential package AddOAuthAccountX installs.

    Obtained by the controller itself through the provider's SMAPI
    ``getDeviceAuthToken`` after the user finishes the browser authorization.
    The player does NOT exchange the link code; it receives this result.
    """

    auth_token: str
    private_key: str
    user_id_hash_code: str = ""
    nickname: str = ""

    # Note: the provider's userInfo.accountTier string (``free`` / ``premium`` /
    # ``trial``) is deliberately NOT carried here -- the player's AccountTier
    # field is numeric (SCPD type ``ui4``) and rejects the string with UPnP 402.
    # The player stores a per-record flag (``0``/``1``), not the provider
    # subscription level; see ACCOUNT_TIER for the committed value.


def account_type(service_id: int, schema_revision: int = CURRENT_ACCOUNT_SCHEMA) -> int:
    if service_id <= 0:
        raise ValueError("service_id must be positive")
    if not 0 <= schema_revision <= 255:
        raise ValueError("schema_revision must be between 0 and 255")
    return service_id * 256 + schema_revision


def _client(host: str, household_id: str, service: Service) -> SmapiClient:
    return SmapiClient(
        service,
        Account(service.service_id, 0, ""),
        household_id,
        player_device_id(host),
        player_zone_id(host),
        host,
    )


def _is_app_link_only_stub(value: Any) -> bool:
    """Detect a provider's encrypted app-link marker with no browser path.

    Apple Music's getAppLink returns only an empty ``callToAction`` and
    ``appUrlEncrypt=true`` -- no ``appUrl``, ``regUrl``, or ``linkCode`` -- for
    every platform identity.  That marker advertises app-to-app linking only,
    so there is no standalone browser authorization this tool could open and
    commit.  The official Sonos desktop app is limited to the same app-to-app
    path.
    """
    if not isinstance(value, dict):
        return False
    authorize = value.get("authorizeAccount", value)
    authorize = authorize if isinstance(authorize, dict) else {}
    device_link = authorize.get("deviceLink", authorize)
    device_link = device_link if isinstance(device_link, dict) else {}
    has_browser_path = bool(device_link.get("regUrl") or device_link.get("linkCode"))
    has_app_url = bool(authorize.get("appUrl") or value.get("appUrl"))
    encrypted = str(
        authorize.get("appUrlEncrypt", value.get("appUrlEncrypt", ""))
    ).lower() == "true"
    return encrypted and not has_browser_path and not has_app_url


def _link_from_result(
    service: Service,
    household_id: str,
    callback_path: str,
    action: str,
    value: Any,
) -> LinkSession:
    result = value if isinstance(value, dict) else {}
    authorize = result.get("authorizeAccount", result)
    authorize = authorize if isinstance(authorize, dict) else {}
    device_link = authorize.get("deviceLink", authorize)
    device_link = device_link if isinstance(device_link, dict) else {}
    app_url = str(authorize.get("appUrl", result.get("appUrl", "")) or "")
    return LinkSession(
        service_id=service.service_id,
        service_name=service.name,
        auth_type=service.auth,
        household_id=household_id,
        account_type=account_type(service.service_id),
        registration_url=str(device_link.get("regUrl", "") or ""),
        link_code=str(device_link.get("linkCode", "") or ""),
        link_device_id=str(device_link.get("linkDeviceId", "") or ""),
        callback_path=callback_path,
        app_url=app_url,
        show_link_code=str(device_link.get("showLinkCode", "")).lower() == "true",
        source_action=action,
    )


def begin_link(
    host: str,
    household_id: str,
    service: Service,
    *,
    callback_path: str = "sonos://addAccount",
) -> LinkSession:
    """Ask the provider for its browser/app authorization choices.

    Modern services use getAppLink.  Older DeviceLink services are retried with
    getDeviceLinkCode, exactly as their descriptor requires.  No player state is
    changed by this function.
    """
    if service.auth not in AUTH_OPERATIONS:
        raise OnboardingError(f"{service.name} uses unsupported authentication type {service.auth!r}")
    if service.auth == "Anonymous":
        return LinkSession(
            service.service_id,
            service.name,
            service.auth,
            household_id,
            account_type(service.service_id),
            "",
            "",
            callback_path=callback_path,
            source_action="anonymous",
        )
    if AUTH_OPERATIONS[service.auth] != "AddOAuthAccountX":
        raise OnboardingError(f"{service.name} uses credentials; call add_credentials instead")
    client = _client(host, household_id, service)
    app_link_error: Exception | None = None
    try:
        root = client._request(
            "getAppLink",
            {
                "householdId": household_id,
                # Match the installed desktop controller's getAppLink request so
                # providers select their desktop/browser authorization path.
                "hardware": "Windows",
                "osVersion": "Microsoft Windows NT 10.0.19045 64-bit",
                "sonosAppName": "Sonos",
                "callbackPath": callback_path,
            },
            credential_mode="base",
            bearer_token="",
        )
        nodes = descendants(root, "getAppLinkResult")
        value = element_value(nodes[0] if nodes else root)
        session = _link_from_result(service, household_id, callback_path, "getAppLink", value)
        # Only AppLink services hit the actionable app-only error: a DeviceLink
        # service returning this stub must still fall back to getDeviceLinkCode.
        if _is_app_link_only_stub(value) and service.auth != "DeviceLink":
            raise OnboardingError(
                f"{service.name} offers app-to-app linking only: getAppLink returned an "
                "encrypted app-link marker (appUrlEncrypt=true) with no browser URL or "
                "link code. Providers such as Apple Music restrict initial authorization "
                "to the Sonos mobile app (iOS/Android); even the official Sonos desktop "
                "app cannot add them. Link the account once from the Sonos phone app, "
                "then this tool can browse, manage, and rename it like any other account."
            )
        if session.standalone_supported or session.app_url or service.auth != "DeviceLink":
            return session
    except OnboardingError:
        # Our own actionable errors (e.g. the app-only marker) must not be
        # mistaken for a rejected getAppLink and wrapped again.
        raise
    except (RuntimeError, TimeoutError, urllib.error.URLError) as exc:
        # Legacy services commonly reject getAppLink.
        app_link_error = exc

    if service.auth != "DeviceLink":
        if app_link_error:
            raise OnboardingError(f"{service.name} getAppLink failed: {app_link_error}") from app_link_error
        raise OnboardingError(f"{service.name} returned no usable authorization path")

    try:
        root = client._request(
            "getDeviceLinkCode",
            {"householdId": household_id},
            credential_mode="base",
            bearer_token="",
        )
    except (RuntimeError, TimeoutError, urllib.error.URLError) as exc:
        raise OnboardingError(
            f"{service.name} supports neither getAppLink nor getDeviceLinkCode: {exc}"
        ) from exc
    nodes = descendants(root, "getDeviceLinkCodeResult")
    session = _link_from_result(
        service,
        household_id,
        callback_path,
        "getDeviceLinkCode",
        element_value(nodes[0] if nodes else root),
    )
    if not session.standalone_supported:
        raise OnboardingError(f"{service.name} returned no browser URL or link code")
    return session


def _parse_add_response(service: Service, response: bytes, household_id: str = "") -> AddedAccount:
    """Parse the player's AddAccountX/AddOAuthAccountX response.

    The player returns the AccountUDN in the household ``2:`` envelope (the
    same envelope as ThirdPartyMediaServersX).  With the household ID known the
    UDN is decrypted to its canonical ``SA_RINCON...`` form so it matches the
    account inventory; without it the raw value is preserved.
    """
    import xml.etree.ElementTree as ET

    root = ET.fromstring(response)
    udn_nodes = descendants(root, "AccountUDN")
    nickname_nodes = descendants(root, "AccountNickname")
    udn = (udn_nodes[0].text or "").strip() if udn_nodes else ""
    if not udn:
        raise OnboardingError("Player reported success but returned no AccountUDN")
    if udn.startswith("2:") and household_id:
        udn = decrypt_blob(udn, household_id).decode("utf-8")
    return AddedAccount(
        service.service_id,
        service.name,
        udn,
        (nickname_nodes[0].text or "").strip() if nickname_nodes else "",
    )


def player_household(host: str) -> str:
    """Read the household currently advertised by one target player."""
    response = local_soap(
        host,
        DEVICE_PROPERTIES_PATH,
        DEVICE_PROPERTIES,
        "GetHouseholdID",
        {},
    )
    import xml.etree.ElementTree as ET

    root = ET.fromstring(response)
    nodes = descendants(root, "CurrentHouseholdID")
    household_id = (nodes[0].text or "").strip() if nodes else ""
    if not household_id:
        raise OnboardingError(f"Player {host} returned no household ID")
    return household_id


def _require_household(host: str, expected_household: str) -> str:
    actual_household = player_household(host)
    if actual_household != expected_household:
        raise OnboardingError(
            f"Target expects {expected_household}, but player {host} belongs to {actual_household}"
        )
    return actual_household


def get_device_auth_token(
    host: str,
    household_id: str,
    service: Service,
    link_code: str,
    link_device_id: str = "",
) -> DeviceAuthCredential:
    """Exchange an authorized link code for the provider credential package.

    The controller performs this provider SMAPI call itself after the user
    finishes the browser authorization.  The result -- authToken, privateKey,
    and userInfo (userIdHashCode, accountTier, nickname) -- is what
    AddOAuthAccountX then installs into the player; the player does not
    exchange the link code itself.  AddOAuthAccountX receives these values as
    ``2:``-enveloped fields with AuthorizationCode/RedirectURI empty and the
    household ID as OAuthDeviceID.
    """
    client = _client(host, household_id, service)
    root = client._request(
        "getDeviceAuthToken",
        {
            "householdId": household_id,
            "linkCode": link_code,
            "linkDeviceId": link_device_id or player_device_id(host),
        },
        credential_mode="base",
        bearer_token="",
    )
    nodes = descendants(root, "getDeviceAuthTokenResult")
    value = element_value(nodes[0] if nodes else root)
    result = value if isinstance(value, dict) else {}
    user_info = result.get("userInfo", {})
    user_info = user_info if isinstance(user_info, dict) else {}
    token = str(result.get("authToken", "") or "")
    key = str(result.get("privateKey", "") or "")
    if not token or not key:
        raise OnboardingError(
            f"{service.name} getDeviceAuthToken returned no authToken/privateKey pair; "
            "the link code may have expired or already been exchanged."
        )
    return DeviceAuthCredential(
        auth_token=token,
        private_key=key,
        user_id_hash_code=str(user_info.get("userIdHashCode", "") or ""),
        # The provider's screen name; the official app pre-fills the account
        # nickname prompt with it (informational only, never sent to the player).
        nickname=str(user_info.get("nickname", "") or ""),
    )


def commit_link(
    host: str,
    service: Service,
    session: LinkSession,
    *,
    replace_account_udn: str = "",
) -> AddedAccount:
    """Commit an authorized provider link to the household's players.

    Mirrors the desktop controller's commit dispatcher, which has two paths:
    a fresh account is installed through ``AddOAuthAccountX``, while a
    re-linked account is replaced in place through ``ReplaceAccountX`` (pass
    ``replace_account_udn`` to select that path).  The player does not
    exchange the link code itself: the controller first calls the provider's
    ``getDeviceAuthToken`` for the credential package, then commits it.

    The fresh-add path sends every account value wrapped in the household
    ``2:`` envelope: AccountToken, AccountKey (the provider's key, which
    already carries its own epoch stamp), OAuthDeviceID (the household ID),
    and UserIdHashCode.  AuthorizationCode and RedirectURI stay empty and
    AccountTier is the ACCOUNT_TIER constant.
    """
    if session.service_id != service.service_id:
        raise OnboardingError("Link session belongs to a different service")
    expected_account_type = account_type(service.service_id)
    if session.account_type != expected_account_type:
        raise OnboardingError("Link session account type does not match its service")
    if AUTH_OPERATIONS.get(service.auth) != "AddOAuthAccountX":
        raise OnboardingError(f"{service.name} does not use linked-account onboarding")
    if not session.link_code:
        raise OnboardingError(
            f"{service.name} did not provide a standalone link code; its app-only authorization cannot be committed here"
        )
    _require_household(host, session.household_id)
    credential = get_device_auth_token(
        host,
        session.household_id,
        service,
        session.link_code,
        session.link_device_id,
    )
    if replace_account_udn:
        # Re-link path: the record keeps its UDN and only the credential
        # package is swapped, exactly like the desktop's per-account replace.
        return replace_account_credentials(
            host,
            service,
            replace_account_udn,
            credential,
            household_id=session.household_id,
        )
    user_id_hash = (
        encode_blob(_normalize_user_id_hash(credential.user_id_hash_code).encode("utf-8"), session.household_id)
        if credential.user_id_hash_code
        else ""
    )
    # The provider's privateKey already carries its own ``/<epoch_millis>``
    # stamp, so the key is enveloped verbatim.  AccountTier is ACCOUNT_TIER.
    try:
        response = local_soap(
            host,
            SYSTEM_PROPERTIES_PATH,
            SYSTEM_PROPERTIES,
            "AddOAuthAccountX",
            {
                "AccountType": str(expected_account_type),
                # Account values must be wrapped in the household 2: envelope;
                # plaintext values are rejected (UPnP 402).
                "AccountToken": encode_blob(credential.auth_token.encode("utf-8"), session.household_id),
                "AccountKey": encode_blob(credential.private_key.encode("utf-8"), session.household_id),
                "OAuthDeviceID": encode_blob(session.household_id.encode("utf-8"), session.household_id),
                "AuthorizationCode": "",
                "RedirectURI": "",
                "UserIdHashCode": user_id_hash,
                "AccountTier": ACCOUNT_TIER,
            },
            timeout=35,
        )
    except LocalSoapFault as exc:
        translated = _translate_commit_fault(host, service, exc, session.household_id)
        if translated is not None:
            raise translated from exc
        raise
    added = _parse_add_response(service, response, session.household_id)
    return AddedAccount(
        added.service_id,
        added.service_name,
        added.account_udn,
        added.nickname,
        provider_nickname=credential.nickname,
    )


def replace_account_credentials(
    host: str,
    service: Service,
    account_udn: str,
    credential: DeviceAuthCredential,
    *,
    household_id: str,
) -> AddedAccount:
    """Replace one existing household account's stored credentials in place.

    Native contract (FUN_100e61e60 / FUN_1004aced0, confirmed against the
    player's SystemProperties SCPD): ReplaceAccountX takes AccountUDN (the
    existing record), NewAccountID, NewAccountPassword, AccountToken,
    AccountKey, OAuthDeviceID, and NewAccountUDN.  The desktop controller's
    commit dispatcher uses this action when re-linking an account instead of
    AddOAuthAccountX: the record keeps its UDN and only the credential
    package is swapped, so no duplicate record or account-slot clash is
    created.  OAuth-style services leave the legacy NewAccountID/
    NewAccountPassword pair empty, exactly as the desktop's replace commit
    does.

    The credential values follow the AddOAuthAccountX envelope contract
    (household ``2:`` envelope); ReplaceAccountX itself has no output
    arguments (SCPD), so the existing UDN is reported unchanged.
    """
    if service.service_id <= 0:
        raise OnboardingError(f"{service.name} has no usable service ID")
    if not account_udn:
        raise OnboardingError("An account UDN is required to replace an account")
    if not credential.auth_token or not credential.private_key:
        raise OnboardingError("A complete credential package is required to replace an account")
    _require_household(host, household_id)
    if account_udn.startswith("2:"):
        account_udn = decrypt_blob(account_udn, household_id).decode("utf-8")
    try:
        local_soap(
            host,
            SYSTEM_PROPERTIES_PATH,
            SYSTEM_PROPERTIES,
            "ReplaceAccountX",
            {
                "AccountUDN": encode_blob(account_udn.encode("utf-8"), household_id),
                "NewAccountID": "",
                "NewAccountPassword": "",
                "AccountToken": encode_blob(credential.auth_token.encode("utf-8"), household_id),
                "AccountKey": encode_blob(credential.private_key.encode("utf-8"), household_id),
                "OAuthDeviceID": encode_blob(household_id.encode("utf-8"), household_id),
                "NewAccountUDN": "",
            },
            timeout=35,
        )
    except LocalSoapFault as exc:
        if exc.upnp_code is not None:
            raise OnboardingError(
                f"The player rejected ReplaceAccountX for the account (UPnP error "
                f"{exc.upnp_code}: {exc.upnp_description or 'invalid arguments'}). "
                "No account state was changed."
            ) from exc
        raise
    return AddedAccount(
        service.service_id,
        service.name,
        account_udn,
        provider_nickname=credential.nickname,
    )


def _normalize_user_id_hash(user_id_hash_code: str) -> str:
    """Return the player's required base64 form of the user-id hash.

    The player accepts ``UserIdHashCode`` only in base64; a 32-character hex
    value (as the provider currently returns) is converted, and any other form
    passes through unchanged.
    """
    value = user_id_hash_code.strip()
    if re.fullmatch(r"[0-9a-fA-F]{32}", value):
        return base64.b64encode(bytes.fromhex(value)).decode("ascii")
    return value


def _translate_commit_fault(
    host: str, service: Service, fault: LocalSoapFault, household_id: str
) -> OnboardingError | None:
    """Turn a player rejection of AddOAuthAccountX into an actionable error.

    A UPnP 402 can have several causes (historically, a malformed
    UserIdHashCode).  When the household already holds an account for this
    service, a duplicate-add refusal is the most plausible one, so the
    inventory is checked to point at the existing account instead of surfacing
    a bare ``invalid arguments``.  Returns None when the fault cannot be
    explained, so the caller re-raises the original fault unchanged.
    """
    if fault.upnp_code == 402:
        try:
            _services, accounts = inventory(host, household_id)
        except Exception:
            _services, accounts = {}, []
        existing = [a for a in accounts if a.service_id == service.service_id]
        if existing:
            names = sorted({(a.nickname or a.username or a.serial) for a in existing})
            return OnboardingError(
                f"{service.name} is already linked to this household as "
                f"{', '.join(map(str, names))}. The player most likely rejected the "
                "duplicate commit (UPnP error 402: invalid arguments). Reauthorize the "
                "existing account in place (ReplaceAccountX) instead of adding a duplicate."
            )
    return None


def add_credentials(
    host: str,
    service: Service,
    username: str,
    password: str,
    *,
    household_id: str,
) -> AddedAccount:
    """Add an anonymous or legacy username/password service account.

    Anonymous descriptors are committed with an empty account ID: the player
    rejects any other value for them (UPnP error 402) and stores a keyless
    record.  Such records remain browsable and can be removed again with the
    empty-key RemoveAccount contract.
    """
    if service.auth not in {"Anonymous", "UserId", "UserIdPassword"}:
        raise OnboardingError(f"{service.name} requires {service.auth}; use begin_link instead")
    if service.auth in {"UserId", "UserIdPassword"} and not username:
        raise OnboardingError(f"{service.name} requires a username")
    if service.auth == "UserIdPassword" and not password:
        raise OnboardingError(f"{service.name} requires a password")
    _require_household(host, household_id)
    response = local_soap(
        host,
        SYSTEM_PROPERTIES_PATH,
        SYSTEM_PROPERTIES,
        "AddAccountX",
        {
            "AccountType": str(account_type(service.service_id)),
            "AccountID": username,
            "AccountPassword": password,
        },
        timeout=35,
    )
    return _parse_add_response(service, response)


def set_nickname(host: str, account_udn: str, nickname: str, *, household_id: str) -> None:
    """Rename one configured household account.

    Native contract (FUN_10037c5c0 / FUN_1004ae120 / FUN_100e610d0):
    SetAccountNicknameX takes AccountUDN and
    AccountNickname, both wrapped in the household ``2:`` envelope (AES-128-CBC
    under the household-derived key, the same envelope as
    ThirdPartyMediaServersX); plaintext values are rejected with UPnP error
    402.  The account identifier may arrive in either form: the plaintext
    ``SA_RINCON...`` UDN from the account inventory, or the ``2:`` blob
    returned by AddAccountX/AddOAuthAccountX -- the latter is decoded back to
    plaintext first so it is never double-encoded.
    """
    if not account_udn:
        raise OnboardingError("An account UDN is required to rename an account")
    _require_household(host, household_id)
    if account_udn.startswith("2:"):
        account_udn = decrypt_blob(account_udn, household_id).decode("utf-8")
    encoded_udn = encode_blob(account_udn.encode("utf-8"), household_id)
    encoded_nickname = encode_blob(nickname.encode("utf-8"), household_id)
    try:
        local_soap(
            host,
            SYSTEM_PROPERTIES_PATH,
            SYSTEM_PROPERTIES,
            "SetAccountNicknameX",
            {"AccountUDN": encoded_udn, "AccountNickname": encoded_nickname},
        )
    except LocalSoapFault as exc:
        if exc.upnp_code is not None:
            raise OnboardingError(
                f"The player rejected SetAccountNicknameX for the account (UPnP error "
                f"{exc.upnp_code}: {exc.upnp_description or 'invalid arguments'}). "
                "No account state was changed."
            ) from exc
        raise


def remove_account(host: str, service: Service, account_udn: str, *, household_id: str) -> None:
    """Remove one configured account from every player in the household.

    Native contract (FUN_100e60cb0 / FUN_1004abd20): RemoveAccount takes the
    encoded AccountType and the account key as AccountID.  Keyed accounts carry
    the full ``SA_RINCON...`` UDN, which the player resolves for removal.
    Keyless records (empty Username0, truncated UDN) resolve only with an empty
    AccountID: ``RemoveAccount(type, "")`` returns 200 and removes exactly that
    service's keyless record, while the truncated UDN is rejected with UPnP
    error 806.
    """
    if service.service_id <= 0:
        raise OnboardingError(f"{service.name} has no usable service ID")
    if not account_udn:
        raise OnboardingError("An account UDN is required to remove an account")
    _require_household(host, household_id)
    prefix = f"SA_RINCON{account_type(service.service_id)}_"
    key = account_udn[len(prefix):] if account_udn.startswith(prefix) else account_udn
    account_id = account_udn if key else ""
    local_soap(
        host,
        SYSTEM_PROPERTIES_PATH,
        SYSTEM_PROPERTIES,
        "RemoveAccount",
        {
            "AccountType": str(account_type(service.service_id)),
            "AccountID": account_id,
        },
        timeout=35,
    )


def _account_key(service: Service, account_udn: str) -> str:
    """Return the player's account-key identifier for edit operations.

    Edit operations resolve the account only when AccountID is the key tail
    after the encoded-type prefix (``X_#Svc...-Token``, stored as Username0);
    the full ``SA_RINCON...`` UDN is rejected with UPnP error 806.  For legacy
    credential accounts the key is assumed to be the username AddAccountX
    committed; if the prefix does not match, the UDN is passed through
    unchanged as a fallback.
    """
    prefix = f"SA_RINCON{account_type(service.service_id)}_"
    return account_udn[len(prefix):] if account_udn.startswith(prefix) else account_udn


def edit_account_password(
    host: str,
    service: Service,
    account_udn: str,
    new_password: str,
    *,
    household_id: str,
) -> None:
    """Replace a legacy username/password account's stored password.

    Native contract (FUN_100e60eb0 / FUN_1004ade50): EditAccountPasswordX takes
    AccountType, the account key (Username0, the UDN tail after the encoded
    type) as AccountID, and NewAccountPassword.
    """
    if service.service_id <= 0:
        raise OnboardingError(f"{service.name} has no usable service ID")
    if service.auth != "UserIdPassword":
        raise OnboardingError(
            f"{service.name} uses {service.auth}; EditAccountPasswordX applies to UserIdPassword services"
        )
    if not new_password:
        raise OnboardingError(f"{service.name} requires a new password")
    if not account_udn:
        raise OnboardingError("An account UDN is required to edit an account")
    _require_household(host, household_id)
    local_soap(
        host,
        SYSTEM_PROPERTIES_PATH,
        SYSTEM_PROPERTIES,
        "EditAccountPasswordX",
        {
            "AccountType": str(account_type(service.service_id)),
            "AccountID": _account_key(service, account_udn),
            "NewAccountPassword": new_password,
        },
        timeout=35,
    )


def edit_account_md(host: str, service: Service, account_udn: str, new_md: str, *, household_id: str) -> None:
    """Replace the provider metadata blob stored with an account.

    Native contract (FUN_100e61520 / FUN_100cd32b0): EditAccountMd takes
    AccountType, the account key (Username0, the UDN tail after the encoded
    type) as AccountID, and NewAccountMd.
    """
    if service.service_id <= 0:
        raise OnboardingError(f"{service.name} has no usable service ID")
    if not account_udn:
        raise OnboardingError("An account UDN is required to edit an account")
    _require_household(host, household_id)
    local_soap(
        host,
        SYSTEM_PROPERTIES_PATH,
        SYSTEM_PROPERTIES,
        "EditAccountMd",
        {
            "AccountType": str(account_type(service.service_id)),
            "AccountID": _account_key(service, account_udn),
            "NewAccountMd": new_md,
        },
        timeout=35,
    )


def refresh_account_credentials(
    host: str,
    service: Service,
    account_uid: int,
    token: str,
    key: str,
    *,
    household_id: str,
) -> None:
    """Push a freshly obtained token/key pair into the stored account record.

    Native contract (FUN_100e612d0): RefreshAccountCredentialsX takes the
    encoded AccountType, the numeric AccountUID from the account UDN, and the
    AccountToken/AccountKey pair.  This is the player-side persistence of a
    provider reauthorization; it is distinct from
    ``SmapiClient.refresh_auth_token``, which asks the provider for a fresh
    token without writing anything to the player.
    """
    if account_uid <= 0:
        raise OnboardingError("A positive numeric AccountUID is required")
    if not token or not key:
        raise OnboardingError("Both a token and a key are required to refresh credentials")
    _require_household(host, household_id)
    local_soap(
        host,
        SYSTEM_PROPERTIES_PATH,
        SYSTEM_PROPERTIES,
        "RefreshAccountCredentialsX",
        {
            "AccountType": str(account_type(service.service_id)),
            "AccountUID": str(account_uid),
            "AccountToken": token,
            "AccountKey": key,
        },
        timeout=35,
    )


def get_web_code(host: str, service: Service) -> str:
    """Ask the player for the account-activation web code.

    Native contract (FUN_100e60530): GetWebCode takes the encoded AccountType
    and returns a WebCode the user can enter on the provider's site.  This is
    read-only; it does not change any player state.  Modern player firmware
    rejects the legacy action (UPnP error 800), so the rejection is translated
    into an actionable OnboardingError instead of a raw fault.
    """
    import xml.etree.ElementTree as ET

    try:
        response = local_soap(
            host,
            SYSTEM_PROPERTIES_PATH,
            SYSTEM_PROPERTIES,
            "GetWebCode",
            {"AccountType": str(account_type(service.service_id))},
            timeout=35,
        )
    except LocalSoapFault as exc:
        if exc.upnp_code is not None:
            raise OnboardingError(
                f"The player rejected GetWebCode for {service.name} (UPnP error {exc.upnp_code}: "
                f"{exc.upnp_description or 'action not supported for this service on this player'}). "
                "Web-code activation is a legacy flow that current providers and this firmware do not "
                "use; no account state was changed."
            ) from exc
        raise
    root = ET.fromstring(response)
    nodes = descendants(root, "WebCode")
    code = (nodes[0].text or "").strip() if nodes else ""
    if not code:
        raise OnboardingError(f"Player returned no web code for {service.name}")
    return code


def _selected_player(host: str | None):
    players = discover_players(timeout=3)
    if not players:
        raise OnboardingError("No Sonos players were discovered")
    if host:
        for player in players:
            if player.host == host:
                return player
        raise OnboardingError(f"Player {host} was not found in discovered household")
    return players[0]


def _inventory_for(player) -> tuple[dict[int, Service], list[Account]]:
    return inventory(player.host, player.household_id)


def _print_account_rows(services: dict[int, Service], accounts: list[Account]) -> None:
    rows = []
    for account in sorted(accounts, key=lambda value: (value.service_id, value.serial)):
        service = services.get(account.service_id)
        rows.append(
            {
                "service_id": account.service_id,
                "service": service.name if service else "unmapped",
                "auth": service.auth if service else "",
                "serial": account.serial,
                "udn": account.udn,
                "username": account.username,
                "nickname": account.nickname,
                "has_token": bool(account.token),
                "needs_reauth": account.token == "needs_reauth",
            }
        )
    print(json.dumps(rows, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host")
    parser.add_argument("--service-id", type=int)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--commit", action="store_true", help="mutate the household after authorization")
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--nickname", default="")
    manage = parser.add_argument_group("account management")
    manage.add_argument("--list-accounts", action="store_true", help="list configured household accounts")
    manage.add_argument("--account-udn", metavar="UDN", help="SA_RINCON UDN identifying the account for edit operations")
    manage.add_argument("--remove-account", metavar="UDN", help="remove an account by its SA_RINCON UDN")
    manage.add_argument("--new-password", metavar="PASSWORD", help="new password for EditAccountPasswordX")
    manage.add_argument("--new-md", metavar="MD", help="new provider metadata for EditAccountMd")
    manage.add_argument("--replace-account-udn", metavar="UDN", help="re-link in place: commit fresh credentials via ReplaceAccountX instead of AddOAuthAccountX")
    manage.add_argument("--account-uid", type=int, help="numeric AccountUID from the account UDN")
    manage.add_argument("--token", default="", help="replacement token for RefreshAccountCredentialsX")
    manage.add_argument("--key", default="", help="replacement key for RefreshAccountCredentialsX")
    manage.add_argument("--web-code", action="store_true", help="print the player's GetWebCode result")
    args = parser.parse_args()

    player = _selected_player(args.host)

    if args.list_accounts:
        services, accounts = _inventory_for(player)
        _print_account_rows(services, accounts)
        return

    services = parse_services(player.host)
    if args.service_id is None:
        raise SystemExit("--service-id is required for account operations; use --list-accounts to see configured IDs")
    service = services.get(args.service_id)
    if not service:
        raise SystemExit(f"Service {args.service_id} is not advertised by the household")

    if args.web_code:
        code = get_web_code(player.host, service)
        print(json.dumps({"service": service.name, "web_code": code}, indent=2))
        return

    if args.new_password is not None:
        if not args.commit:
            print("Preview: EditAccountPasswordX. Re-run with --commit to change the stored password.")
            return
        if not args.account_udn:
            raise SystemExit("EditAccountPasswordX requires --account-udn as the account identifier")
        edit_account_password(
            player.host,
            service,
            args.account_udn,
            args.new_password,
            household_id=player.household_id,
        )
        print(json.dumps({"status": "password_updated", "account_udn": args.account_udn}, indent=2))
        return

    if args.new_md is not None:
        if not args.commit:
            print("Preview: EditAccountMd. Re-run with --commit to write the provider metadata.")
            return
        if not args.account_udn:
            raise SystemExit("EditAccountMd requires --account-udn as the account identifier")
        edit_account_md(player.host, service, args.account_udn, args.new_md, household_id=player.household_id)
        print(json.dumps({"status": "metadata_updated", "account_udn": args.account_udn}, indent=2))
        return

    if args.account_uid is not None:
        if not args.commit:
            print("Preview: RefreshAccountCredentialsX. Re-run with --commit to write the token/key pair.")
            return
        refresh_account_credentials(
            player.host,
            service,
            args.account_uid,
            args.token,
            args.key,
            household_id=player.household_id,
        )
        print(json.dumps({"status": "credentials_refreshed", "account_uid": args.account_uid}, indent=2))
        return

    if args.remove_account:
        if not args.commit:
            print(
                json.dumps(
                    {
                        "household": player.household_id,
                        "player": player.host,
                        "service": service.name,
                        "operation": "RemoveAccount",
                        "account_udn": args.remove_account,
                        "will_mutate": False,
                    },
                    indent=2,
                )
            )
            print("Preview only. Re-run with --commit to remove this account from the household.")
            return
        remove_account(player.host, service, args.remove_account, household_id=player.household_id)
        print(json.dumps({"status": "removed", "account_udn": args.remove_account}, indent=2))
        return

    if service.auth in {"Anonymous", "UserId", "UserIdPassword"}:
        if args.replace_account_udn:
            raise SystemExit(
                f"--replace-account-udn applies to linked (OAuth) accounts; {service.name} uses {service.auth} credentials"
            )
        preview = {
            "household": player.household_id,
            "player": player.host,
            "service": service.name,
            "auth": service.auth,
            "operation": "AddAccountX",
            "will_mutate": args.commit,
        }
        print(json.dumps(preview, indent=2))
        if not args.commit:
            return
        result = add_credentials(
            player.host,
            service,
            args.username,
            args.password,
            household_id=player.household_id,
        )
    else:
        callback = f"sonos://addAccount?state={secrets.token_urlsafe(24)}"
        session = begin_link(player.host, player.household_id, service, callback_path=callback)
        print(json.dumps(session.public_dict(), indent=2))
        if not args.commit:
            print("Authorization was prepared only. Use --commit to complete this as one interactive transaction.")
            return
        if not session.standalone_supported:
            raise SystemExit(f"{service.name} does not offer a standalone browser authorization path")
        if args.open_browser:
            webbrowser.open(session.registration_url)
        else:
            print(f"Open this URL and finish signing in:\n{session.registration_url}")
        input("After the provider confirms authorization, press Enter to commit this same link code… ")
        result = commit_link(
            player.host,
            service,
            session,
            replace_account_udn=args.replace_account_udn or "",
        )

    if args.nickname:
        set_nickname(player.host, result.account_udn, args.nickname, household_id=player.household_id)
        result = AddedAccount(result.service_id, result.service_name, result.account_udn, args.nickname)
    print(json.dumps(asdict(result), indent=2))


if __name__ == "__main__":
    main()

"""Experimental music-service account onboarding for Sonos households.

The module deliberately separates authorization from the player mutation.  A
provider link can be inspected and opened without changing the household; the
caller must explicitly commit the resulting authorization to SystemProperties.

Anonymous ``AddAccountX`` is live-tested. Provider authorization succeeds for
several linked services, but tested S2 players currently reject the final
``AddOAuthAccountX`` mutation with UPnP 402. Treat linked-account commit as a
research probe, not a supported production feature.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import webbrowser
from dataclasses import asdict, dataclass
from typing import Any

from smapi_browser import (
    Account,
    Service,
    SmapiClient,
    SmapiFault,
    child_text,
    descendants,
    element_value,
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
AUTH_OPERATIONS = {
    "Anonymous": "AddAccountX",
    "UserId": "AddAccountX",
    "UserIdPassword": "AddAccountX",
    "DeviceLink": "AddOAuthAccountX",
    "AppLink": "AddOAuthAccountX",
}
ACCOUNT_TIERS = {
    "unknown": 0,
    "free": 1,
    "paidlimited": 2,
    "paidpremium": 3,
    "none": 0xFF,
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
            and urllib.parse.urlsplit(self.registration_url).scheme.lower()
            in {"http", "https"}
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


@dataclass(frozen=True)
class ExchangedLink:
    """Credentials returned after a provider confirms a browser link."""

    token: str
    key: str
    oauth_device_id: str
    user_id_hash: str = ""
    account_tier: str = ""
    nickname: str = ""


def account_type(service_id: int, schema_revision: int = CURRENT_ACCOUNT_SCHEMA) -> int:
    if service_id <= 0:
        raise ValueError("service_id must be positive")
    if not 0 <= schema_revision <= 255:
        raise ValueError("schema_revision must be between 0 and 255")
    return service_id * 256 + schema_revision


def account_tier(value: str) -> int:
    """Translate the SMAPI tier name to the numeric controller/player value."""
    normalized = value.strip()
    if normalized.isdecimal():
        return int(normalized)
    return ACCOUNT_TIERS.get(normalized.lower(), 0)


def new_oauth_device_id(household_id: str) -> str:
    """Create the per-account OAuth identity used by the desktop controller."""
    if not household_id:
        raise ValueError("household_id cannot be empty")
    account_uid = 0
    while account_uid == 0:
        account_uid = secrets.randbits(32)
    return f"{household_id}_{account_uid:08x}"


def app_link_callback(account_type_id: int, oauth_device_id: str, route: str) -> str:
    """Build the callback URI/state envelope used by official Sonos clients."""
    state = (
        f"sid={account_type_id}&OAuthDeviceID={oauth_device_id}"
        f"&callbackPath={route}"
    )
    query = urllib.parse.urlencode({"state": state})
    return f"sonos://x-callback-url/addAccount?{query}"


def _client(
    host: str,
    household_id: str,
    service: Service,
    *,
    device_id: str | None = None,
) -> SmapiClient:
    return SmapiClient(
        service,
        Account(service.service_id, 0, ""),
        household_id,
        device_id or player_device_id(host),
        player_zone_id(host),
        host,
    )


def _link_from_result(
    service: Service,
    household_id: str,
    callback_path: str,
    action: str,
    value: Any,
    oauth_device_id: str = "",
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
        link_device_id=str(device_link.get("linkDeviceId", "") or oauth_device_id),
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
    callback_path: str = "/addAccount",
) -> LinkSession:
    """Ask the provider for its browser/app authorization choices.

    Modern services use getAppLink.  Older DeviceLink services are retried with
    getDeviceLinkCode, exactly as their descriptor requires.  No player state is
    changed by this function.
    """
    if service.auth not in AUTH_OPERATIONS:
        raise OnboardingError(
            f"{service.name} uses unsupported authentication type {service.auth!r}"
        )
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
        raise OnboardingError(
            f"{service.name} uses credentials; call add_credentials instead"
        )
    # Each new linked account gets a random 32-bit identity suffix. The same
    # value must be used for every provider and player step in the transaction.
    oauth_device_id = new_oauth_device_id(household_id)
    client = _client(host, household_id, service, device_id=oauth_device_id)
    provider_callback = app_link_callback(
        account_type(service.service_id), oauth_device_id, callback_path
    )
    app_link_error: Exception | None = None
    try:
        root = client._request(
            "getAppLink",
            {
                "householdId": household_id,
                # Match the installed desktop's SCLib parameters. Its app
                # interop reports UNKNOWN for native-app installation and only
                # opens HTTP(S), so providers choose their desktop/browser path.
                "hardware": "Windows",
                "osVersion": "Microsoft Windows NT 10.0.19045 64-bit",
                "sonosAppName": "Sonos",
                "callbackPath": provider_callback,
            },
            credential_mode="base",
            bearer_token="",
        )
        nodes = descendants(root, "getAppLinkResult")
        session = _link_from_result(
            service,
            household_id,
            callback_path,
            "getAppLink",
            element_value(nodes[0] if nodes else root),
            oauth_device_id,
        )
        if (
            session.standalone_supported
            or session.app_url
            or service.auth != "DeviceLink"
        ):
            return session
    except (RuntimeError, TimeoutError, urllib.error.URLError) as exc:
        # Legacy services commonly reject getAppLink.
        app_link_error = exc

    if service.auth != "DeviceLink":
        if app_link_error:
            raise OnboardingError(
                f"{service.name} getAppLink failed: {app_link_error}"
            ) from app_link_error
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
        oauth_device_id,
    )
    if not session.standalone_supported:
        raise OnboardingError(f"{service.name} returned no browser URL or link code")
    return session


def _parse_add_response(service: Service, response: bytes) -> AddedAccount:
    import xml.etree.ElementTree as ET

    root = ET.fromstring(response)
    udn_nodes = descendants(root, "AccountUDN")
    nickname_nodes = descendants(root, "AccountNickname")
    udn = (udn_nodes[0].text or "").strip() if udn_nodes else ""
    if not udn:
        raise OnboardingError("Player reported success but returned no AccountUDN")
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


def exchange_link_code(
    host: str,
    household_id: str,
    service: Service,
    session: LinkSession,
    *,
    retries: int = 24,
    retry_delay: float = 5.0,
) -> ExchangedLink:
    """Exchange an authorized link code for the provider's account credentials.

    Browser authorization and household mutation are separate operations.  The
    provider first returns a short-lived link code from ``getAppLink`` (or
    ``getDeviceLinkCode``).  Once the user authorizes that code, this operation
    polls ``getDeviceAuthToken`` for the token/key pair that the player can
    persist with ``AddOAuthAccountX``.
    """
    if retries < 1:
        raise ValueError("retries must be positive")
    if retry_delay < 0:
        raise ValueError("retry_delay cannot be negative")

    client = _client(
        host,
        household_id,
        service,
        device_id=session.link_device_id or household_id,
    )
    oauth_device_id = session.link_device_id or household_id
    last_error: SmapiFault | None = None
    for attempt in range(retries):
        try:
            root = client._request(
                "getDeviceAuthToken",
                {
                    "householdId": household_id,
                    "linkCode": session.link_code,
                    "linkDeviceId": oauth_device_id,
                },
                credential_mode="base",
                bearer_token="",
            )
            break
        except SmapiFault as fault:
            combined = f"{fault.code} {fault.message}".lower()
            if "not_linked" not in combined and "retry" not in combined:
                raise
            last_error = fault
            if attempt < retries - 1:
                time.sleep(retry_delay)
    else:
        raise OnboardingError(
            f"{service.name} did not confirm browser authorization: {last_error}"
        )

    result_nodes = descendants(root, "getDeviceAuthTokenResult")
    result = result_nodes[0] if result_nodes else root
    token = child_text(result, "authToken")
    key = child_text(result, "privateKey")
    if not token or not key:
        raise OnboardingError(
            f"{service.name} getDeviceAuthToken returned no token/key pair"
        )
    user_nodes = descendants(result, "userInfo")
    user = user_nodes[0] if user_nodes else result
    return ExchangedLink(
        token=token,
        key=key,
        oauth_device_id=oauth_device_id,
        user_id_hash=child_text(user, "userIdHashCode"),
        account_tier=child_text(user, "accountTier"),
        nickname=child_text(user, "nickname"),
    )


def encode_user_id_hash(value: str) -> str:
    """Apply the desktop controller's truncated-SHA256/Base64 transformation."""
    if not value:
        return ""
    digest = hashlib.sha256(value.encode()).digest()[:16]
    return base64.b64encode(digest).decode()


def commit_link(host: str, service: Service, session: LinkSession) -> AddedAccount:
    """Exchange a browser link and persist its credentials on the household."""
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
    exchanged = exchange_link_code(host, session.household_id, service, session)
    response = local_soap(
        host,
        SYSTEM_PROPERTIES_PATH,
        SYSTEM_PROPERTIES,
        "AddOAuthAccountX",
        {
            "AccountType": str(expected_account_type),
            "AccountToken": exchanged.token,
            "AccountKey": exchanged.key,
            "OAuthDeviceID": session.link_device_id,
            "AuthorizationCode": "",
            "RedirectURI": "",
            "UserIdHashCode": encode_user_id_hash(exchanged.user_id_hash),
            "AccountTier": str(account_tier(exchanged.account_tier)),
        },
        timeout=35,
    )
    added = _parse_add_response(service, response)
    if not added.nickname and exchanged.nickname:
        return AddedAccount(
            added.service_id,
            added.service_name,
            added.account_udn,
            exchanged.nickname,
        )
    return added


def add_credentials(
    host: str,
    service: Service,
    username: str,
    password: str,
    *,
    household_id: str,
) -> AddedAccount:
    """Add an anonymous or legacy username/password service account."""
    if service.auth not in {"Anonymous", "UserId", "UserIdPassword"}:
        raise OnboardingError(
            f"{service.name} requires {service.auth}; use begin_link instead"
        )
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


def set_nickname(host: str, account_udn: str, nickname: str) -> None:
    local_soap(
        host,
        SYSTEM_PROPERTIES_PATH,
        SYSTEM_PROPERTIES,
        "SetAccountNicknameX",
        {"AccountUDN": account_udn, "AccountNickname": nickname},
    )


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host")
    parser.add_argument("--service-id", type=int, required=True)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument(
        "--commit",
        action="store_true",
        help="attempt the experimental household mutation after authorization",
    )
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--nickname", default="")
    args = parser.parse_args()

    player = _selected_player(args.host)
    services = parse_services(player.host)
    service = services.get(args.service_id)
    if not service:
        raise SystemExit(
            f"Service {args.service_id} is not advertised by the household"
        )

    if service.auth in {"Anonymous", "UserId", "UserIdPassword"}:
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
        callback = "/addAccount"
        session = begin_link(
            player.host, player.household_id, service, callback_path=callback
        )
        print(json.dumps(session.public_dict(), indent=2))
        if not args.commit:
            print(
                "Authorization was prepared only. Use --commit to complete this as one interactive transaction."
            )
            return
        if not session.standalone_supported:
            raise SystemExit(
                f"{service.name} does not offer a standalone browser authorization path"
            )
        if args.open_browser:
            webbrowser.open(session.registration_url)
            print("Waiting for the provider to confirm browser authorization…")
        else:
            print(f"Open this URL and finish signing in:\n{session.registration_url}")
            input(
                "After the provider confirms authorization, press Enter to continue… "
            )
        result = commit_link(player.host, service, session)

    if args.nickname:
        set_nickname(player.host, result.account_udn, args.nickname)
        result = AddedAccount(
            result.service_id, result.service_name, result.account_udn, args.nickname
        )
    print(json.dumps(asdict(result), indent=2))


if __name__ == "__main__":
    main()

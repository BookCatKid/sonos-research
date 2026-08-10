"""Descriptor-driven music-service account onboarding for Sonos households.

The module deliberately separates authorization from the player mutation.  A
provider link can be inspected and opened without changing the household; the
caller must explicitly commit the resulting authorization to SystemProperties.
"""

from __future__ import annotations

import argparse
import json
import secrets
import urllib.error
import urllib.parse
import webbrowser
from dataclasses import asdict, dataclass
from typing import Any

from smapi_browser import (
    Account,
    Service,
    SmapiClient,
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
                # Match the installed desktop's SCLib parameters. Its app
                # interop reports UNKNOWN for native-app installation and only
                # opens HTTP(S), so providers choose their desktop/browser path.
                "hardware": "Windows",
                "osVersion": "Microsoft Windows NT 10.0.19045 64-bit",
                "sonosAppName": "Sonos",
                "callbackPath": callback_path,
            },
            credential_mode="base",
            bearer_token="",
        )
        nodes = descendants(root, "getAppLinkResult")
        session = _link_from_result(
            service, household_id, callback_path, "getAppLink", element_value(nodes[0] if nodes else root)
        )
        if session.standalone_supported or session.app_url or service.auth != "DeviceLink":
            return session
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


def commit_link(host: str, service: Service, session: LinkSession) -> AddedAccount:
    """Commit an authorized provider link to the household's players."""
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
    response = local_soap(
        host,
        SYSTEM_PROPERTIES_PATH,
        SYSTEM_PROPERTIES,
        "AddOAuthAccountX",
        {
            "AccountType": str(expected_account_type),
            "AccountToken": "",
            "AccountKey": "",
            "OAuthDeviceID": session.link_device_id,
            "AuthorizationCode": session.link_code,
            "RedirectURI": session.callback_path,
            "UserIdHashCode": "",
            "AccountTier": "0",
        },
        timeout=35,
    )
    return _parse_add_response(service, response)


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
    parser.add_argument("--commit", action="store_true", help="mutate the household after authorization")
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    parser.add_argument("--nickname", default="")
    args = parser.parse_args()

    player = _selected_player(args.host)
    services = parse_services(player.host)
    service = services.get(args.service_id)
    if not service:
        raise SystemExit(f"Service {args.service_id} is not advertised by the household")

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
        result = commit_link(player.host, service, session)

    if args.nickname:
        set_nickname(player.host, result.account_udn, args.nickname)
        result = AddedAccount(result.service_id, result.service_name, result.account_udn, args.nickname)
    print(json.dumps(asdict(result), indent=2))


if __name__ == "__main__":
    main()

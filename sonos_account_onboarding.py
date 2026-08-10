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
    LocalSoapFault,
    Service,
    SmapiClient,
    descendants,
    element_value,
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
#: Descriptor auth -> player commit action.  "Anonymous" remains mapped so
#: begin_link can still validate/preview it; add_credentials commits an
#: empty-key AddAccountX, which stores a keyless record that stays browsable and
#: is removed again via the empty-key RemoveAccount contract (verified live).
#: Rename stays firmware-rejected for every record (UPnP error 402).
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
    """Add an anonymous or legacy username/password service account.

    Anonymous descriptors are committed with an empty account ID: the player
    rejects any other value for them (UPnP error 402) and stores a keyless
    record.  Such records remain browsable and can be removed again with the
    empty-key RemoveAccount contract (verified live).
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


def set_nickname(host: str, account_udn: str, nickname: str) -> None:
    try:
        local_soap(
            host,
            SYSTEM_PROPERTIES_PATH,
            SYSTEM_PROPERTIES,
            "SetAccountNicknameX",
            {"AccountUDN": account_udn, "AccountNickname": nickname},
        )
    except LocalSoapFault as exc:
        if exc.upnp_code == 402:
            raise OnboardingError(
                f"The player rejected SetAccountNicknameX (UPnP error 402: invalid arguments). "
                "This firmware does not perform local nickname changes; the Sonos apps rename "
                "accounts through their cloud instead. No account state was changed."
            ) from exc
        raise


def remove_account(host: str, service: Service, account_udn: str, *, household_id: str) -> None:
    """Remove one configured account from every player in the household.

    Native contract (FUN_100e60cb0 / FUN_1004abd20): RemoveAccount takes the
    encoded AccountType and the account key as AccountID.  Keyed accounts carry
    the full ``SA_RINCON...`` UDN, which the player resolves for removal
    (confirmed live: the GUI removed a keyed record the mobile app had created).
    Keyless records (empty Username0, truncated UDN) resolve only with an empty
    AccountID -- verified live: ``RemoveAccount(type, "")`` returns 200 and
    removes exactly that service's keyless record, while the truncated UDN is
    rejected with UPnP error 806.
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

    Verified against a live player: EditAccountMd resolves the account only when
    AccountID is the key tail after the encoded-type prefix (``X_#Svc...-Token``,
    stored as Username0); the full ``SA_RINCON...`` UDN is rejected with UPnP
    error 806.  For legacy credential accounts the key is assumed to be the
    username AddAccountX committed (no live legacy account was available to
    confirm the exact UDN shape); if the prefix does not match, the UDN is passed
    through unchanged as a fallback.
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

    Native contract (FUN_100e61520 / FUN_100cd34a1): EditAccountMd takes
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
    provider reauthorization; it is distinct from the controller-local
    in-memory refresh performed by ``SmapiClient.refresh_auth_token``.
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
    read-only; it does not change any player state.  Modern player firmware and
    current providers reject the legacy action (observed UPnP error 800 for
    every auth type on firmware 90.0), so the rejection is translated into an
    actionable OnboardingError instead of a raw fault.
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

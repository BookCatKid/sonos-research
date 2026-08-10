#!/usr/bin/env python3
"""Browse configured Sonos music services using the desktop controller's LAN flow."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from decode_third_party_media_servers import (
    CaptureHandler,
    ZGT_EVENT_PATH,
    decrypt_blob,
    discover,
    encode_blob,
    local_ip_for,
    subscribe,
    unsubscribe,
)
SOAP_ENV = "http://schemas.xmlsoap.org/soap/envelope/"
SMAPI_NS = "http://www.sonos.com/Services/1.1"
SYSTEM_PROPERTIES = "urn:schemas-upnp-org:service:SystemProperties:1"
MUSIC_SERVICES = "urn:schemas-upnp-org:service:MusicServices:1"
DESKTOP_USER_AGENT = (
    "Linux UPnP/1.0 Sonos/90.0-77070 "
    "(WDCR:Microsoft Windows NT 10.0.19045 64-bit)"
)

ET.register_namespace("s", SOAP_ENV)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child_text(node: ET.Element, name: str, default: str = "") -> str:
    for child in node:
        if local_name(child.tag) == name:
            return child.text or default
    return default


def descendants(node: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in node.iter() if local_name(child.tag) == name]


def element_value(node: ET.Element) -> Any:
    """Convert a SMAPI result element without discarding nested metadata."""
    if not list(node):
        return node.text or ""
    result: dict[str, Any] = {}
    for child in node:
        name = local_name(child.tag)
        value = element_value(child)
        if name in result:
            if not isinstance(result[name], list):
                result[name] = [result[name]]
            result[name].append(value)
        else:
            result[name] = value
    return result


def artwork_uri(record: Any) -> str:
    """Resolve the artwork shapes used across content JSON and SMAPI services."""
    if not isinstance(record, dict):
        return ""
    for key in ("album_art_uri", "albumArtURI", "albumArtUri", "imageUrl", "logo"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return (
                value.replace("${width}", "400")
                .replace("${height}", "400")
                .replace("${ratio}", "1x1")
            )
    for key in ("streamMetadata", "trackMetadata", "metadata", "container", "track", "album"):
        value = artwork_uri(record.get(key))
        if value:
            return value
    return ""


def _explicit_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.lower() == "true":
            return True
        if value.lower() == "false":
            return False
    return None


def legacy_item_kind(provider_kind: str, record: dict[str, Any]) -> str:
    """Mirror the desktop's canPush distinction for legacy SMAPI records."""
    if provider_kind != "mediaCollection":
        return provider_kind
    object_id = str(record.get("id", "")).lower()
    # These provider records are controller actions, not browse containers.
    if object_id.startswith(("upsell-banner/", "refmarketplace:")):
        return "mediaMetadata"
    can_enumerate = _explicit_bool(record.get("canEnumerate"))
    if can_enumerate is True:
        return provider_kind
    if can_enumerate is False:
        return "mediaMetadata"
    item_type = str(record.get("itemType", "")).lower()
    if item_type in {"program", "stream", "track"}:
        return "mediaMetadata"
    if _explicit_bool(record.get("canPlay")) is True and item_type not in {
        "album",
        "albumlist",
        "collection",
        "container",
        "playlist",
    }:
        return "mediaMetadata"
    return provider_kind


SENSITIVE_DIAGNOSTIC_FIELDS = {
    "authorization",
    "authtoken",
    "key",
    "password",
    "privatekey",
    "token",
}


def redact_diagnostic(value: Any) -> Any:
    """Keep fault structure useful without printing reusable credentials."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for name, child in value.items():
            if name.lower() in SENSITIVE_DIAGNOSTIC_FIELDS:
                length = len(child) if isinstance(child, str) else None
                redacted[name] = {"redacted": True, "length": length}
            else:
                redacted[name] = redact_diagnostic(child)
        return redacted
    if isinstance(value, list):
        return [redact_diagnostic(child) for child in value]
    return value


def diagnostic_headers(headers: dict[str, str] | None) -> dict[str, str] | None:
    if not headers:
        return None
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in {"authorization", "proxy-authorization", "set-cookie", "cookie"}
    }


#: Human-readable hints for the numeric UPnP error codes the player embeds in
#: SystemProperties fault details (``<UPnPError><errorCode>``).  Codes 800 and
#: 806 were observed against a live player (legacy-action rejection and unknown
#: account respectively); the rest are the standard UPnP Device Architecture
#: codes.  Codes not listed here fall back to the player-supplied
#: errorDescription.
UPNP_ERROR_TEXT = {
    402: "invalid arguments",
    501: "action failed",
    701: "no such object",
    702: "invalid arguments",
    714: "illegal value for argument",
    800: "action not supported for this service/account on this player",
    806: "account could not be resolved",
}


@dataclass
class LocalSoapFault(RuntimeError):
    action: str
    http_status: int
    code: str = ""
    message: str = ""
    detail: Any = None
    upnp_code: int | None = None
    upnp_description: str = ""

    def __str__(self) -> str:
        suffix = f": {self.code} {self.message}".rstrip() if self.code or self.message else ""
        if self.upnp_code is not None:
            meaning = (
                self.upnp_description
                or UPNP_ERROR_TEXT.get(self.upnp_code, "")
                or "unspecified UPnP error"
            )
            suffix += f" (UPnP error {self.upnp_code}: {meaning})"
        return f"Local {self.action} failed with HTTP {self.http_status}{suffix}"


def _upnp_fault_fields(root: ET.Element) -> tuple[int | None, str]:
    """Extract the numeric UPnP errorCode/errorDescription the player embeds in faults.

    The generic faultstring ("UPnPError") hides the actual failure; the meaningful
    value lives in ``<detail><UPnPError><errorCode>`` on every player fault we have
    observed.  A missing/unnumbered detail yields (None, "").
    """
    for upnp_error in descendants(root, "UPnPError"):
        code_text = child_text(upnp_error, "errorCode").strip()
        if code_text.isdigit():
            return int(code_text), child_text(upnp_error, "errorDescription").strip()
    return None, ""


def local_soap(
    host: str,
    path: str,
    service_type: str,
    action: str,
    fields: dict[str, str],
    *,
    timeout: float = 10,
) -> bytes:
    envelope = ET.Element(f"{{{SOAP_ENV}}}Envelope")
    body = ET.SubElement(envelope, f"{{{SOAP_ENV}}}Body")
    operation = ET.SubElement(body, f"{{{service_type}}}{action}")
    for name, value in fields.items():
        ET.SubElement(operation, name).text = value
    payload = ET.tostring(envelope, encoding="utf-8", xml_declaration=True)
    connection = http.client.HTTPConnection(host, 1400, timeout=timeout)
    connection.request(
        "POST",
        path,
        body=payload,
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"{service_type}#{action}"',
        },
    )
    response = connection.getresponse()
    result = response.read()
    status = response.status
    connection.close()
    if status != 200:
        try:
            root = ET.fromstring(result)
        except ET.ParseError:
            preview = result.decode("utf-8", errors="replace").strip()
            raise LocalSoapFault(action, status, message=preview[:200]) from None
        fault_nodes = descendants(root, "Fault")
        fault = fault_nodes[0] if fault_nodes else root
        detail_nodes = descendants(fault, "detail")
        upnp_code, upnp_description = _upnp_fault_fields(fault)
        raise LocalSoapFault(
            action,
            status,
            child_text(fault, "faultcode") or child_text(root, "errorCode"),
            child_text(fault, "faultstring") or child_text(root, "errorDescription"),
            element_value(detail_nodes[0]) if detail_nodes else element_value(fault),
            upnp_code=upnp_code,
            upnp_description=upnp_description,
        )
    return result


def player_device_id(host: str) -> str:
    response = local_soap(
        host,
        "/SystemProperties/Control",
        SYSTEM_PROPERTIES,
        "GetString",
        {"VariableName": "R_TrialZPSerial"},
    )
    root = ET.fromstring(response)
    values = descendants(root, "StringValue")
    if not values or not (values[0].text or "").strip():
        raise RuntimeError("Player did not return R_TrialZPSerial")
    return (values[0].text or "").strip()


def player_zone_id(host: str) -> str:
    with urllib.request.urlopen(f"http://{host}:1400/xml/device_description.xml", timeout=10) as response:
        root = ET.fromstring(response.read())
    values = descendants(root, "UDN")
    if not values or not (values[0].text or "").strip():
        raise RuntimeError("Player description did not contain a UDN")
    return (values[0].text or "").strip().removeprefix("uuid:")


def host_device_id(host: str) -> str:
    """Return the persistent controller identity used by Sonos cloud services.

    This is deliberately distinct from ``R_TrialZPSerial``: the latter names a
    reachable player, while the desktop controller sends its own stable host
    identity in SMAPI credentials and modern content headers. HA deployments
    should persist this value and may provide it through ``SONOS_HOST_DEVICE_ID``.
    """
    configured = os.environ.get("SONOS_HOST_DEVICE_ID", "").strip()
    if configured:
        return configured
    # Keep the discovery fallback for callers that have not configured a
    # persistent host identity yet. Modern content services should set
    # SONOS_HOST_DEVICE_ID to the app's MachineIdentifier; legacy SMAPI calls
    # use player_device_id() directly instead.
    return player_device_id(host)


def local_time_zone() -> str:
    configured = os.environ.get("TZ", "").strip()
    if configured:
        return configured
    resolved = os.path.realpath("/etc/localtime")
    marker = "/zoneinfo/"
    if marker in resolved:
        return resolved.split(marker, 1)[1]
    return "UTC"


def capture_account_payload(host: str, household_id: str, port: int = 0, timeout: int = 8) -> bytes:
    CaptureHandler.captured = queue.Queue(maxsize=1)
    server = ThreadingHTTPServer(("0.0.0.0", port), CaptureHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    sid = ""
    try:
        callback = f"http://{local_ip_for(host)}:{server.server_port}{ZGT_EVENT_PATH}"
        sid = subscribe(host, callback, timeout + 10)
        encoded = CaptureHandler.captured.get(timeout=timeout)
        return decrypt_blob(encoded, household_id)
    finally:
        if sid:
            try:
                unsubscribe(host, sid)
            except Exception:
                pass
        server.shutdown()
        server.server_close()


@dataclass(frozen=True)
class Account:
    service_id: int
    serial: int
    udn: str
    username: str = ""
    password: str = ""
    token: str = ""
    key: str = ""
    nickname: str = ""
    tier: str = ""
    schema_revision: int = 7

    @property
    def account_uid(self) -> int:
        # The desktop app constructs the token identity with this field using
        # X_#Svc<AccountType>-<AccountUID in hex>-Token. SerialNum0 is only
        # the controller-facing account selector, not the UPnP account UID.
        match = re.search(r"X_#Svc\d+-([0-9a-fA-F]+)-Token$", self.udn)
        if not match:
            raise RuntimeError(f"Account UDN does not contain a numeric AccountUID: {self.udn}")
        return int(match.group(1), 16)

    @property
    def keyless(self) -> bool:
        """True for empty-key records (no username, token, or key).

        Such records are stored by the player with an unmanaged
        ``SA_RINCON<type>_`` UDN: RemoveAccount and SetAccountNicknameX reject
        them (observed UPnP errors 806 and 402), so management actions should be
        withheld for them.
        """
        return not self.username and not self.token and not self.key


def account_content_device_id(household_id: str, account: Account) -> str:
    """Build the per-account device identity used by native content sessions."""
    return f"{household_id}_{account.account_uid:08x}"


@dataclass(frozen=True)
class Service:
    service_id: int
    name: str
    uri: str
    auth: str
    capabilities: int
    policy: dict[str, str]
    manifest_uri: str = ""


def parse_accounts(payload: bytes) -> list[Account]:
    root = ET.fromstring(payload.decode("utf-8"))
    accounts: list[Account] = []
    for node in root:
        attrs = node.attrib
        udn = attrs.get("UDN", "")
        match = re.match(r"^SA_RINCON(\d+)", udn)
        if not match:
            continue
        encoded_type = int(match.group(1))
        accounts.append(
            Account(
                service_id=encoded_type // 256,
                serial=int(attrs.get("SerialNum0", "0") or 0),
                udn=udn,
                username=attrs.get("Username0", ""),
                password=attrs.get("Password0", ""),
                token=attrs.get("Token0", ""),
                key=attrs.get("Key0", ""),
                nickname=attrs.get("Nickname0", ""),
                tier=attrs.get("Tier0", ""),
                schema_revision=encoded_type % 256,
            )
        )
    return accounts


def parse_services(host: str) -> dict[int, Service]:
    response = local_soap(host, "/MusicServices/Control", MUSIC_SERVICES, "ListAvailableServices", {})
    outer = ET.fromstring(response)
    descriptors = descendants(outer, "AvailableServiceDescriptorList")
    if not descriptors or not (descriptors[0].text or "").strip():
        raise RuntimeError("Player returned no music-service descriptors")
    catalog = ET.fromstring(descriptors[0].text or "")
    services: dict[int, Service] = {}
    for node in catalog:
        if local_name(node.tag) != "Service":
            continue
        raw = node.attrib
        service_id = int(raw.get("Id", "0") or 0)
        policy_nodes = [child for child in node if local_name(child.tag) == "Policy"]
        policy = dict(policy_nodes[0].attrib) if policy_nodes else {}
        manifest_nodes = [child for child in node if local_name(child.tag) == "Manifest"]
        auth = raw.get("Auth", policy.get("Auth", "Anonymous"))
        services[service_id] = Service(
            service_id=service_id,
            name=raw.get("Name", str(service_id)),
            uri=raw.get("SecureUri") or raw.get("Uri", ""),
            auth=auth,
            capabilities=int(raw.get("Capabilities", "0") or 0),
            policy=policy,
            manifest_uri=manifest_nodes[0].attrib.get("Uri", "") if manifest_nodes else "",
        )
    return services


@dataclass
class SmapiFault(RuntimeError):
    code: str
    message: str
    http_status: int
    detail: Any = None
    response_headers: dict[str, str] | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message} (HTTP {self.http_status})"


class SmapiClient:
    def __init__(
        self,
        service: Service,
        account: Account,
        household_id: str,
        device_id: str,
        zone_player_id: str,
        player_host: str,
        *,
        host_device_id: str | None = None,
        controller_id: str | None = None,
        time_zone: str | None = None,
        explicit_content: bool = False,
        allow_credential_refresh: bool = False,
    ) -> None:
        self.service = service
        self.account = account
        self.household_id = household_id
        self.device_id = device_id
        self.host_device_id = host_device_id or device_id
        self.zone_player_id = zone_player_id
        self.player_host = player_host
        self.controller_id = controller_id or str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"sonos-smapi-browser:{household_id}:{device_id}")
        )
        self.time_zone = time_zone or local_time_zone()
        self.explicit_content = explicit_content
        self.allow_credential_refresh = allow_credential_refresh
        self.session_id = ""

    def _credentials(
        self,
        parent: ET.Element,
        account: Account | None = None,
        *,
        mode: str = "normal",
    ) -> ET.Element:
        current = account or self.account
        credentials = ET.SubElement(parent, f"{{{SMAPI_NS}}}credentials")
        if self.service.capabilities & (1 << 18) and self.zone_player_id:
            ET.SubElement(credentials, f"{{{SMAPI_NS}}}zonePlayerId").text = self.zone_player_id
        ET.SubElement(credentials, f"{{{SMAPI_NS}}}deviceId").text = self.device_id
        ET.SubElement(credentials, f"{{{SMAPI_NS}}}deviceProvider").text = "Sonos"
        if mode == "base" or self.service.auth == "Anonymous":
            return credentials
        if mode == "normal" and self.service.auth in {"UserId", "UserIdPassword"}:
            login = ET.SubElement(credentials, f"{{{SMAPI_NS}}}login")
            ET.SubElement(login, f"{{{SMAPI_NS}}}username").text = current.username
            ET.SubElement(login, f"{{{SMAPI_NS}}}password").text = current.password
            return credentials
        # Auth=DeviceLink describes how an account is provisioned. Once Sonos
        # has stored Token0/Key0, the desktop controller browses with that
        # linked-account pair just like AppLink. getSessionId is only the
        # legacy/no-token fallback.
        if mode == "normal" and current.token:
            login_token = ET.SubElement(credentials, f"{{{SMAPI_NS}}}loginToken")
            if not (self.service.capabilities & 8):
                ET.SubElement(login_token, f"{{{SMAPI_NS}}}token").text = current.token
                if current.key:
                    ET.SubElement(login_token, f"{{{SMAPI_NS}}}key").text = current.key
            ET.SubElement(login_token, f"{{{SMAPI_NS}}}householdId").text = self.household_id
            return credentials
        if mode == "normal" and self.service.auth == "DeviceLink":
            if self.session_id:
                ET.SubElement(credentials, f"{{{SMAPI_NS}}}sessionId").text = self.session_id
            return credentials
        if current.token or self.household_id:
            login_token = ET.SubElement(credentials, f"{{{SMAPI_NS}}}loginToken")
            # Capability bit 3 switches the token from the SOAP loginToken into
            # an HTTP Bearer header for normal requests. The desktop's special
            # refreshAuthToken builder puts the old token/key back into the
            # SOAP loginToken even when that bit is set.
            if not (self.service.capabilities & 8) or mode == "refresh":
                if current.token:
                    ET.SubElement(login_token, f"{{{SMAPI_NS}}}token").text = current.token
                if current.key:
                    ET.SubElement(login_token, f"{{{SMAPI_NS}}}key").text = current.key
            ET.SubElement(login_token, f"{{{SMAPI_NS}}}householdId").text = self.household_id
        return credentials

    def _envelope(
        self,
        action: str,
        fields: dict[str, str],
        account: Account | None = None,
        *,
        credential_mode: str = "normal",
    ) -> bytes:
        envelope = ET.Element(f"{{{SOAP_ENV}}}Envelope")
        header = ET.SubElement(envelope, f"{{{SOAP_ENV}}}Header")
        self._credentials(header, account, mode=credential_mode)
        # The desktop app keys context inclusion from bit 16 of Capabilities.
        if self.service.capabilities & (1 << 16):
            context = ET.SubElement(header, f"{{{SMAPI_NS}}}context")
            ET.SubElement(context, f"{{{SMAPI_NS}}}timeZone").text = self.time_zone
            if self.service.capabilities & (1 << 21) and self.explicit_content:
                filtering = ET.SubElement(context, f"{{{SMAPI_NS}}}contentFiltering")
                ET.SubElement(filtering, f"{{{SMAPI_NS}}}explicit").text = "true"
        body = ET.SubElement(envelope, f"{{{SOAP_ENV}}}Body")
        operation = ET.SubElement(body, f"{{{SMAPI_NS}}}{action}")
        for name, value in fields.items():
            ET.SubElement(operation, f"{{{SMAPI_NS}}}{name}").text = str(value)
        return ET.tostring(envelope, encoding="utf-8", xml_declaration=True)

    def _request(
        self,
        action: str,
        fields: dict[str, str],
        account: Account | None = None,
        *,
        credential_mode: str = "normal",
        bearer_token: str | None = None,
    ) -> ET.Element:
        if urllib.parse.urlparse(self.service.uri).scheme.lower() != "https":
            raise RuntimeError(f"SMAPI endpoint must use HTTPS: {self.service.uri}")
        payload = self._envelope(action, fields, account, credential_mode=credential_mode)
        headers = {
            "Content-Type": 'text/xml; charset="utf-8"',
            "Soapaction": f'"{SMAPI_NS}#{action}"',
            "Accept-Language": "en-US",
            "X-Sonos-Controller-ID": self.controller_id,
            "User-Agent": DESKTOP_USER_AGENT,
        }
        current = account or self.account
        # The native refreshAuthToken constructor explicitly passes a null
        # bearer value. Other SMAPI operations pass the active account token
        # when capability bit 3 requests HTTP bearer authentication.
        active_bearer = current.token if bearer_token is None else bearer_token
        if credential_mode != "refresh" and self.service.capabilities & 8 and active_bearer:
            headers["Authorization"] = f"Bearer {active_bearer}"
        request = urllib.request.Request(
            self.service.uri,
            data=payload,
            method="POST",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                status = response.status
                result = response.read()
                response_headers = dict(getattr(response, "headers", {}).items())
        except urllib.error.HTTPError as error:
            status = error.code
            result = error.read()
            response_headers = dict(error.headers.items()) if error.headers else {}
            error.close()
        try:
            root = ET.fromstring(result)
        except ET.ParseError as error:
            # Sonos Radio currently returns xsi:nil without declaring the xsi
            # prefix. The desktop parser tolerates it, so repair that narrowly
            # defined provider defect before treating the response as corrupt.
            repaired = result
            if b"xsi:" in result and b"xmlns:xsi" not in result:
                repaired = re.sub(
                    br"(<(?:[A-Za-z_][\w.-]*:)?Envelope)(\s)",
                    br'\1 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\2',
                    result,
                    count=1,
                )
            if repaired != result:
                try:
                    root = ET.fromstring(repaired)
                except ET.ParseError:
                    pass
                else:
                    result = repaired
                    error = None  # type: ignore[assignment]
            if error is None:
                pass
            else:
                preview = result.decode("utf-8", errors="replace").strip()
                if status != 200:
                    message = preview if len(preview) <= 200 else f"Non-XML response ({len(result)} bytes)"
                    raise SmapiFault(
                        "HTTP",
                        message or f"Unexpected status {status}",
                        status,
                        response_headers=response_headers,
                    ) from error
                raise RuntimeError(f"SMAPI returned malformed XML ({len(result)} bytes): {error}") from error
        faults = descendants(root, "Fault")
        if faults:
            fault = faults[0]
            details = descendants(fault, "detail")
            raise SmapiFault(
                child_text(fault, "faultcode", "SMAPI.Fault"),
                child_text(fault, "faultstring", "Unknown SMAPI fault"),
                status,
                element_value(details[0]) if details else None,
                response_headers,
            )
        if status != 200:
            raise SmapiFault(
                "HTTP",
                f"Unexpected status {status}",
                status,
                response_headers=response_headers,
            )
        return root

    @staticmethod
    def _replacement_credentials(detail: Any) -> tuple[str, str] | None:
        if isinstance(detail, dict):
            token = detail.get("authToken")
            key = detail.get("privateKey")
            if isinstance(token, str) and isinstance(key, str) and token and key:
                return token, key
            for child in detail.values():
                replacement = SmapiClient._replacement_credentials(child)
                if replacement:
                    return replacement
        elif isinstance(detail, list):
            for child in detail:
                replacement = SmapiClient._replacement_credentials(child)
                if replacement:
                    return replacement
        return None

    def _accept_replacement(self, token: str, key: str) -> Account:
        if not token or not key:
            raise RuntimeError("Cannot accept an incomplete replacement credential pair")
        # The desktop app's active household adapter updates only its in-memory
        # account model here. It does not invoke RefreshAccountCredentialsX on
        # the player; each controller can refresh the stored seed independently.
        self.account = replace(self.account, token=token, key=key)
        self.session_id = ""
        return self.account

    def refresh_auth_token(self) -> Account:
        if self.account.token == "needs_reauth":
            raise SmapiFault(
                "Client.NeedsReauthorization",
                "The Sonos household stores needs_reauth instead of a refreshable token",
                0,
            )
        root = self._request(
            "refreshAuthToken",
            {},
            credential_mode="refresh",
            bearer_token="",
        )
        results = descendants(root, "refreshAuthTokenResult")
        result = results[0] if results else root
        token = child_text(result, "authToken")
        key = child_text(result, "privateKey")
        if not token or not key:
            raise RuntimeError("refreshAuthToken response did not contain both authToken and privateKey")
        return self._accept_replacement(token, key)

    def _refresh_from_fault(self, fault: SmapiFault) -> Account:
        # For services without capability bit 3, the desktop consumes the
        # refreshAuthTokenResult embedded in the original browse fault. Bit 3
        # services perform the explicit refreshAuthToken operation instead.
        if self.service.capabilities & 8:
            return self.refresh_auth_token()
        replacement = self._replacement_credentials(fault.detail)
        if replacement:
            return self._accept_replacement(*replacement)
        # Some current providers advertise the embedded-replacement branch but
        # return a plain Token Expired fault. Their explicit refresh operation
        # is still available; Sonos Radio is a live example.
        return self.refresh_auth_token()

    @staticmethod
    def _is_expired_fault(fault: SmapiFault) -> bool:
        combined = f"{fault.code} {fault.message}".lower()

        return (
            "authtokenexpired" in combined
            or "invalidtoken" in combined
            or "tokenrefreshrequired" in combined
            or "token expired" in combined
            or fault.http_status == 401
        )

    @staticmethod
    def _is_invalid_session_fault(fault: SmapiFault) -> bool:
        combined = f"{fault.code} {fault.message}".lower()
        return "invalidsession" in combined or "invalid session" in combined

    @staticmethod
    def _is_transient_fault(fault: SmapiFault) -> bool:
        combined = f"{fault.code} {fault.message}".lower()
        provider_detail = json.dumps(fault.detail, sort_keys=True).lower() if fault.detail is not None else ""
        # Apple intermittently returns the generic SMAPI SonosError 999 for a
        # valid collection and succeeds immediately on the identical request.
        provider_retry = '"sonoserror": "999"' in provider_detail
        return provider_retry or fault.http_status in {408, 429, 502, 503, 504} or any(
            marker in combined
            for marker in ("read timed out", "timed out reading", "temporarily unavailable", "try again")
        )

    def get_session_id(self) -> str:
        """Obtain and cache the DeviceLink session used in browse credentials."""
        if self.service.auth != "DeviceLink":
            raise RuntimeError("getSessionId is only valid for a DeviceLink service")
        try:
            root = self._request(
                "getSessionId",
                {"username": self.account.username, "password": self.account.password},
                credential_mode="base",
            )
        except SmapiFault as fault:
            if not self._is_expired_fault(fault) or not self.allow_credential_refresh:
                raise
            self._refresh_from_fault(fault)
            root = self._request(
                "getSessionId",
                {"username": self.account.username, "password": self.account.password},
                credential_mode="base",
            )
        results = descendants(root, "getSessionIdResult")
        session_id = (results[0].text or "").strip() if results else ""
        if not session_id:
            raise RuntimeError("getSessionId response did not contain a session ID")
        self.session_id = session_id
        return session_id

    def _ensure_session(self) -> None:
        if self.service.auth == "DeviceLink" and not self.account.token and not self.session_id:
            self.get_session_id()

    def _request_with_refresh(self, action: str, fields: dict[str, str]) -> ET.Element:
        if self.account.token == "needs_reauth":
            raise SmapiFault(
                "Client.NeedsReauthorization",
                "The Sonos household stores needs_reauth instead of a usable token",
                0,
            )
        self._ensure_session()
        try:
            return self._request(action, fields)
        except SmapiFault as fault:
            if self.service.auth == "DeviceLink" and self._is_invalid_session_fault(fault):
                self.session_id = ""
                self._ensure_session()
                return self._request(action, fields)
            if self.service.auth == "Anonymous" or not self._is_expired_fault(fault):
                raise
            if not self.allow_credential_refresh:
                raise
            self._refresh_from_fault(fault)
            self._ensure_session()
            return self._request(action, fields)

    def get_metadata(
        self,
        object_id: str = "root",
        index: int = 0,
        count: int = 100,
        recursive: bool = False,
    ) -> dict[str, Any]:
        fields = {"id": object_id, "index": str(index), "count": str(count)}
        if recursive:
            fields["recursive"] = "true"
        for attempt in range(3):
            try:
                root = self._request_with_refresh("getMetadata", fields)
                break
            except SmapiFault as fault:
                if attempt == 2 or not self._is_transient_fault(fault):
                    raise
            except (TimeoutError, urllib.error.URLError):
                if attempt == 2:
                    raise
        else:  # pragma: no cover - both retry exits are explicit
            raise RuntimeError("getMetadata retry exhausted")
        results = descendants(root, "getMetadataResult")
        result = results[0] if results else root
        items: list[dict[str, Any]] = []
        for node in result:
            kind = local_name(node.tag)
            if kind not in {"mediaCollection", "mediaMetadata"}:
                continue
            record = element_value(node)
            assert isinstance(record, dict)
            record["provider_kind"] = kind
            record["kind"] = legacy_item_kind(kind, record)
            # Modern content JSON and legacy SMAPI use different spellings for
            # the same artwork field. Keep the provider response intact while
            # exposing one key to the GUI at every browse depth.
            record["album_art_uri"] = artwork_uri(record)
            items.append(record)
        return {
            "index": int(child_text(result, "index", str(index))),
            "count": int(child_text(result, "count", str(len(items)))),
            "total": int(child_text(result, "total", str(len(items)))),
            "items": items,
        }

    def get_media_metadata(self, object_id: str) -> dict[str, Any]:
        root = self._request_with_refresh("getMediaMetadata", {"id": object_id})
        results = descendants(root, "getMediaMetadataResult")
        if not results:
            raise RuntimeError("getMediaMetadata response did not contain a result")
        value = element_value(results[0])
        if isinstance(value, dict):
            value["album_art_uri"] = artwork_uri(value)
            return value
        return {"value": value}

    def search(self, category_id: str, term: str, index: int = 0, count: int = 100) -> dict[str, Any]:
        count = min(count, max(0, 1000 - index))
        root = self._request_with_refresh(
            "search",
            {"id": category_id, "term": term, "index": str(index), "count": str(count)},
        )
        results = descendants(root, "searchResult")
        result = results[0] if results else root
        items: list[dict[str, Any]] = []
        for node in result:
            kind = local_name(node.tag)
            if kind not in {"mediaCollection", "mediaMetadata"}:
                continue
            record = element_value(node)
            assert isinstance(record, dict)
            record["kind"] = kind
            items.append(record)
        return {
            "count": int(child_text(result, "count", str(len(items)))),
            "total": min(1000, int(child_text(result, "total", str(len(items))))),
            "items": items,
        }


def service_search_categories(service: Service) -> list[dict[str, Any]]:
    """Follow the descriptor manifest into the presentation map used by the app."""
    if not service.manifest_uri:
        return []
    with urllib.request.urlopen(service.manifest_uri, timeout=20) as response:
        manifest = json.loads(response.read())
    presentation_uri = manifest.get("presentationMap", {}).get("uri", "")
    if not presentation_uri:
        return []
    with urllib.request.urlopen(presentation_uri, timeout=20) as response:
        presentation = ET.fromstring(response.read())
    groups: list[dict[str, Any]] = []
    for mapping in presentation.iter():
        if local_name(mapping.tag) != "PresentationMap" or mapping.attrib.get("type") != "Search":
            continue
        for categories in mapping:
            if local_name(categories.tag) != "Match":
                continue
            for group in categories:
                if local_name(group.tag) != "SearchCategories":
                    continue
                entries = []
                for category in group:
                    if local_name(category.tag) not in {"Category", "CustomCategory"}:
                        continue
                    entries.append(
                        {
                            "kind": local_name(category.tag),
                            "id": category.attrib.get("id", ""),
                            "mapped_id": category.attrib.get("mappedId", ""),
                            "string_id": category.attrib.get("stringId", ""),
                        }
                    )
                groups.append({"string_id": group.attrib.get("stringId", ""), "categories": entries})
    return groups



class ContentBrowseFault(RuntimeError):
    """A manifest-driven content browse request failed or returned an unexpected page."""

    def __init__(self, service: Service, message: str, http_status: int = 0) -> None:
        self.service = service
        self.http_status = http_status
        super().__init__(f"{service.name}: {message}")


def service_manifest(service: Service) -> dict[str, Any]:
    """Fetch and decode the manifest advertised by ListAvailableServices."""
    if not service.manifest_uri:
        return {}
    request = urllib.request.Request(
        service.manifest_uri,
        headers={"Accept": "application/json", "Accept-Language": "en-US"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        error.close()
        raise ContentBrowseFault(service, f"manifest HTTP {error.code}", error.code) from error
    try:
        manifest = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as error:
        raise ContentBrowseFault(service, "manifest was not valid JSON") from error
    if not isinstance(manifest, dict):
        raise ContentBrowseFault(service, "manifest root was not an object")
    return manifest


def service_content_endpoint(service: Service, endpoint_type: str = "browse") -> str:
    manifest = service_manifest(service)
    endpoints = manifest.get("endpoints", [])
    if isinstance(endpoints, list):
        for endpoint in endpoints:
            if isinstance(endpoint, dict) and endpoint.get("type") == endpoint_type:
                uri = endpoint.get("uri")
                if isinstance(uri, str) and uri:
                    return uri
    raise ContentBrowseFault(service, f"manifest has no {endpoint_type} endpoint")


def content_auth_header(service: Service, account: Account) -> str:
    """Build the aggregate-search X-Sonos-SMAPI-Auth envelope without logging it."""
    value = {
        "type": "TOKEN" if account.token else "SESSION",
        "value": account.token,
        "serviceId": str(service.service_id),
        "accountId": account.udn,
    }
    return json.dumps({"accounts": [value]}, separators=(",", ":"))


def content_browse_headers(
    service: Service,
    account: Account,
    device_id: str,
    *,
    controller_id: str | None = None,
    correlation_id: str | None = None,
    time_zone: str | None = None,
    explicit_content: bool = False,
    group_capability: str | None = None,
) -> dict[str, str]:
    """Build the headers used by the desktop SCContentSessionBrowse path.

    Provider-owned browse endpoints do not use the aggregate-search
    ``X-Sonos-SMAPI-Auth`` envelope.  AppLink accounts are sent as HTTPS Bearer
    credentials, matching the desktop core's FUN_100247e60 header builder.
    """
    headers = {
        "Accept-Language": "en-US",
        "X-Sonos-Device-Id": device_id,
        "X-Sonos-Corr-Id": correlation_id or str(uuid.uuid4()),
        "User-Agent": DESKTOP_USER_AGENT,
        "Connection": "keep-alive",
    }
    if controller_id:
        headers["X-Sonos-Controller-ID"] = controller_id
    if account.token:
        headers["Authorization"] = f"Bearer {account.token}"
    if group_capability:
        headers["X-Sonos-GroupCapability"] = group_capability
    if service.capabilities & (1 << 16) and time_zone:
        headers["X-Sonos-Context-TimeZone"] = time_zone
    if service.capabilities & (1 << 21) and explicit_content:
        headers["X-Sonos-Context-ContentFiltering"] = "explicit"
    return headers


def content_browse(
    service: Service,
    account: Account,
    device_id: str,
    object_id: str = "root",
    *,
    time_zone: str | None = None,
    explicit_content: bool = False,
    refresh_client: SmapiClient | None = None,
    group_capability: str | None = None,
    controller_id: str | None = None,
) -> dict[str, Any]:
    """Browse a manifest endpoint using the desktop core's authenticated REST transport.

    Modern services return their root page (including multiple views) from the
    manifest URI. Child-page routing is provider-owned and is not inferred here;
    callers must use legacy SMAPI for children until that provider's contract has
    been established from controller code or a captured request.
    """
    if object_id not in {"", "root"}:
        raise ContentBrowseFault(
            service,
            f"child-page routing is not established for object {object_id!r}",
        )
    endpoint = service_content_endpoint(service, "browse")
    current = account
    for attempt in range(2):
        request = urllib.request.Request(
            endpoint,
            headers=content_browse_headers(
                service,
                current,
                device_id,
                controller_id=controller_id,
                time_zone=time_zone,
                explicit_content=explicit_content,
                group_capability=group_capability,
            ),
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                status = response.status
                payload = response.read()
        except urllib.error.HTTPError as error:
            payload = error.read()
            error.close()
            if error.code == 401 and attempt == 0 and refresh_client is not None:
                # SCContentSessionBrowse treats HTTP 401 specially: it runs the
                # account's explicit refreshAuthToken operation and retries the
                # original provider URL once.
                current = refresh_client.refresh_auth_token()
                continue
            raise ContentBrowseFault(service, f"browse HTTP {error.code}", error.code) from error
        break
    else:  # pragma: no cover - both loop exits are explicit above
        raise ContentBrowseFault(service, "browse retry exhausted")
    if status != 200:
        raise ContentBrowseFault(service, f"browse HTTP {status}", status)
    try:
        page = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as error:
        raise ContentBrowseFault(service, "browse response was not valid JSON", status) from error
    if not isinstance(page, dict):
        raise ContentBrowseFault(service, "browse response root was not an object", status)
    return page


def desktop_content_object_id(service: Service, object_id: str) -> str:
    """Return the provider ID passed by the desktop's SMAPI browse delegate.

    Eight-hex prefixes seen at the controller's browse/SCUri boundary are local
    controller identifiers.  The native delegate removes that routing layer
    before calling a provider.  The provider therefore receives the objectId
    from the content response unchanged.
    """
    del service
    return object_id


def _content_item_row(item: dict[str, Any], section: str = "") -> dict[str, Any] | None:
    identity = item.get("id", {})
    content = item.get("content", {})
    if not isinstance(identity, dict) or not isinstance(content, dict):
        return None
    object_id = identity.get("objectId", "")
    if not isinstance(object_id, str) or not object_id:
        return None
    record = content.get("container")
    content_kind = "container"
    if not isinstance(record, dict):
        record = content.get("track")
        content_kind = "track"
    if not isinstance(record, dict):
        return None
    item_type = str(record.get("type", content_kind))
    can_enumerate = record.get("canEnumerate")
    collection_types = {"album", "artist", "container", "playlist", "show"}
    kind = (
        "mediaCollection"
        if content_kind == "container" and (can_enumerate is True or item_type in collection_types)
        else "mediaMetadata"
    )
    artist = record.get("artist", {})
    artist_name = artist.get("name", "") if isinstance(artist, dict) else ""
    return {
        "kind": kind,
        "id": object_id,
        "title": record.get("name", object_id),
        "item_type": item_type,
        "artist": artist_name,
        "summary": record.get("summary", ""),
        "album_art_uri": artwork_uri(record),
        "section": section,
        "display_type": item.get("displayType", ""),
        "source_transport": "content",
    }


def content_page_items(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten modern views for diagnostics while retaining their section labels."""
    rows: list[dict[str, Any]] = []
    views = page.get("views", [])
    if not isinstance(views, list):
        return rows
    for view in views:
        if not isinstance(view, dict):
            continue
        view_content = view.get("content", {})
        view_container = view_content.get("container", {}) if isinstance(view_content, dict) else {}
        section = view_container.get("name", "") if isinstance(view_container, dict) else ""
        items = view.get("items", [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            row = _content_item_row(item, section)
            if row:
                rows.append(row)
    return rows


def content_page_sections(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Represent each desktop content view as one drill-down root row."""
    sections: list[dict[str, Any]] = []
    views = page.get("views", [])
    if not isinstance(views, list):
        return sections
    for view in views:
        if not isinstance(view, dict):
            continue
        identity = view.get("id", {})
        content = view.get("content", {})
        container = content.get("container", {}) if isinstance(content, dict) else {}
        if not isinstance(identity, dict) or not isinstance(container, dict):
            continue
        object_id = identity.get("objectId", "")
        if not isinstance(object_id, str) or not object_id:
            continue
        title = str(container.get("name", object_id))
        raw_items = view.get("items", [])
        if not isinstance(raw_items, list):
            raw_items = []
        items = [
            row
            for item in raw_items if isinstance(item, dict)
            if (row := _content_item_row(item)) is not None
        ]
        sections.append(
            {
                "kind": "mediaCollection",
                "id": object_id,
                "title": title,
                "item_type": "section",
                "album_art_uri": items[0].get("album_art_uri", "") if items else "",
                "section": "",
                "display_type": view.get("displayType", ""),
                "source_transport": "content-section",
                "embedded_total": int(view.get("total", len(items)) or len(items)),
                "_embedded_items": items,
            }
        )
    return sections


class DesktopBrowseSession:
    """The official controller's transport chooser for one service account.

    A service with a manifest browse endpoint gets its home page from that
    authenticated JSON endpoint. Selecting a returned provider object switches
    to ordinary SMAPI getMetadata with the raw objectId. Services without a
    browse endpoint use SMAPI from the root onward.
    """

    def __init__(self, client: SmapiClient, *, content_device_id: str | None = None) -> None:
        self.client = client
        self.content_views: dict[str, dict[str, Any]] = {}
        self.content_endpoint = ""
        if client.service.manifest_uri:
            try:
                self.content_endpoint = service_content_endpoint(client.service, "browse")
            except ContentBrowseFault:
                self.content_endpoint = ""
        self.content_device_id = content_device_id or client.host_device_id
        if self.content_endpoint and content_device_id is None:
            self.content_device_id = account_content_device_id(
                client.household_id,
                client.account,
            )

    @property
    def root_transport(self) -> str:
        return "content" if self.content_endpoint else "smapi"

    def browse(
        self,
        object_id: str = "root",
        index: int = 0,
        count: int = 100,
        *,
        from_content_page: bool = False,
    ) -> dict[str, Any]:
        if object_id in self.content_views:
            return self.content_views[object_id]
        if object_id in {"", "root"} and self.content_endpoint:
            page = content_browse(
                self.client.service,
                self.client.account,
                self.content_device_id,
                time_zone=self.client.time_zone,
                explicit_content=self.client.explicit_content,
                refresh_client=self.client if self.client.allow_credential_refresh else None,
                controller_id=self.client.controller_id,
            )
            sections = content_page_sections(page)
            self.content_views = {}
            for section in sections:
                embedded = section.pop("_embedded_items", [])
                section_id = str(section["id"])
                self.content_views[section_id] = {
                    "index": 0,
                    "count": len(embedded),
                    "total": int(section.get("embedded_total", len(embedded))),
                    "items": embedded,
                    "transport": "content",
                    "endpoint": self.content_endpoint,
                    "requested_id": section_id,
                    "embedded": True,
                }
            return {
                "index": 0,
                "count": len(sections),
                "total": len(sections),
                "items": sections,
                "transport": "content",
                "endpoint": self.content_endpoint,
                "raw_page": page,
            }
        smapi_id = (
            desktop_content_object_id(self.client.service, object_id)
            if from_content_page
            else object_id
        )
        request_client = self.client
        if from_content_page:
            # Content-session objects are handed to SMAPI with the account's
            # OAuth device identity as loginToken.householdId.  Returning to
            # the bare household ID makes Apple accept /browse/v1 but reject
            # every Library child as InvalidTokenException.
            scoped_household_id = account_content_device_id(
                self.client.household_id,
                self.client.account,
            )
            request_client = SmapiClient(
                self.client.service,
                self.client.account,
                scoped_household_id,
                self.client.device_id,
                self.client.zone_player_id,
                self.client.player_host,
                host_device_id=self.client.host_device_id,
                controller_id=self.client.controller_id,
                time_zone=self.client.time_zone,
                explicit_content=self.client.explicit_content,
                allow_credential_refresh=self.client.allow_credential_refresh,
            )
            request_client.session_id = self.client.session_id
        try:
            page = request_client.get_metadata(smapi_id, index, count)
        finally:
            if request_client is not self.client:
                self.client.account = request_client.account
                self.client.session_id = request_client.session_id
        if from_content_page:
            # Preserve the scoped credential choice through every subsequent
            # collection and page beneath the content-session object.
            for item in page.get("items", []):
                if isinstance(item, dict):
                    item["source_transport"] = "content"
        page.update(transport="smapi", requested_id=smapi_id)
        return page

def inventory(host: str, household_id: str) -> tuple[dict[int, Service], list[Account]]:
    payload = capture_account_payload(host, household_id)
    return parse_services(host), parse_accounts(payload)


def account_label(service: Service, account: Account) -> str:
    suffix = f" — {account.nickname}" if account.nickname else ""
    return f"{service.name} [serial {account.serial}]{suffix}"


def interactive_browse(session: DesktopBrowseSession) -> None:
    """Small terminal navigator whose stack mirrors desktop container browsing."""
    page_size = 100
    stack: list[tuple[str, str, int, bool]] = [("root", session.client.service.name, 0, False)]
    while stack:
        object_id, title, page_index, from_content = stack[-1]
        page = session.browse(object_id, page_index, page_size, from_content_page=from_content)
        items = page["items"]
        first = page_index + 1 if items else 0
        last = page_index + len(items)
        print(f"\n{title}  [{object_id}] — {first}-{last} of {page['total']}")
        for index, item in enumerate(items, 1):
            marker = ">" if item.get("kind") == "mediaCollection" else "·"
            print(f"  {index:>3}. {marker} {item.get('title', item.get('id', 'untitled'))}")
        choice = input("number to open, 'n'/'p' page, '..' back, 'q' quit: ").strip()
        if choice.lower() == "q":
            return
        if choice == "..":
            if len(stack) > 1:
                stack.pop()
            continue
        if choice.lower() == "n":
            if page_index + len(items) < page["total"]:
                stack[-1] = (object_id, title, page_index + max(1, len(items)), from_content)
            continue
        if choice.lower() == "p":
            if page_index > 0:
                stack[-1] = (object_id, title, max(0, page_index - page_size), from_content)
            continue
        if not choice.isdigit() or not 1 <= int(choice) <= len(items):
            continue
        selected = items[int(choice) - 1]
        selected_id = str(selected.get("id", ""))
        if selected.get("kind") == "mediaCollection":
            stack.append(
                (
                    selected_id,
                    str(selected.get("title", selected_id)),
                    0,
                    selected.get("source_transport") == "content",
                )
            )
        else:
            try:
                detail = session.client.get_media_metadata(selected_id)
            except SmapiFault as fault:
                combined = f"{fault.code} {fault.message}".lower()
                if "not supported" not in combined and "serviceunknown" not in combined:
                    raise
                detail = {**selected, "detail_status": "getMediaMetadata unsupported by provider"}
            print(json.dumps(detail, indent=2, ensure_ascii=False))


def _crawl_error(error: Exception) -> dict[str, Any]:
    if isinstance(error, SmapiFault):
        result: dict[str, Any] = {
            "type": "SmapiFault",
            "code": error.code,
            "message": error.message,
            "http_status": error.http_status,
        }
        if error.detail is not None:
            result["detail"] = redact_diagnostic(error.detail)
        return result
    return {"type": error.__class__.__name__, "message": str(error)}


def crawl_account_tree(
    session: DesktopBrowseSession,
    *,
    max_depth: int = 8,
    max_nodes: int = 25_000,
    max_collections: int = 1_000,
    max_seconds: float = 120.0,
    page_size: int = 100,
) -> dict[str, Any]:
    """Browse every reachable collection for one account without exposing credentials."""
    seen: dict[str, str] = {}
    errors: list[dict[str, Any]] = []
    stats = {
        "collections_opened": 0,
        "items_seen": 0,
        "pages_requested": 0,
        "references": 0,
        "errors": 0,
        "depth_limit_hits": 0,
        "node_limit_hits": 0,
        "collection_limit_hits": 0,
        "time_limit_hits": 0,
    }
    deadline = time.monotonic() + max_seconds

    def browse_all(object_id: str, from_content: bool) -> dict[str, Any]:
        merged: dict[str, Any] | None = None
        index = 0
        while True:
            page = session.browse(
                object_id,
                index,
                page_size,
                from_content_page=from_content,
            )
            stats["pages_requested"] += 1
            if merged is None:
                merged = {key: value for key, value in page.items() if key not in {"items", "raw_page"}}
                merged["items"] = []
            items = page.get("items", [])
            if not isinstance(items, list):
                items = []
            merged["items"].extend(items)
            # Content-session views contain their currently embedded items and
            # have no provider-defined pagination URL. Network children are
            # SMAPI and use the ordinary index/count loop below.
            if page.get("transport") == "content":
                break
            if stats["items_seen"] + len(merged["items"]) >= max_nodes or time.monotonic() >= deadline:
                merged["truncated"] = True
                break
            total = int(page.get("total", len(merged["items"])) or 0)
            next_index = int(page.get("index", index) or index) + len(items)
            if not items or next_index >= total or next_index <= index:
                break
            index = next_index
        assert merged is not None
        return merged

    root: dict[str, Any] = {"id": "root", "title": session.client.service.name, "depth": 0}
    pending = deque([(root, "root", session.client.service.name, 0, False, session.client.service.name)])
    while pending:
        node, object_id, title, depth, from_content, path = pending.popleft()
        if time.monotonic() >= deadline:
            node["status"] = "time-limit"
            stats["time_limit_hits"] += 1
            for remaining, *_rest in pending:
                remaining["status"] = "time-limit"
                stats["time_limit_hits"] += 1
            break
        if object_id in seen:
            node.update(status="reference", ref=seen[object_id])
            stats["references"] += 1
            continue
        if stats["collections_opened"] >= max_collections:
            node["status"] = "collection-limit"
            stats["collection_limit_hits"] += 1
            continue
        if stats["items_seen"] >= max_nodes:
            node["status"] = "node-limit"
            stats["node_limit_hits"] += 1
            continue
        if depth > max_depth:
            node["status"] = "depth-limit"
            stats["depth_limit_hits"] += 1
            continue

        seen[object_id] = path
        try:
            page = browse_all(object_id, from_content)
        except Exception as error:
            diagnostic = _crawl_error(error)
            node.update(status="error", error=diagnostic)
            errors.append({"path": path, "id": object_id, **diagnostic})
            stats["errors"] += 1
            continue

        stats["collections_opened"] += 1
        node.update(
            status="ok",
            transport=page.get("transport", "smapi"),
            requested_id=page.get("requested_id", object_id),
            total=int(page.get("total", 0) or 0),
        )
        endpoint = page.get("endpoint")
        if endpoint:
            node["endpoint"] = endpoint
        if page.get("truncated"):
            node["truncated"] = True
        children: list[dict[str, Any]] = []
        for item in page.get("items", []):
            if not isinstance(item, dict):
                continue
            stats["items_seen"] += 1
            public_item = {
                key: value
                for key, value in item.items()
                if not key.startswith("_") and key not in {"source_transport"}
            }
            child = redact_diagnostic(public_item)
            assert isinstance(child, dict)
            child_id = str(item.get("id", ""))
            child_title = str(item.get("title", child_id or "untitled"))
            child_path = f"{path} / {child_title}"
            if item.get("kind") == "mediaCollection" and child_id:
                child_node = {"id": child_id, "title": child_title, "depth": depth + 1}
                child["children"] = child_node
                pending.append(
                    (
                        child_node,
                        child_id,
                        child_title,
                        depth + 1,
                        item.get("source_transport") == "content",
                        child_path,
                    )
                )
            children.append(child)
            if stats["items_seen"] >= max_nodes:
                stats["node_limit_hits"] += 1
                break
        node["items"] = children
    return {
        "service_id": session.client.service.service_id,
        "service": session.client.service.name,
        "serial": session.client.account.serial,
        "nickname": session.client.account.nickname,
        "auth": session.client.service.auth,
        "limits": {
            "max_depth": max_depth,
            "max_nodes": max_nodes,
            "max_collections": max_collections,
            "max_seconds": max_seconds,
            "page_size": page_size,
        },
        "stats": stats,
        "errors": errors,
        "tree": root,
    }


def crawl_all_account_trees(
    host: str,
    household_id: str,
    services: dict[int, Service],
    accounts: list[Account],
    *,
    host_device_identity: str | None = None,
    refresh_credentials: bool = True,
    max_depth: int = 8,
    max_nodes: int = 25_000,
    max_collections: int = 1_000,
    max_seconds: float = 120.0,
    page_size: int = 100,
    checkpoint_path: Path | None = None,
) -> dict[str, Any]:
    device_id = player_device_id(host)
    zone_id = player_zone_id(host)
    trees: list[dict[str, Any]] = []

    def report() -> dict[str, Any]:
        return {
            "schema": "sonos-account-music-tree-v1",
            "household_id": household_id,
            "player": host,
            "account_count": len(trees),
            "accounts": trees,
        }

    for account in accounts:
        service = services.get(account.service_id)
        if not service:
            continue
        print(f"Crawling {account_label(service, account)}...", file=sys.stderr, flush=True)
        started = time.monotonic()
        client = SmapiClient(
            service,
            account,
            household_id,
            device_id,
            zone_id,
            host,
            host_device_id=host_device_identity or host_device_id(host),
            allow_credential_refresh=refresh_credentials,
        )
        try:
            trees.append(
                crawl_account_tree(
                    DesktopBrowseSession(client),
                    max_depth=max_depth,
                    max_nodes=max_nodes,
                    max_collections=max_collections,
                    max_seconds=max_seconds,
                    page_size=page_size,
                )
            )
        except Exception as error:
            trees.append(
                {
                    "service_id": service.service_id,
                    "service": service.name,
                    "serial": account.serial,
                    "nickname": account.nickname,
                    "auth": service.auth,
                    "stats": {"errors": 1},
                    "errors": [{"path": service.name, **_crawl_error(error)}],
                    "tree": {"id": "root", "title": service.name, "status": "error", "error": _crawl_error(error)},
                }
            )
        completed = trees[-1]
        stats = completed.get("stats", {})
        print(
            f"Finished {account_label(service, account)} in {time.monotonic() - started:.1f}s: "
            f"{stats.get('collections_opened', 0)} collections, "
            f"{stats.get('items_seen', 0)} items, {stats.get('errors', 0)} errors",
            file=sys.stderr,
            flush=True,
        )
        if checkpoint_path is not None:
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint_path.write_text(
                json.dumps(report(), indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
    return report()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", help="Sonos player IP; otherwise use SSDP")
    parser.add_argument(
        "--device-id",
        help="persistent Sonos controller MachineIdentifier (or SONOS_HOST_DEVICE_ID)",
    )
    parser.add_argument("--list", action="store_true", help="list configured service accounts")
    parser.add_argument("--probe-all", action="store_true", help="test a one-item root browse for every account")
    parser.add_argument("--crawl-all", action="store_true", help="recursively browse every configured account")
    parser.add_argument("--tree-output", help="write --crawl-all JSON to this file instead of stdout")
    parser.add_argument("--max-depth", type=int, default=8, help="maximum collection depth for --crawl-all")
    parser.add_argument("--max-nodes", type=int, default=25000, help="maximum returned items per account for --crawl-all")
    parser.add_argument("--max-collections", type=int, default=1000, help="maximum opened collections per account")
    parser.add_argument("--max-seconds", type=float, default=120.0, help="maximum crawl time per account")
    parser.add_argument(
        "--refresh-credentials",
        action="store_true",
        help="on expiration, accept the provider's transient replacement and retry like the desktop app",
    )
    parser.add_argument("--service-id", type=int, help="catalog service ID")
    parser.add_argument("--serial", type=int, help="account SerialNum0")
    parser.add_argument("--id", default="root", help="SMAPI container/object ID")
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--recursive", action="store_true", help="request recursive getMetadata browsing")
    parser.add_argument("--media-metadata", action="store_true", help="call getMediaMetadata for --id")
    parser.add_argument("--interactive", action="store_true", help="navigate containers in the terminal")
    parser.add_argument("--search-categories", action="store_true", help="show presentation-map search IDs")
    parser.add_argument("--content-browse", action="store_true", help="browse a manifest-driven modern content endpoint")
    parser.add_argument("--search", metavar="TERM", help="run the SMAPI search operation")
    parser.add_argument("--search-id", help="mapped search category ID, such as artist or song")
    args = parser.parse_args()

    if args.host:
        host = args.host
        _, household_id = discover(requested_host=args.host)
    else:
        host, household_id = discover()
    services, accounts = inventory(host, household_id)
    if args.crawl_all:
        crawl_accounts = [
            account
            for account in accounts
            if (args.service_id is None or account.service_id == args.service_id)
            and (args.serial is None or account.serial == args.serial)
        ]
        output_path = Path(args.tree_output).expanduser().resolve() if args.tree_output else None
        report = crawl_all_account_trees(
            host,
            household_id,
            services,
            crawl_accounts,
            host_device_identity=args.device_id,
            refresh_credentials=True,
            max_depth=max(0, args.max_depth),
            max_nodes=max(1, args.max_nodes),
            max_collections=max(1, args.max_collections),
            max_seconds=max(1.0, args.max_seconds),
            page_size=max(1, min(args.count, 1000)),
            checkpoint_path=output_path,
        )
        rendered = json.dumps(report, indent=2, ensure_ascii=False)
        if args.tree_output:
            assert output_path is not None
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(rendered + "\n", encoding="utf-8")
            summary = {
                "status": "ok",
                "output": str(output_path),
                "account_count": report["account_count"],
                "errors": sum(account["stats"].get("errors", 0) for account in report["accounts"]),
            }
            print(json.dumps(summary, indent=2, ensure_ascii=False))
        else:
            print(rendered)
        return
    if args.probe_all:
        device_id = player_device_id(host)
        zone_id = player_zone_id(host)
        rows: list[dict[str, Any]] = []
        for account in accounts:
            service = services.get(account.service_id)
            if not service:
                continue
            client = SmapiClient(
                service,
                account,
                household_id,
                device_id,
                zone_id,
                host,
                host_device_id=args.device_id or host_device_id(host),
                allow_credential_refresh=args.refresh_credentials,
            )
            row: dict[str, Any] = {
                "service_id": service.service_id,
                "service": service.name,
                "serial": account.serial,
                "nickname": account.nickname,
                "auth": service.auth,
            }
            try:
                session = DesktopBrowseSession(client)
                result = session.browse("root", 0, 1)
                row.update(
                    status="ok",
                    root_total=result["total"],
                    transport=result["transport"],
                )
                if result.get("endpoint"):
                    row["endpoint"] = result["endpoint"]
            except SmapiFault as fault:
                row.update(
                    status="unavailable",
                    error_code=fault.code,
                    error=fault.message,
                    http_status=fault.http_status,
                )
                if fault.detail is not None:
                    row["fault_detail"] = redact_diagnostic(fault.detail)
                if fault.response_headers:
                    row["response_headers"] = diagnostic_headers(fault.response_headers)
            except Exception as error:
                row.update(status="error", error=error.__class__.__name__, detail=str(error))
            rows.append(row)
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return
    if args.list or args.service_id is None:
        rows = []
        for account in accounts:
            service = services.get(account.service_id)
            if service:
                rows.append(
                    {
                        "service_id": service.service_id,
                        "service": service.name,
                        "serial": account.serial,
                        "nickname": account.nickname,
                        "auth": service.auth,
                    }
                )
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return

    matches = [
        account for account in accounts
        if account.service_id == args.service_id and (args.serial is None or account.serial == args.serial)
    ]
    if len(matches) != 1:
        raise SystemExit(f"Expected exactly one matching account, found {len(matches)}; specify --serial")
    account = matches[0]
    service = services.get(account.service_id)
    if not service:
        raise SystemExit(f"No descriptor for service {account.service_id}")
    if args.search_categories:
        print(json.dumps(service_search_categories(service), indent=2, ensure_ascii=False))
        return
    if args.content_browse:
        device_id = args.device_id or account_content_device_id(household_id, account)
        content_client = SmapiClient(
            service,
            account,
            household_id,
            player_device_id(host),
            player_zone_id(host),
            host,
            host_device_id=device_id,
            allow_credential_refresh=args.refresh_credentials,
        )
        page = content_browse(
            service,
            account,
            device_id,
            args.id,
            time_zone=local_time_zone(),
            refresh_client=content_client if args.refresh_credentials else None,
            controller_id=content_client.controller_id,
        )
        print(json.dumps({"account": account_label(service, account), "container_id": args.id, **page}, indent=2, ensure_ascii=False))
        return
    client = SmapiClient(
        service,
        account,
        household_id,
        player_device_id(host),
        player_zone_id(host),
        host,
        host_device_id=args.device_id or host_device_id(host),
        allow_credential_refresh=args.refresh_credentials,
    )
    if args.interactive:
        interactive_browse(DesktopBrowseSession(client))
        return
    if args.media_metadata:
        result = client.get_media_metadata(args.id)
        print(json.dumps({"account": account_label(service, account), "id": args.id, **result}, indent=2))
        return
    if args.search is not None:
        if not args.search_id:
            raise SystemExit("--search requires --search-id; use --search-categories to list mapped IDs")
        result = client.search(args.search_id, args.search, args.index, args.count)
        print(
            json.dumps(
                {
                    "account": account_label(service, account),
                    "search_id": args.search_id,
                    "term": args.search,
                    **result,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    if args.id in {"", "root"} and not args.recursive:
        result = DesktopBrowseSession(client).browse(args.id, args.index, args.count)
    else:
        result = client.get_metadata(args.id, args.index, args.count, args.recursive)
    output = {
        "account": account_label(service, account),
        "container_id": args.id,
        **result,
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except SmapiFault as error:
        print(
            json.dumps(
                {
                    "status": "unavailable",
                    "error_code": error.code,
                    "error": error.message,
                    "http_status": error.http_status,
                    "fault_detail": redact_diagnostic(error.detail),
                    "response_headers": diagnostic_headers(error.response_headers),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    except (RuntimeError, urllib.error.URLError) as error:
        print(
            json.dumps(
                {"status": "error", "error": error.__class__.__name__, "detail": str(error)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from None

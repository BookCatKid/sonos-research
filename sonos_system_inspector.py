#!/usr/bin/env python3
"""Read-only Sonos household, capability, account, and controller inspector.

The inspector intentionally has no mutation primitive. It inventories the same
LAN descriptions and household state consumed by Sonos controllers, catalogs
every advertised UPnP operation, and invokes only a small explicit allow-list of
read operations.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import posixpath
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from smapi_browser import (
    LocalSoapFault,
    descendants,
    inventory,
    local_name,
    local_soap,
)
from sonos_discovery import DiscoveredPlayer, discover_players


ZONE_GROUP_TOPOLOGY = "urn:schemas-upnp-org:service:ZoneGroupTopology:1"
DEVICE_PROPERTIES = "urn:schemas-upnp-org:service:DeviceProperties:1"
SYSTEM_PROPERTIES = "urn:schemas-upnp-org:service:SystemProperties:1"

DEFAULT_CROSSOVER_ROOT = (
    Path.home() / "Library/Application Support/CrossOver/Bottles/sonos/drive_c"
)
DEFAULT_DECOMPILED_ROOT = Path("/tmp/sonos-desktop-decompiled")

SENSITIVE_NAMES = {
    "accountkey",
    "accountpassword",
    "accounttoken",
    "authorization",
    "cookie",
    "key",
    "password",
    "privatekey",
    "refreshtoken",
    "token",
}

DESTRUCTIVE_WORDS = {
    "add",
    "begin",
    "change",
    "clear",
    "configure",
    "create",
    "delete",
    "disable",
    "enable",
    "destroy",
    "edit",
    "factoryreset",
    "format",
    "remove",
    "rename",
    "replace",
    "reset",
    "separate",
    "set",
    "snooze",
    "start",
    "stop",
    "submit",
    "become",
    "bond",
    "delegate",
    "update",
}

# Only these operations may be executed by the inspector. The advertised action
# catalog includes everything else without invoking it.
READ_ACTIONS: dict[str, tuple[str, ...]] = {
    "urn:schemas-upnp-org:service:DeviceProperties": (
        "GetHouseholdID",
        "GetZoneAttributes",
        "GetZoneInfo",
        "GetLEDState",
        "GetButtonLockState",
        "GetAutoplayRoomUUID",
        "GetAutoplayVolume",
        "GetAutoplayLinkedZones",
        "GetUseAutoplayVolume",
    ),
    "urn:schemas-upnp-org:service:SystemProperties": ("GetRDM",),
    "urn:schemas-upnp-org:service:ZoneGroupTopology": (
        "GetZoneGroupAttributes",
        "GetZoneGroupState",
    ),
    "urn:schemas-upnp-org:service:HTControl": (
        "GetIRRepeaterState",
        "IsRemoteConfigured",
        "GetLEDFeedbackState",
    ),
    "urn:schemas-upnp-org:service:ContentDirectory": (
        "GetAlbumArtistDisplayOption",
        "GetBrowseable",
        "GetLastIndexChange",
        "GetSearchCapabilities",
        "GetShareIndexInProgress",
        "GetSortCapabilities",
        "GetSystemUpdateID",
    ),
    "urn:schemas-upnp-org:service:RenderingControl": (
        "GetBass",
        "GetTreble",
        "GetLoudness",
        "GetMute",
        "GetVolume",
        "GetOutputFixed",
        "GetRoomCalibrationStatus",
        "GetSupportsOutputFixed",
    ),
    "urn:schemas-upnp-org:service:GroupRenderingControl": (
        "GetGroupMute",
        "GetGroupVolume",
    ),
    "urn:schemas-upnp-org:service:AVTransport": (
        "GetCrossfadeMode",
        "GetMediaInfo",
        "GetPositionInfo",
        "GetTransportInfo",
        "GetTransportSettings",
        "GetCurrentTransportActions",
        "GetDeviceCapabilities",
        "GetRemainingSleepTimerDuration",
    ),
    "urn:schemas-upnp-org:service:ConnectionManager": (
        "GetCurrentConnectionIDs",
        "GetProtocolInfo",
    ),
    "urn:schemas-upnp-org:service:AudioIn": (
        "GetAudioInputAttributes",
        "GetLineInLevel",
    ),
    "urn:schemas-upnp-org:service:AlarmClock": (
        "GetDailyIndexRefreshTime",
        "GetFormat",
        "GetTimeNow",
        "GetTimeServer",
        "GetTimeZone",
        "GetTimeZoneAndRule",
        "ListAlarms",
    ),
}

# Required arguments for otherwise safe getters. Unknown arguments are never
# guessed, which keeps model-specific extensions from being invoked accidentally.
READ_ARGUMENTS: dict[str, dict[str, str]] = {
    "GetBass": {"InstanceID": "0", "Channel": "Master"},
    "GetTreble": {"InstanceID": "0", "Channel": "Master"},
    "GetLoudness": {"InstanceID": "0", "Channel": "Master"},
    "GetMute": {"InstanceID": "0", "Channel": "Master"},
    "GetVolume": {"InstanceID": "0", "Channel": "Master"},
    "GetGroupMute": {"InstanceID": "0"},
    "GetGroupVolume": {"InstanceID": "0"},
    "GetCrossfadeMode": {"InstanceID": "0"},
    "GetMediaInfo": {"InstanceID": "0"},
    "GetPositionInfo": {"InstanceID": "0"},
    "GetTransportInfo": {"InstanceID": "0"},
    "GetTransportSettings": {"InstanceID": "0"},
    "GetCurrentTransportActions": {"InstanceID": "0"},
    "GetDeviceCapabilities": {"InstanceID": "0"},
    "GetRemainingSleepTimerDuration": {"InstanceID": "0"},
    "GetOutputFixed": {"InstanceID": "0"},
    "GetRoomCalibrationStatus": {"InstanceID": "0"},
    "GetSupportsOutputFixed": {"InstanceID": "0"},
}

COORDINATOR_ONLY_READS = {"GetRemainingSleepTimerDuration"}


@dataclass(frozen=True)
class ServiceDescription:
    service_type: str
    service_id: str
    control_url: str
    event_url: str
    scpd_url: str


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def fetch_player_path(host: str, raw_path: str, timeout: float = 8.0) -> bytes:
    """Fetch a speaker-owned path without following redirects or authorities."""
    parsed = urllib.parse.urlsplit(raw_path)
    if parsed.scheme or parsed.netloc:
        raise ValueError(f"Player description supplied an absolute URL: {raw_path!r}")
    decoded_path = urllib.parse.unquote(parsed.path)
    if any(segment in {".", ".."} for segment in decoded_path.split("/")):
        raise ValueError(f"Player description supplied an invalid path: {raw_path!r}")
    normalized = posixpath.normpath("/" + decoded_path.lstrip("/"))
    url = urllib.parse.urlunsplit(("http", f"{host}:1400", normalized, parsed.query, ""))
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(url, timeout=timeout) as response:
        return response.read()


def _text(node: ET.Element, name: str, default: str = "") -> str:
    for child in node:
        if local_name(child.tag) == name:
            return (child.text or default).strip()
    return default


def parse_device_description(xml_data: bytes) -> tuple[dict[str, Any], list[ServiceDescription]]:
    root = ET.fromstring(xml_data)
    devices = descendants(root, "device")
    if not devices:
        raise RuntimeError("Device description contained no device")
    primary = devices[0]
    details = {
        name: _text(primary, name)
        for name in (
            "deviceType",
            "friendlyName",
            "roomName",
            "modelName",
            "modelNumber",
            "modelDescription",
            "serialNum",
            "softwareVersion",
            "hardwareVersion",
            "displayVersion",
            "UDN",
            "iconList",
        )
        if _text(primary, name)
    }
    services: list[ServiceDescription] = []
    for device in devices:
        for service in descendants(device, "service"):
            service_type = _text(service, "serviceType")
            control = _text(service, "controlURL")
            scpd = _text(service, "SCPDURL")
            if service_type and control and scpd:
                candidate = ServiceDescription(
                    service_type=service_type,
                    service_id=_text(service, "serviceId"),
                    control_url=control,
                    event_url=_text(service, "eventSubURL"),
                    scpd_url=scpd,
                )
                if candidate not in services:
                    services.append(candidate)
    return details, services


def _service_prefix(service_type: str) -> str:
    match = re.match(r"(.+):\d+$", service_type)
    return match.group(1) if match else service_type


def action_risk(action: str) -> str:
    compact = re.sub(r"[^a-z]", "", action.lower())
    if compact.startswith(("get", "list", "browse", "search", "query", "is", "has")):
        return "read"
    if any(word in compact for word in DESTRUCTIVE_WORDS):
        return "mutation"
    return "unknown"


def parse_scpd(xml_data: bytes) -> dict[str, Any]:
    root = ET.fromstring(xml_data)
    actions: list[dict[str, Any]] = []
    for action in descendants(root, "action"):
        name = _text(action, "name")
        arguments = []
        for argument in descendants(action, "argument"):
            arguments.append(
                {
                    "name": _text(argument, "name"),
                    "direction": _text(argument, "direction"),
                    "related_state_variable": _text(argument, "relatedStateVariable"),
                }
            )
        actions.append({"name": name, "risk": action_risk(name), "arguments": arguments})
    state_variables: list[dict[str, Any]] = []
    for variable in descendants(root, "stateVariable"):
        allowed = [(node.text or "").strip() for node in descendants(variable, "allowedValue")]
        row: dict[str, Any] = {
            "name": _text(variable, "name"),
            "data_type": _text(variable, "dataType"),
            "evented": variable.attrib.get("sendEvents", ""),
        }
        if allowed:
            row["allowed_values"] = allowed
        state_variables.append(row)
    return {"actions": actions, "state_variables": state_variables}


def soap_values(xml_data: bytes) -> dict[str, Any]:
    root = ET.fromstring(xml_data)
    body_nodes = descendants(root, "Body")
    response = list(body_nodes[0])[0] if body_nodes and list(body_nodes[0]) else root
    result: dict[str, Any] = {}
    for child in response:
        name = local_name(child.tag)
        value = "".join(child.itertext()).strip()
        if name in SENSITIVE_NAMES or any(marker in name.lower() for marker in ("password", "token", "key")):
            result[name] = {"redacted": True, "present": bool(value), "length": len(value)}
        elif value.startswith("<") and value.endswith(">"):
            result[name] = {"xml": True, "length": len(value), "sha256": hashlib.sha256(value.encode()).hexdigest()}
        else:
            result[name] = value
    return result


def parse_zone_group_state(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(html.unescape(xml_text))
    groups: list[dict[str, Any]] = []
    members_by_uuid: dict[str, dict[str, Any]] = {}
    for group in descendants(root, "ZoneGroup"):
        members: list[dict[str, Any]] = []
        for member in [child for child in group if local_name(child.tag) == "ZoneGroupMember"]:
            attrs = dict(member.attrib)
            safe = {
                key: attrs[key]
                for key in (
                    "UUID",
                    "ZoneName",
                    "Location",
                    "Invisible",
                    "IsZoneBridge",
                    "SoftwareVersion",
                    "MinCompatibleVersion",
                    "LegacyCompatibleVersion",
                    "BootSeq",
                    "WirelessMode",
                    "ChannelFreq",
                    "HTSatChanMapSet",
                    "VoiceConfigState",
                    "MicEnabled",
                    "AirPlayEnabled",
                    "BluetoothConnected",
                    "MoreInfo",
                )
                if attrs.get(key, "") != ""
            }
            satellites = []
            for satellite in [child for child in member if local_name(child.tag) == "Satellite"]:
                satellites.append(
                    {
                        key: value
                        for key, value in satellite.attrib.items()
                        if key in {"UUID", "ZoneName", "Location", "Invisible", "SoftwareVersion", "MoreInfo"}
                    }
                )
            if satellites:
                safe["satellites"] = satellites
            members.append(safe)
            if "UUID" in safe:
                members_by_uuid[str(safe["UUID"])] = safe
        groups.append(
            {
                "id": group.attrib.get("ID", ""),
                "coordinator": group.attrib.get("Coordinator", ""),
                "members": members,
            }
        )
    return {"group_count": len(groups), "member_count": len(members_by_uuid), "groups": groups}


def _read_action_allowed(service_type: str, action: str) -> bool:
    return action in READ_ACTIONS.get(_service_prefix(service_type), ())


def inspect_player(host: str, *, allow_group_reads: bool = True) -> dict[str, Any]:
    details, services = parse_device_description(fetch_player_path(host, "/xml/device_description.xml"))
    result: dict[str, Any] = {
        "host": host,
        "device": details,
        "services": [],
        "reads": {},
        "errors": [],
    }
    for service in services:
        service_row: dict[str, Any] = asdict(service)
        try:
            scpd = parse_scpd(fetch_player_path(host, service.scpd_url))
            service_row.update(scpd)
        except Exception as error:
            service_row["description_error"] = f"{error.__class__.__name__}: {error}"
            result["services"].append(service_row)
            continue
        result["services"].append(service_row)
        for action in scpd["actions"]:
            name = action["name"]
            if not _read_action_allowed(service.service_type, name):
                continue
            if _service_prefix(service.service_type).endswith("GroupRenderingControl") and not allow_group_reads:
                continue
            if name in COORDINATOR_ONLY_READS and not allow_group_reads:
                continue
            supplied = READ_ARGUMENTS.get(name, {})
            required_inputs = [
                argument["name"]
                for argument in action["arguments"]
                if argument["direction"].lower() == "in"
            ]
            if any(argument not in supplied for argument in required_inputs):
                continue
            try:
                response = local_soap(
                    host,
                    service.control_url,
                    service.service_type,
                    name,
                    {key: supplied[key] for key in required_inputs},
                )
                values = soap_values(response)
                if name == "GetZoneGroupState" and isinstance(values.get("ZoneGroupState"), dict):
                    # soap_values deliberately replaces embedded XML with a digest;
                    # parse this one known, non-secret topology payload separately.
                    root = ET.fromstring(response)
                    nodes = descendants(root, "ZoneGroupState")
                    topology_text = (nodes[0].text or "") if nodes else ""
                    values["ZoneGroupState"] = parse_zone_group_state(topology_text)
                if name == "ListAlarms" and isinstance(values.get("CurrentAlarmList"), dict):
                    root = ET.fromstring(response)
                    nodes = descendants(root, "CurrentAlarmList")
                    alarm_text = (nodes[0].text or "") if nodes else ""
                    try:
                        alarms = descendants(ET.fromstring(alarm_text), "Alarm") if alarm_text else []
                        values["CurrentAlarmList"] = {"alarm_count": len(alarms)}
                    except ET.ParseError:
                        pass
                result["reads"][name] = values
            except LocalSoapFault as error:
                result["errors"].append({"action": name, "status": error.http_status, "error": str(error)})
            except Exception as error:
                result["errors"].append({"action": name, "error": f"{error.__class__.__name__}: {error}"})
    return result


def account_inventory(host: str, household_id: str) -> dict[str, Any]:
    services, accounts = inventory(host, household_id)
    service_rows = []
    for service in sorted(services.values(), key=lambda item: (item.name.lower(), item.service_id)):
        service_rows.append(
            {
                "id": service.service_id,
                "name": service.name,
                "auth": service.auth,
                "capabilities": service.capabilities,
                "secure_endpoint_host": urllib.parse.urlparse(service.uri).hostname or "",
                "manifest_host": urllib.parse.urlparse(service.manifest_uri).hostname or "",
                "policy": service.policy,
                "configured_account_count": sum(account.service_id == service.service_id for account in accounts),
            }
        )
    account_rows = []
    for account in sorted(accounts, key=lambda item: (item.service_id, item.serial)):
        service = services.get(account.service_id)
        try:
            account_uid = f"{account.account_uid:08x}"
        except RuntimeError:
            account_uid = None
        account_rows.append(
            {
                "service_id": account.service_id,
                "service": service.name if service else "unmapped",
                "serial": account.serial,
                "nickname": account.nickname,
                "tier": account.tier,
                "schema_revision": account.schema_revision,
                "account_uid": account_uid,
                "credential_state": {
                    "username_present": bool(account.username),
                    "password_present": bool(account.password),
                    "token_present": bool(account.token),
                    "key_present": bool(account.key),
                    "needs_reauthorization": account.token == "needs_reauth",
                },
            }
        )
    return {
        "service_count": len(service_rows),
        "configured_account_count": len(account_rows),
        "services": service_rows,
        "accounts": account_rows,
    }


def inspect_local_controller(
    crossover_root: Path = DEFAULT_CROSSOVER_ROOT,
    decompiled_root: Path = DEFAULT_DECOMPILED_ROOT,
) -> dict[str, Any]:
    result: dict[str, Any] = {"files": {}, "hidden_surfaces": {}}
    runtime = crossover_root / "ProgramData/SonosV2,_Inc/runtime"
    anacapa = crossover_root / "ProgramData/SonosV2,_Inc/anacapa/conf/anacapa.conf"
    desktop_config = (
        crossover_root
        / "Program Files (x86)/SonosV2/Sonos.Controller.Desktop.dll.config"
    )
    cache = runtime / "sonos_application_cache.config"
    uidata = runtime / "uidata.xml"
    for name, path in {
        "application_cache": cache,
        "controller_identity": uidata,
        "anacapa_config": anacapa,
        "desktop_config": desktop_config,
    }.items():
        result["files"][name] = {"path": str(path), "exists": path.exists()}
    if cache.exists():
        try:
            root = ET.parse(cache).getroot()
            settings = []
            alarm_count = 0
            for node in root.iter():
                key = node.attrib.get("key") or node.attrib.get("name")
                if key:
                    settings.append(key)
                    if "alarm" in key.lower():
                        alarm_count += 1
            result["files"]["application_cache"].update(
                setting_names=sorted(set(settings)), alarm_related_setting_count=alarm_count
            )
        except ET.ParseError as error:
            result["files"]["application_cache"]["error"] = str(error)
    if uidata.exists():
        text = uidata.read_text(encoding="utf-8", errors="replace")
        result["files"]["controller_identity"].update(
            machine_identifier_present="MachineIdentifier" in text,
            mac_address_present="MACAddress" in text,
            values_redacted=True,
        )
    if anacapa.exists():
        settings: dict[str, str] = {}
        for line in anacapa.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            key, _, value = stripped.partition(" ")
            settings[key] = value.strip()
        result["files"]["anacapa_config"]["settings"] = settings
    if desktop_config.exists():
        try:
            root = ET.parse(desktop_config).getroot()
            debug_nodes = root.findall("./debugConsole")
            result["files"]["desktop_config"].update(
                debug_console_configured=bool(debug_nodes),
                debug_console_allowed=(debug_nodes[0].attrib.get("allow") if debug_nodes else None),
            )
        except ET.ParseError as error:
            result["files"]["desktop_config"]["error"] = str(error)
    metrics = decompiled_root / "Sonos.Controller.Desktop.SCLib.Resources.ctrlMetricsConfig.xml"
    if metrics.exists():
        try:
            root = ET.parse(metrics).getroot()
            categories = [node.attrib.get("name", "") for node in root.iter() if local_name(node.tag) == "Category"]
            result["hidden_surfaces"]["metrics"] = {
                "path": str(metrics),
                "category_count": len(categories),
                "categories": categories,
            }
        except ET.ParseError as error:
            result["hidden_surfaces"]["metrics"] = {"path": str(metrics), "error": str(error)}
    constant_file = Path("/tmp/sonos-interop-decompiled.MZAhPX/Sonos.SCLib.Interop/sclib.cs")
    interop_root = constant_file.parent
    if constant_file.exists():
        text = constant_file.read_text(encoding="utf-8", errors="replace")
        groups = {
            # The word boundary excludes the prefix of generated SWIG getters
            # (FOO_get), which previously created a fake trailing-underscore
            # duplicate for every constant.
            "content_debug": r"SCISETTING_CONTENT_DEBUG_[A-Z0-9_]+\b",
            "receipt_debug": r"SCISETTING_RECEIPT_DEBUG_[A-Z0-9_]+\b",
            "developer_options": r"SC_DEVOPT_[A-Z0-9_]+\b",
            "experiments": r"SCI_EXPERIMENT(?:ALFEATURE)?_[A-Z0-9_]+\b",
            "debug_actions": r"SC_ACTIONID_DEBUG_[A-Z0-9_]+\b",
        }
        result["hidden_surfaces"]["native_constants"] = {
            name: sorted(set(re.findall(pattern, text))) for name, pattern in groups.items()
        }
    token_manager = interop_root / "SCITokenManager.cs"
    user_account = interop_root / "SCIUserAccount.cs"
    if token_manager.exists():
        text = token_manager.read_text(encoding="utf-8", errors="replace")
        result["hidden_surfaces"]["first_party_identity"] = {
            "token_purposes": sorted(set(re.findall(r"\b[A-Z][A-Z0-9_]*_PURPOSE\b", text))),
            "source": str(token_manager),
            "static_names_only": True,
        }
    if user_account.exists():
        text = user_account.read_text(encoding="utf-8", errors="replace")
        identity = result["hidden_surfaces"].setdefault("first_party_identity", {})
        identity["roles"] = sorted(set(re.findall(r"^\s*(OWNER|ADMIN|UNKNOWN),?$", text, re.MULTILINE)))
        identity["profile_methods"] = sorted(
            set(re.findall(r"public virtual [^{\n]+\s+(getEmail|getId|getReleaseProgramType|getVerificationStatus|refreshUserProfileInfo|signOut)\(", text))
        )
    wizard_sources = [interop_root / "SCIHousehold.cs", interop_root / "SCISystem.cs"]
    wizard_factories: list[dict[str, str]] = []
    for source in wizard_sources:
        if not source.exists():
            continue
        text = source.read_text(encoding="utf-8", errors="replace")
        for name in sorted(set(re.findall(r"\b(create[A-Za-z0-9_]*Wizard(?:Action)?)\(", text))):
            wizard_factories.append({"factory": name, "source": str(source)})
    if wizard_factories:
        result["hidden_surfaces"]["wizard_factories"] = {
            "count": len(wizard_factories),
            "factories": wizard_factories,
            "static_names_only": True,
        }
    return result


def capability_summary(players: list[dict[str, Any]]) -> dict[str, Any]:
    unique_actions: dict[str, dict[str, Any]] = {}
    models: dict[str, int] = {}
    firmware: dict[str, int] = {}
    for player in players:
        device = player.get("device", {})
        model = device.get("modelName") or device.get("modelNumber") or "unknown"
        version = device.get("softwareVersion") or "unknown"
        models[model] = models.get(model, 0) + 1
        firmware[version] = firmware.get(version, 0) + 1
        for service in player.get("services", []):
            for action in service.get("actions", []):
                key = f"{service['service_type']}#{action['name']}"
                unique_actions.setdefault(
                    key,
                    {
                        "service_type": service["service_type"],
                        "action": action["name"],
                        "risk": action["risk"],
                        "advertised_by": [],
                    },
                )["advertised_by"].append(player["host"])
    actions = sorted(unique_actions.values(), key=lambda item: (item["service_type"], item["action"]))
    return {
        "models": models,
        "firmware_versions": firmware,
        "advertised_action_count": len(actions),
        "read_action_count": sum(action["risk"] == "read" for action in actions),
        "mutation_action_count": sum(action["risk"] == "mutation" for action in actions),
        "unknown_action_count": sum(action["risk"] == "unknown" for action in actions),
        "actions": actions,
    }


def markdown_report(report: dict[str, Any]) -> str:
    discovery = report["discovery"]
    summary = report["capabilities"]
    accounts = report.get("music", {})
    topology = report.get("topology", {})
    lines = [
        "# Sonos system intelligence report",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "This report is credential-redacted and was produced using read-only operations.",
        "",
        "## Household",
        "",
        f"- Household: `{discovery.get('household_id', 'unknown')}`",
        f"- Discovered players: {len(discovery.get('players', []))}",
        f"- Topology groups: {topology.get('group_count', 'unknown')}",
        f"- Topology members: {topology.get('member_count', 'unknown')}",
        "",
        "## Models and firmware",
        "",
    ]
    for model, count in summary["models"].items():
        lines.append(f"- {model}: {count}")
    lines.extend(["", "Firmware:", ""])
    for version, count in summary["firmware_versions"].items():
        lines.append(f"- {version}: {count}")
    lines.extend(
        [
            "",
            "## Music accounts",
            "",
            f"- Catalog services: {accounts.get('service_count', 0)}",
            f"- Configured accounts: {accounts.get('configured_account_count', 0)}",
            "",
        ]
    )
    for account in accounts.get("accounts", []):
        reauth = " — needs reauthorization" if account["credential_state"]["needs_reauthorization"] else ""
        label = account.get("nickname") or f"serial {account['serial']}"
        lines.append(f"- {account['service']} ({label}){reauth}")
    lines.extend(
        [
            "",
            "## Advertised operation surface",
            "",
            f"- Total unique actions: {summary['advertised_action_count']}",
            f"- Classified read operations: {summary['read_action_count']}",
            f"- Classified mutations: {summary['mutation_action_count']}",
            f"- Unknown/unclassified: {summary['unknown_action_count']}",
            "",
            "The JSON companion contains every service, action, argument, state variable, safe read result, and local-controller artifact.",
            "",
            "## Safety boundary",
            "",
            "The inspector contains no generic SOAP execution option and invokes only its hard-coded read allow-list. Advertised mutation operations are cataloged but never called.",
            "",
        ]
    )
    return "\n".join(lines)


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", action="append", help="player IPv4 address; repeat for multiple players")
    parser.add_argument("--timeout", type=float, default=3.2, help="SSDP discovery duration")
    parser.add_argument("--output", default="analysis/system-inspection.json", help="redacted JSON output")
    parser.add_argument("--markdown", default="analysis/system-inspection.md", help="summary Markdown output")
    parser.add_argument("--skip-accounts", action="store_true", help="skip event subscription/account inventory")
    parser.add_argument("--skip-local", action="store_true", help="skip installed-controller artifact inventory")
    args = parser.parse_args()

    discovered = [] if args.host else discover_players(args.timeout)
    by_host = {player.host: player for player in discovered}
    hosts = list(dict.fromkeys(args.host or [player.host for player in discovered]))
    if not hosts:
        raise SystemExit("No Sonos players discovered; pass --host ADDRESS")
    household_id = next((by_host[host].household_id for host in hosts if host in by_host), "")
    if not household_id:
        # A targeted host may not answer multicast; the read-only player action
        # supplies the same household identifier.
        response = local_soap(hosts[0], "/DeviceProperties/Control", DEVICE_PROPERTIES, "GetHouseholdID", {})
        nodes = descendants(ET.fromstring(response), "CurrentHouseholdID")
        household_id = (nodes[0].text or "").strip() if nodes else ""
    # One topology read gives us every current member, including players that
    # did not independently answer SSDP, and tells us which devices coordinate
    # GroupRenderingControl. Querying that service on a non-coordinator is a
    # harmless UPnP error, but avoiding it makes the report semantically clean.
    inspection_errors: list[dict[str, str]] = []
    topology_seed_failed = False
    try:
        topology_response = local_soap(
            hosts[0],
            "/ZoneGroupTopology/Control",
            ZONE_GROUP_TOPOLOGY,
            "GetZoneGroupState",
            {},
        )
        topology_nodes = descendants(ET.fromstring(topology_response), "ZoneGroupState")
        seed_topology = parse_zone_group_state(
            (topology_nodes[0].text or "") if topology_nodes else "<ZoneGroupState/>"
        )
    except Exception as error:
        topology_seed_failed = True
        seed_topology = {"group_count": 0, "member_count": 0, "groups": []}
        inspection_errors.append(
            {"stage": "seed_topology", "error": f"{error.__class__.__name__}: {error}"}
        )
    coordinator_ids = {group.get("coordinator", "") for group in seed_topology.get("groups", [])}
    coordinator_hosts: set[str] = set()
    for group in seed_topology.get("groups", []):
        for member in group.get("members", []):
            location_host = urllib.parse.urlparse(member.get("Location", "")).hostname
            if location_host and location_host not in hosts:
                hosts.append(location_host)
            if location_host and member.get("UUID") in coordinator_ids:
                coordinator_hosts.add(location_host)
    reports_by_host: dict[str, dict[str, Any]] = {}
    for host in hosts:
        try:
            reports_by_host[host] = inspect_player(
                host,
                allow_group_reads=topology_seed_failed or host in coordinator_hosts,
            )
        except Exception as error:
            reports_by_host[host] = {
                "host": host,
                "device": {},
                "services": [],
                "reads": {},
                "errors": [{"error": f"{error.__class__.__name__}: {error}"}],
            }
    player_reports = list(reports_by_host.values())
    hosts = list(reports_by_host)
    topology: dict[str, Any] = {}
    for player in player_reports:
        candidate = player.get("reads", {}).get("GetZoneGroupState", {}).get("ZoneGroupState")
        if isinstance(candidate, dict) and candidate.get("groups") is not None:
            topology = candidate
            break
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "read_only": True,
        "inspection_errors": inspection_errors,
        "discovery": {
            "household_id": household_id,
            "players": [asdict(by_host[host]) if host in by_host else {"host": host, "targeted": True} for host in hosts],
        },
        "topology": topology,
        "players": player_reports,
        "capabilities": capability_summary(player_reports),
    }
    if not args.skip_accounts:
        try:
            report["music"] = account_inventory(hosts[0], household_id)
        except Exception as error:
            report["music"] = {"error": f"{error.__class__.__name__}: {error}"}
    if not args.skip_local:
        report["local_controller"] = inspect_local_controller()
    output_path = Path(args.output).expanduser().resolve()
    markdown_path = Path(args.markdown).expanduser().resolve()
    write_private(output_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    write_private(markdown_path, markdown_report(report))
    print(
        json.dumps(
            {
                "status": "ok",
                "read_only": True,
                "players": len(player_reports),
                "household_id": household_id,
                "advertised_actions": report["capabilities"]["advertised_action_count"],
                "configured_music_accounts": report.get("music", {}).get("configured_account_count"),
                "json": str(output_path),
                "markdown": str(markdown_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

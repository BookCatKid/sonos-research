#!/usr/bin/env python3
"""Generate controller identity and accurate alarm caches for Sonos households."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from smapi_browser import descendants, local_soap  # noqa: E402
from sonos_discovery import discover_players  # noqa: E402

VERSION = "90.0-77070"
BUNDLE_ROOT = Path(__file__).resolve().parent / VERSION
FIXTURE_ROOT = BUNDLE_ROOT / "fixture/drive_c"
DEFAULT_OUTPUT_ROOT = REPOSITORY_ROOT / "generated/controller-state"

DEVICE_PROPERTIES = "urn:schemas-upnp-org:service:DeviceProperties:1"
ZONE_GROUP_TOPOLOGY = "urn:schemas-upnp-org:service:ZoneGroupTopology:1"
ALARM_CLOCK = "urn:schemas-upnp-org:service:AlarmClock:1"


@dataclass(frozen=True)
class ControllerIdentity:
    machine_identifier: str
    mac_address: str


@dataclass(frozen=True)
class HouseholdState:
    household_id: str
    muse_household_id: str
    alarm_list_version: str
    alarms: list[dict[str, object]]
    source_host: str

    @property
    def alarm_cache_key(self) -> str:
        return f"{self.household_id}.{self.muse_household_id.rsplit('.', 1)[-1]}alarms"

    @property
    def alarm_update_id(self) -> str:
        return self.alarm_list_version.rsplit(":", 1)[0]


def _write_xml(path: Path, root: ET.Element) -> None:
    ET.indent(root, space="  ")
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def _response_text(response: bytes, name: str) -> str:
    nodes = descendants(ET.fromstring(response), name)
    return (nodes[0].text or "").strip() if nodes else ""


def _bool_attribute(node: ET.Element, name: str) -> bool:
    return node.attrib.get(name, "").strip().lower() in {"1", "true", "yes"}


def _integer_attribute(node: ET.Element, name: str) -> int:
    try:
        return int(node.attrib.get(name, "0"))
    except ValueError:
        return 0


def _cache_alarm(node: ET.Element) -> dict[str, object]:
    return {
        "ID": _integer_attribute(node, "ID"),
        "duration": node.attrib.get("Duration", ""),
        "enabled": _bool_attribute(node, "Enabled"),
        "includeLinkedZones": _bool_attribute(node, "IncludeLinkedZones"),
        "playMode": node.attrib.get("PlayMode", ""),
        "program": node.attrib.get("ProgramURI", ""),
        "programMetadata": node.attrib.get("ProgramMetaData", ""),
        "recurrence": node.attrib.get("Recurrence", ""),
        "roomUUID": node.attrib.get("RoomUUID", ""),
        "time": node.attrib.get("StartTime", ""),
        "volume": _integer_attribute(node, "Volume"),
    }


def household_id(host: str) -> str:
    response = local_soap(
        host,
        "/DeviceProperties/Control",
        DEVICE_PROPERTIES,
        "GetHouseholdID",
        {},
    )
    value = _response_text(response, "CurrentHouseholdID")
    if not value:
        raise RuntimeError(f"Player {host} returned no household ID")
    return value


def fetch_household_state(host: str) -> HouseholdState:
    current_household = household_id(host)
    attributes = local_soap(
        host,
        "/ZoneGroupTopology/Control",
        ZONE_GROUP_TOPOLOGY,
        "GetZoneGroupAttributes",
        {},
    )
    muse_household = _response_text(attributes, "CurrentMuseHouseholdId")
    if not muse_household or "." not in muse_household:
        raise RuntimeError(f"Player {host} returned no usable Muse household ID")

    alarm_response = local_soap(
        host,
        "/AlarmClock/Control",
        ALARM_CLOCK,
        "ListAlarms",
        {},
    )
    alarm_xml = _response_text(alarm_response, "CurrentAlarmList")
    alarm_version = _response_text(alarm_response, "CurrentAlarmListVersion")
    if not alarm_version:
        raise RuntimeError(f"Player {host} returned no alarm-list version")
    alarm_nodes = descendants(ET.fromstring(alarm_xml), "Alarm") if alarm_xml else []
    return HouseholdState(
        current_household,
        muse_household,
        alarm_version,
        [_cache_alarm(node) for node in alarm_nodes],
        host,
    )


def _identity_xml(identity: ControllerIdentity) -> ET.Element:
    items = ET.Element("Items")
    for name, value in (
        ("MachineIdentifier", identity.machine_identifier),
        ("MACAddress", identity.mac_address),
    ):
        item = ET.SubElement(items, "Item")
        ET.SubElement(item, "Name").text = name
        ET.SubElement(item, "Value").text = value
    return items


def _read_identity(path: Path) -> ControllerIdentity | None:
    if not path.exists():
        return None
    values: dict[str, str] = {}
    for item in ET.parse(path).getroot().iter("Item"):
        name = item.findtext("Name", "").strip()
        value = item.findtext("Value", "").strip()
        if name:
            values[name] = value
    if values.get("MachineIdentifier") and values.get("MACAddress"):
        return ControllerIdentity(values["MachineIdentifier"], values["MACAddress"])
    return None


def _normalize_mac(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Fa-f]", "", value).upper()
    if len(normalized) != 12 or normalized == "000000000000":
        raise ValueError("MAC address must contain twelve non-zero hexadecimal digits")
    return normalized


def load_or_create_identity(
    output_root: Path,
    *,
    machine_identifier: str = "",
    mac_address: str = "",
) -> ControllerIdentity:
    identity_path = output_root / "controller/uidata.xml"
    existing = _read_identity(identity_path)
    if existing:
        return existing
    identifier = str(uuid.UUID(machine_identifier)) if machine_identifier else str(uuid.uuid4())
    mac = _normalize_mac(mac_address or f"{uuid.getnode():012X}")
    identity = ControllerIdentity(identifier, mac)
    _write_xml(identity_path, _identity_xml(identity))
    return identity


def _application_cache(state: HouseholdState) -> ET.Element:
    configuration = ET.Element("configuration")
    settings = ET.SubElement(configuration, "appSettings")
    values = {
        "searchHistoryEnabled": "True",
        "staleSessionEnabled": "True",
        "business::global.usageContext.value": "CONSUMER",
        state.alarm_cache_key: json.dumps(
            {"alarms": state.alarms, "updateID": state.alarm_update_id},
            separators=(",", ":"),
        ),
        "favoritesHidden": "False",
    }
    for key, value in values.items():
        ET.SubElement(settings, "add", key=key, value=value)
    return configuration


def _safe_household_directory(household: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", household)


def write_household_state(
    output_root: Path,
    state: HouseholdState,
    identity: ControllerIdentity,
    *,
    fixture_root: Path = FIXTURE_ROOT,
) -> Path:
    household_root = output_root / "households" / _safe_household_directory(state.household_id)
    drive_c = household_root / "drive_c"
    runtime = drive_c / "ProgramData/SonosV2,_Inc/runtime"
    _write_xml(runtime / "uidata.xml", _identity_xml(identity))
    _write_xml(runtime / "sonos_application_cache.config", _application_cache(state))

    for relative in (
        Path("ProgramData/SonosV2,_Inc/anacapa/conf/anacapa.conf"),
        Path("Program Files (x86)/SonosV2/Sonos.Controller.Desktop.dll.config"),
    ):
        source = fixture_root / relative
        target = drive_c / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    manifest = {
        "schema_version": 1,
        "household_id": state.household_id,
        "muse_household_id": state.muse_household_id,
        "alarm_list_version": state.alarm_list_version,
        "alarm_count": len(state.alarms),
        "source_host": state.source_host,
        "controller_machine_identifier": identity.machine_identifier,
        "controller_mac_address": identity.mac_address,
        "drive_c": str(drive_c),
    }
    (household_root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return drive_c


def generate_synthetic_fixture(output_root: Path) -> list[Path]:
    identity = ControllerIdentity("00000000-0000-4000-8000-000000000001", "020000000001")
    state = HouseholdState(
        "Sonos_fixture_household",
        "Sonos_fixture_household.fixture",
        "RINCON_00000000000101400:0",
        [],
        "192.0.2.1",
    )
    runtime = output_root / "ProgramData/SonosV2,_Inc/runtime"
    uidata = runtime / "uidata.xml"
    cache = runtime / "sonos_application_cache.config"
    _write_xml(uidata, _identity_xml(identity))
    _write_xml(cache, _application_cache(state))
    return [uidata, cache]


def _household_hosts(hosts: list[str], timeout: float) -> dict[str, list[str]]:
    selected: dict[str, list[str]] = {}
    if hosts:
        for host in hosts:
            selected.setdefault(household_id(host), []).append(host)
        return selected
    for player in discover_players(timeout):
        selected.setdefault(player.household_id, []).append(player.host)
    if not selected:
        raise RuntimeError("No Sonos households discovered; pass --host ADDRESS")
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", action="append", default=[], help="seed player; repeat across households")
    parser.add_argument("--timeout", type=float, default=3.2, help="SSDP discovery duration")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--machine-identifier", default="", help="controller UUID; generated once if omitted")
    parser.add_argument("--mac-address", default="", help="controller MAC; current host MAC if omitted")
    parser.add_argument(
        "--synthetic-fixture",
        action="store_true",
        help="regenerate the checked-in non-personal test fixture instead of reading households",
    )
    args = parser.parse_args()
    output_root = args.output_root.expanduser().resolve()
    if args.synthetic_fixture:
        paths = generate_synthetic_fixture(output_root)
        print(json.dumps({"mode": "synthetic_fixture", "files": [str(path) for path in paths]}, indent=2))
        return

    identity = load_or_create_identity(
        output_root,
        machine_identifier=args.machine_identifier,
        mac_address=args.mac_address,
    )
    generated = []
    for current_household, hosts in _household_hosts(args.host, args.timeout).items():
        failures = []
        state = None
        for host in hosts:
            try:
                state = fetch_household_state(host)
                break
            except Exception as error:  # Continue to another player in the same household.
                failures.append(f"{host}: {error}")
        if state is None:
            raise RuntimeError(
                f"Could not read household {current_household} from any discovered player: {'; '.join(failures)}"
            )
        if state.household_id != current_household:
            raise RuntimeError(f"Player {state.source_host} changed households during generation")
        drive_c = write_household_state(output_root, state, identity)
        generated.append(
            {
                "household_id": state.household_id,
                "source_host": state.source_host,
                "alarm_count": len(state.alarms),
                "drive_c": str(drive_c),
            }
        )
    print(json.dumps({"mode": "live", "controller_identity": asdict(identity), "households": generated}, indent=2))


if __name__ == "__main__":
    main()

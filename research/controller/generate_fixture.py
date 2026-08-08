#!/usr/bin/env python3
"""Generate deterministic, non-personal controller state fixtures."""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path

VERSION = "90.0-77070"
FIXTURE_MACHINE_ID = "00000000-0000-4000-8000-000000000001"
FIXTURE_MAC = "020000000001"
FIXTURE_HOUSEHOLD = "Sonos_fixture_household"
FIXTURE_ROOM = "RINCON_00000000000101400"


def _write_xml(path: Path, root: ET.Element) -> None:
    ET.indent(root, space="  ")
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def generate(output_root: Path) -> list[Path]:
    runtime = output_root / "ProgramData/SonosV2,_Inc/runtime"

    items = ET.Element("Items")
    for name, value in (
        ("MachineIdentifier", FIXTURE_MACHINE_ID),
        ("MACAddress", FIXTURE_MAC),
    ):
        item = ET.SubElement(items, "Item")
        ET.SubElement(item, "Name").text = name
        ET.SubElement(item, "Value").text = value
    uidata = runtime / "uidata.xml"
    _write_xml(uidata, items)

    configuration = ET.Element("configuration")
    settings = ET.SubElement(configuration, "appSettings")
    synthetic_settings = {
        "searchHistoryEnabled": "True",
        "staleSessionEnabled": "True",
        "business::global.usageContext.value": "CONSUMER",
        f"{FIXTURE_HOUSEHOLD}.fixture.alarms": json.dumps(
            {"alarms": [], "updateID": FIXTURE_ROOM}, separators=(",", ":")
        ),
        "favoritesHidden": "False",
    }
    for key, value in synthetic_settings.items():
        ET.SubElement(settings, "add", key=key, value=value)
    cache = runtime / "sonos_application_cache.config"
    _write_xml(cache, configuration)
    return [uidata, cache]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(__file__).resolve().parent / VERSION / "fixture/drive_c",
    )
    args = parser.parse_args()
    for path in generate(args.output_root.expanduser().resolve()):
        print(path)


if __name__ == "__main__":
    main()

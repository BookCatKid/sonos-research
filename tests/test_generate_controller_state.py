from __future__ import annotations

import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from research.controller.generate_controller_state import (
    ControllerIdentity,
    HouseholdState,
    fetch_household_state,
    load_or_create_identity,
    write_household_state,
)

HOUSEHOLD = b"""<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>
<Response><CurrentHouseholdID>Sonos_household</CurrentHouseholdID></Response></s:Body></s:Envelope>"""
ATTRIBUTES = b"""<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>
<Response><CurrentMuseHouseholdId>Sonos_household.MuseCacheId</CurrentMuseHouseholdId></Response>
</s:Body></s:Envelope>"""
ALARMS = b"""<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><Response>
<CurrentAlarmList>&lt;Alarms&gt;&lt;Alarm ID="7" StartTime="06:30:00" Duration="01:00:00"
Recurrence="DAILY" Enabled="1" RoomUUID="RINCON_ROOM" ProgramURI="x-test:station"
ProgramMetaData="metadata" PlayMode="SHUFFLE_NOREPEAT" Volume="19" IncludeLinkedZones="0"/&gt;
&lt;/Alarms&gt;</CurrentAlarmList><CurrentAlarmListVersion>RINCON_ROOM:42</CurrentAlarmListVersion>
</Response></s:Body></s:Envelope>"""


class ControllerStateGeneratorTests(unittest.TestCase):
    @patch("research.controller.generate_controller_state.local_soap")
    def test_live_household_uses_official_cache_contract(self, soap) -> None:
        soap.side_effect = [HOUSEHOLD, ATTRIBUTES, ALARMS]

        state = fetch_household_state("192.0.2.1")

        self.assertEqual(state.alarm_cache_key, "Sonos_household.MuseCacheIdalarms")
        self.assertEqual(state.alarm_update_id, "RINCON_ROOM")
        self.assertEqual(
            state.alarms[0],
            {
                "ID": 7,
                "duration": "01:00:00",
                "enabled": True,
                "includeLinkedZones": False,
                "playMode": "SHUFFLE_NOREPEAT",
                "program": "x-test:station",
                "programMetadata": "metadata",
                "recurrence": "DAILY",
                "roomUUID": "RINCON_ROOM",
                "time": "06:30:00",
                "volume": 19,
            },
        )

    def test_controller_identity_is_generated_once_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = load_or_create_identity(
                root,
                machine_identifier="12345678-1234-4234-8234-123456789abc",
                mac_address="02:00:00:00:00:02",
            )
            second = load_or_create_identity(root)

        self.assertEqual(first, second)
        self.assertEqual(second.mac_address, "020000000002")

    def test_each_household_gets_a_separate_accurate_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "fixture"
            for relative in (
                Path("ProgramData/SonosV2,_Inc/anacapa/conf/anacapa.conf"),
                Path("Program Files (x86)/SonosV2/Sonos.Controller.Desktop.dll.config"),
            ):
                source = fixture / relative
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text("fixture", encoding="utf-8")
            state = HouseholdState(
                "Sonos_household",
                "Sonos_household.MuseCacheId",
                "RINCON_ROOM:42",
                [],
                "192.0.2.1",
            )
            drive_c = write_household_state(
                root,
                state,
                ControllerIdentity("controller-id", "020000000002"),
                fixture_root=fixture,
            )
            cache = drive_c / "ProgramData/SonosV2,_Inc/runtime/sonos_application_cache.config"
            values = {
                node.attrib["key"]: node.attrib["value"]
                for node in ET.parse(cache).getroot().iter("add")
            }
            payload = json.loads(values["Sonos_household.MuseCacheIdalarms"])

        self.assertEqual(payload, {"alarms": [], "updateID": "RINCON_ROOM"})


if __name__ == "__main__":
    unittest.main()

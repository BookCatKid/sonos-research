from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smapi_browser import Account, Service
from sonos_system_inspector import (
    account_inventory,
    action_risk,
    fetch_player_path,
    generated_controller_root,
    inspect_local_controller,
    inspect_player,
    parse_device_description,
    parse_scpd,
    parse_zone_group_state,
    soap_values,
    write_private,
)

DEVICE_XML = b"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0"><device>
  <deviceType>urn:schemas-upnp-org:device:ZonePlayer:1</deviceType>
  <friendlyName>Test Room</friendlyName><modelName>Test Speaker</modelName>
  <softwareVersion>1.2-345</softwareVersion><UDN>uuid:RINCON_TEST</UDN>
  <serviceList>
    <service><serviceType>urn:schemas-upnp-org:service:DeviceProperties:1</serviceType>
      <serviceId>urn:upnp-org:serviceId:DeviceProperties</serviceId>
      <controlURL>/DeviceProperties/Control</controlURL>
      <eventSubURL>/DeviceProperties/Event</eventSubURL>
      <SCPDURL>/xml/DeviceProperties1.xml</SCPDURL></service>
    <service><serviceType>urn:schemas-upnp-org:service:GroupRenderingControl:1</serviceType>
      <serviceId>urn:upnp-org:serviceId:GroupRenderingControl</serviceId>
      <controlURL>/GroupRenderingControl/Control</controlURL>
      <eventSubURL>/GroupRenderingControl/Event</eventSubURL>
      <SCPDURL>/xml/GroupRenderingControl1.xml</SCPDURL></service>
  </serviceList>
</device></root>"""

DEVICE_SCPD = b"""<scpd xmlns="urn:schemas-upnp-org:service-1-0"><actionList>
  <action><name>GetHouseholdID</name><argumentList>
    <argument><name>CurrentHouseholdID</name><direction>out</direction><relatedStateVariable>HouseholdID</relatedStateVariable></argument>
  </argumentList></action>
  <action><name>SetZoneAttributes</name><argumentList>
    <argument><name>DesiredZoneName</name><direction>in</direction><relatedStateVariable>ZoneName</relatedStateVariable></argument>
  </argumentList></action>
</actionList><serviceStateTable>
  <stateVariable sendEvents="yes"><name>HouseholdID</name><dataType>string</dataType></stateVariable>
</serviceStateTable></scpd>"""

GROUP_SCPD = b"""<scpd xmlns="urn:schemas-upnp-org:service-1-0"><actionList>
  <action><name>GetGroupVolume</name><argumentList>
    <argument><name>InstanceID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_InstanceID</relatedStateVariable></argument>
    <argument><name>CurrentVolume</name><direction>out</direction><relatedStateVariable>Volume</relatedStateVariable></argument>
  </argumentList></action>
</actionList></scpd>"""

HOUSEHOLD_RESPONSE = b"""<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
<s:Body><u:GetHouseholdIDResponse xmlns:u="urn:schemas-upnp-org:service:DeviceProperties:1">
<CurrentHouseholdID>Sonos_test</CurrentHouseholdID></u:GetHouseholdIDResponse></s:Body></s:Envelope>"""


class InspectorTests(unittest.TestCase):
    def test_device_and_scpd_parsing(self) -> None:
        device, services = parse_device_description(DEVICE_XML)
        self.assertEqual(device["friendlyName"], "Test Room")
        self.assertEqual(len(services), 2)
        scpd = parse_scpd(DEVICE_SCPD)
        self.assertEqual([row["name"] for row in scpd["actions"]], ["GetHouseholdID", "SetZoneAttributes"])
        self.assertEqual(scpd["actions"][0]["risk"], "read")
        self.assertEqual(scpd["actions"][1]["risk"], "mutation")

    def test_action_risk_is_conservative(self) -> None:
        self.assertEqual(action_risk("GetZoneInfo"), "read")
        self.assertEqual(action_risk("FactoryReset"), "mutation")
        self.assertEqual(action_risk("BecomeCoordinatorOfStandaloneGroup"), "mutation")
        self.assertEqual(action_risk("MagicVendorOperation"), "unknown")

    def test_embedded_topology_parsing(self) -> None:
        topology = parse_zone_group_state(
            '<ZoneGroupState><ZoneGroups><ZoneGroup Coordinator="RINCON_A" ID="g1">'
            '<ZoneGroupMember UUID="RINCON_A" ZoneName="Kitchen" '
            'Location="http://192.0.2.1:1400/xml/device_description.xml">'
            '<Satellite UUID="RINCON_B" ZoneName="Sub"/></ZoneGroupMember>'
            "</ZoneGroup></ZoneGroups></ZoneGroupState>"
        )
        self.assertEqual(topology["group_count"], 1)
        self.assertEqual(topology["member_count"], 1)
        self.assertEqual(topology["groups"][0]["members"][0]["satellites"][0]["ZoneName"], "Sub")

    def test_soap_values_redacts_credentials(self) -> None:
        response = b"""<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>
        <Response><Token>secret</Token><Name>safe</Name></Response></s:Body></s:Envelope>"""
        values = soap_values(response)
        self.assertEqual(values["Name"], "safe")
        self.assertEqual(values["Token"], {"redacted": True, "present": True, "length": 6})

    def test_inspection_never_calls_mutations_or_noncoordinator_group_reads(self) -> None:
        calls: list[str] = []

        def fake_fetch(_host: str, path: str, timeout: float = 8.0) -> bytes:
            if path.endswith("device_description.xml"):
                return DEVICE_XML
            if path.endswith("DeviceProperties1.xml"):
                return DEVICE_SCPD
            if path.endswith("GroupRenderingControl1.xml"):
                return GROUP_SCPD
            raise AssertionError(path)

        def fake_soap(_host: str, _path: str, _service: str, action: str, _fields: dict[str, str]) -> bytes:
            calls.append(action)
            if action == "GetHouseholdID":
                return HOUSEHOLD_RESPONSE
            raise AssertionError(f"unexpected action {action}")

        with patch("sonos_system_inspector.fetch_player_path", side_effect=fake_fetch), patch(
            "sonos_system_inspector.local_soap", side_effect=fake_soap
        ):
            result = inspect_player("192.0.2.1", allow_group_reads=False)
        self.assertEqual(calls, ["GetHouseholdID"])
        self.assertNotIn("SetZoneAttributes", result["reads"])
        self.assertNotIn("GetGroupVolume", result["reads"])

    def test_player_fetch_rejects_absolute_scpd_url(self) -> None:
        with patch("sonos_system_inspector.urllib.request.build_opener") as opener:
            with self.assertRaises(ValueError):
                fetch_player_path("192.0.2.1", "https://attacker.invalid/scpd.xml")
        opener.assert_not_called()

    def test_player_fetch_rejects_encoded_traversal(self) -> None:
        with patch("sonos_system_inspector.urllib.request.build_opener") as opener:
            with self.assertRaises(ValueError):
                fetch_player_path("192.0.2.1", "/xml/%2e%2e/private")
        opener.assert_not_called()

    def test_special_account_without_numeric_uid_does_not_abort_inventory(self) -> None:
        service = Service(235, "Special", "https://example.test/smapi", "Anonymous", 0, {})
        account = Account(235, 0, "SA_RINCON60167_", nickname="Special")
        with patch("sonos_system_inspector.inventory", return_value=({235: service}, [account])):
            result = account_inventory("192.0.2.1", "Sonos_test")
        self.assertEqual(result["configured_account_count"], 1)
        self.assertIsNone(result["accounts"][0]["account_uid"])

    def test_private_output_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            write_private(path, json.dumps({"ok": True}))
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, 0o600)

    def test_fresh_clone_does_not_require_local_controller_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "not-installed"
            result = inspect_local_controller(missing, missing, missing)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["files"], {})
        self.assertEqual(result["hidden_surfaces"], {})
        self.assertIn("LAN", result["message"])

    def test_default_research_bundle_is_self_contained(self) -> None:
        result = inspect_local_controller()
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["sources"]["installed_controller"]["kind"], "repository_fixture")
        self.assertTrue(result["files"]["application_cache"]["exists"])
        self.assertTrue(result["files"]["controller_identity"]["values_redacted"])
        self.assertIn("native_constants", result["hidden_surfaces"])

    def test_generated_controller_root_is_selected_per_household(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            drive_c = root / "Sonos_household" / "drive_c"
            drive_c.mkdir(parents=True)
            with patch("sonos_system_inspector.GENERATED_CONTROLLER_STATES", root):
                selected = generated_controller_root("Sonos_household")
        self.assertEqual(selected, drive_c)

    def test_explicit_interop_root_is_used_instead_of_a_temp_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sclib.cs").write_text(
                "SCISETTING_CONTENT_DEBUG_LOG_REQUEST SC_ACTIONID_DEBUG_FETCH_SERVICE_OUTAGES",
                encoding="utf-8",
            )
            (root / "SCITokenManager.cs").write_text("DEFAULT_USER_PURPOSE", encoding="utf-8")
            result = inspect_local_controller(interop_root=root)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["sources"]["decompiled_interop"]["kind"], "user_supplied")
        self.assertEqual(
            result["hidden_surfaces"]["native_constants"]["debug_actions"],
            ["SC_ACTIONID_DEBUG_FETCH_SERVICE_OUTAGES"],
        )
        self.assertEqual(
            result["hidden_surfaces"]["first_party_identity"]["token_purposes"],
            ["DEFAULT_USER_PURPOSE"],
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import copy
import unittest

from sonos_system_diff import compare_reports


BASE = {
    "discovery": {"household_id": "Sonos_A"},
    "topology": {"groups": [{"members": [{"UUID": "RINCON_A"}]}]},
    "players": [
        {
            "host": "192.0.2.1",
            "device": {"UDN": "uuid:RINCON_A", "modelName": "One", "softwareVersion": "1"},
            "reads": {"GetZoneAttributes": {"CurrentZoneName": "Room"}},
        }
    ],
    "music": {
        "accounts": [
            {
                "service_id": 1,
                "account_uid": "00000001",
                "serial": 1,
                "nickname": "Me",
                "tier": "premium",
                "credential_state": {"token_present": True},
                "schema_revision": 7,
            }
        ],
        "services": [{"id": 1, "name": "Service", "auth": "AppLink", "capabilities": 1}],
    },
    "capabilities": {"actions": [{"service_type": "svc:1", "action": "GetThing"}]},
}


class DiffTests(unittest.TestCase):
    def test_identical_reports_have_no_changes(self) -> None:
        self.assertFalse(compare_reports(BASE, copy.deepcopy(BASE))["has_changes"])

    def test_material_changes_are_classified(self) -> None:
        after = copy.deepcopy(BASE)
        after["players"][0]["device"]["softwareVersion"] = "2"
        after["music"]["accounts"][0]["credential_state"] = {"token_present": False}
        after["capabilities"]["actions"].append({"service_type": "svc:1", "action": "NewThing"})
        after["topology"]["groups"].append({"members": [{"UUID": "RINCON_B"}]})
        result = compare_reports(BASE, after)
        self.assertTrue(result["has_changes"])
        self.assertEqual(result["players"]["changed"][0]["fields"]["softwareVersion"]["after"], "2")
        self.assertEqual(result["music_accounts"]["changed"][0]["id"], "1:00000001")
        self.assertEqual(result["capabilities"]["added"], ["svc:1#NewThing"])
        self.assertTrue(result["topology"])

    def test_satellite_bond_change_is_a_topology_change(self) -> None:
        before = copy.deepcopy(BASE)
        after = copy.deepcopy(BASE)
        before["topology"]["groups"][0]["coordinator"] = "RINCON_A"
        after["topology"]["groups"][0]["coordinator"] = "RINCON_A"
        after["topology"]["groups"][0]["members"][0]["satellites"] = [
            {"UUID": "RINCON_SUB"}
        ]
        self.assertTrue(compare_reports(before, after)["topology"])


if __name__ == "__main__":
    unittest.main()

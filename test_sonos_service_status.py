from __future__ import annotations

import unittest
from unittest.mock import patch

import sonos_service_status


class ServiceStatusTests(unittest.TestCase):
    @patch("sonos_service_status._get")
    def test_current_status_uses_one_consistent_summary(self, get) -> None:
        get.return_value = {
            "page": {"name": "Sonos"},
            "status": {"indicator": "minor"},
            "components": [
                {"name": "Music Services", "status": "degraded_performance"},
                {"name": "Radio", "status": "operational"},
            ],
            "incidents": [{"name": "Provider interruption"}],
        }

        result = sonos_service_status.current_status()

        get.assert_called_once_with("summary")
        self.assertEqual(result["page"], {"name": "Sonos"})
        self.assertEqual(result["status"], {"indicator": "minor"})
        self.assertEqual(result["degraded_components"], [
            {"name": "Music Services", "status": "degraded_performance"}
        ])
        self.assertEqual(result["unresolved_incidents"], [{"name": "Provider interruption"}])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from unittest.mock import patch

from sonos_discovery import DiscoveredPlayer, discover_one, parse_ssdp_headers


class DiscoveryTests(unittest.TestCase):
    def test_header_parser_is_case_insensitive(self) -> None:
        packet = (
            b"HTTP/1.1 200 OK\r\nLOCATION: http://192.0.2.1:1400/xml/device_description.xml\r\n"
            b"X-RINCON-HOUSEHOLD: Sonos_test\r\n\r\n"
        )
        headers = parse_ssdp_headers(packet)
        self.assertEqual(headers["location"], "http://192.0.2.1:1400/xml/device_description.xml")
        self.assertEqual(headers["x-rincon-household"], "Sonos_test")

    def test_discover_one_filters_requested_host(self) -> None:
        players = [
            DiscoveredPlayer("192.0.2.1", "Sonos_A", "http://192.0.2.1:1400/xml/device_description.xml"),
            DiscoveredPlayer("192.0.2.2", "Sonos_B", "http://192.0.2.2:1400/xml/device_description.xml"),
        ]
        with patch("sonos_discovery.discover_players", return_value=players):
            self.assertEqual(discover_one(requested_host="192.0.2.2"), ("192.0.2.2", "Sonos_B"))


if __name__ == "__main__":
    unittest.main()

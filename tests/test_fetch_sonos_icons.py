from __future__ import annotations

import unittest
from unittest.mock import patch

from fetch_sonos_icons import (
    CatalogEntry,
    ServiceDescriptor,
    collect_icons,
    fetch_catalog,
    icon_cache_path,
    list_service_descriptors,
    manifest_uuid,
    parse_catalog,
    resolve_placement,
)

CATALOG_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<images>
<sized>
<service id="75271"><image lastModified="17:46:56 13 Nov 2024" placement="square:x-small">https://integration-image-assets.ws.sonos.com/app.rjsonos/36f7f1a7-d101-4968-8135-12ca14a13105/75271-20x20.png</image><image lastModified="17:46:56 13 Nov 2024" placement="square:medium">https://integration-image-assets.ws.sonos.com/app.rjsonos/36f7f1a7-d101-4968-8135-12ca14a13105/75271-80x80.png</image></service>
<service id="52231"><image lastModified="14:49:19 8 Jul 2026" placement="square">https://integration-image-assets.ws.sonos.com/com.apple.sonos-music/898f9431-e65a-4b1b-a78b-b3a2c218bfcd/iconsvg-applemusic_servicelogo_400_507.svg</image></service>
<service id="60679"><image lastModified="17:47:25 13 Nov 2024" placement="square:small">https://integration-image-assets.ws.sonos.com/au.com.storeplay.api.sonos/7d769b7e-9df6-4843-9b34-39ba5f85920f/40x40.png</image></service>
</sized>
<presentationmap>
<service id="60679"><image lastModified="17:47:25 13 Nov 2024" placement="AttributionFullLogo">https://integration-image-assets.ws.sonos.com/au.com.storeplay.api.sonos/7d769b7e-9df6-4843-9b34-39ba5f85920f/full.png</image><image lastModified="17:47:25 13 Nov 2024" placement="BrandLogo-v2">https://integration-image-assets.ws.sonos.com/au.com.storeplay.api.sonos/7d769b7e-9df6-4843-9b34-39ba5f85920f/brand.png</image></service>
</presentationmap>
</images>
"""

SOAP_RESPONSE = b"""<?xml version="1.0" encoding="utf-8"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
<s:Body><u:ListAvailableServicesResponse xmlns:u="urn:schemas-upnp-org:service:MusicServices:1">
<AvailableServiceDescriptorList>&lt;Services SchemaVersion=&quot;1&quot;&gt;
&lt;Service Id=&quot;294&quot; Name=&quot;Radio Javan&quot; Version=&quot;1.1&quot; ContainerType=&quot;MService&quot; Capabilities=&quot;29889025&quot;&gt;
&lt;Policy Auth=&quot;DeviceLink&quot; PollInterval=&quot;60&quot; /&gt;
&lt;Manifest Version=&quot;258&quot; Uri=&quot;https://cf.ws.sonos.com/p/m/36f7f1a7-d101-4968-8135-12ca14a13105&quot; /&gt;
&lt;/Service&gt;
&lt;Service Id=&quot;500&quot; Name=&quot;Sonos Backgrounds&quot; Version=&quot;1.1&quot; ContainerType=&quot;Preload&quot; Capabilities=&quot;0&quot;&gt;
&lt;Manifest Version=&quot;1&quot; Uri=&quot;https://cf.ws.sonos.com/p/m/00000000-0000-4000-8000-000000000001&quot; /&gt;
&lt;/Service&gt;
&lt;/Services&gt;</AvailableServiceDescriptorList>
</u:ListAvailableServicesResponse></s:Body></s:Envelope>
"""


class ParseCatalogTests(unittest.TestCase):
    def test_entries_are_keyed_by_manifest_uuid(self) -> None:
        catalog = parse_catalog(CATALOG_XML)
        entry = catalog["36f7f1a7-d101-4968-8135-12ca14a13105"]
        self.assertEqual(entry.catalog_id, "75271")
        self.assertIn("square:medium", entry.placements)
        self.assertEqual(
            entry.placements["square:medium"],
            "https://integration-image-assets.ws.sonos.com/app.rjsonos/"
            "36f7f1a7-d101-4968-8135-12ca14a13105/75271-80x80.png",
        )

    def test_sections_merge_by_catalog_id(self) -> None:
        catalog = parse_catalog(CATALOG_XML)
        entry = catalog["7d769b7e-9df6-4843-9b34-39ba5f85920f"]
        self.assertEqual(entry.catalog_id, "60679")
        # ``sized`` and ``presentationmap`` sections both carry id 60679 and
        # the parser merges them into a single entry.
        self.assertIn("square:small", entry.placements)
        self.assertIn("AttributionFullLogo", entry.placements)
        self.assertIn("BrandLogo-v2", entry.placements)

    def test_svg_url_keeps_extension_in_cache_path(self) -> None:
        entry = parse_catalog(CATALOG_XML)["898f9431-e65a-4b1b-a78b-b3a2c218bfcd"]
        placement = resolve_placement(entry, "square:medium")
        self.assertEqual(placement, "square")
        image_url = entry.placements[placement]
        self.assertTrue(image_url.endswith(".svg"))
        self.assertTrue(
            icon_cache_path("logocache", entry.catalog_id, placement, image_url).endswith(".svg")
        )


class ResolvePlacementTests(unittest.TestCase):
    def test_exact_placement_is_preferred(self) -> None:
        entry = CatalogEntry("1", {"uuid"}, {"square:medium": "a.png", "square": "b.png"})
        self.assertEqual(resolve_placement(entry, "square:medium"), "square:medium")

    def test_missing_placement_falls_back(self) -> None:
        entry = CatalogEntry("1", {"uuid"}, {"square": "b.png"})
        self.assertEqual(resolve_placement(entry, "square:medium"), "square")

    def test_empty_entry_returns_empty(self) -> None:
        self.assertEqual(resolve_placement(CatalogEntry("1", set(), {}), "square:medium"), "")


class ManifestUuidTests(unittest.TestCase):
    def test_extracts_uuid_from_manifest_uri(self) -> None:
        self.assertEqual(
            manifest_uuid("https://cf.ws.sonos.com/p/m/36f7f1a7-d101-4968-8135-12ca14a13105"),
            "36f7f1a7-d101-4968-8135-12ca14a13105",
        )

    def test_non_manifest_uri_returns_empty(self) -> None:
        self.assertEqual(manifest_uuid("https://example.invalid/no/uuid"), "")


class ListServiceDescriptorsTests(unittest.TestCase):
    @patch("fetch_sonos_icons.http.client.HTTPConnection")
    def test_parses_manifest_uri_and_attributes(self, connection) -> None:
        response = connection.return_value.getresponse.return_value
        response.status = 200
        response.read.return_value = SOAP_RESPONSE

        services = list_service_descriptors("192.0.2.10")

        self.assertEqual(
            services,
            [
                ServiceDescriptor(
                    service_id=294,
                    name="Radio Javan",
                    container_type="MService",
                    manifest_uri="https://cf.ws.sonos.com/p/m/36f7f1a7-d101-4968-8135-12ca14a13105",
                ),
                ServiceDescriptor(
                    service_id=500,
                    name="Sonos Backgrounds",
                    container_type="Preload",
                    manifest_uri="https://cf.ws.sonos.com/p/m/00000000-0000-4000-8000-000000000001",
                ),
            ],
        )

    @patch("fetch_sonos_icons.http.client.HTTPConnection")
    def test_raises_on_http_error(self, connection) -> None:
        response = connection.return_value.getresponse.return_value
        response.status = 500
        response.read.return_value = b""

        with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
            list_service_descriptors("192.0.2.10")


class CollectIconsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = parse_catalog(CATALOG_XML)
        self.services = [
            ServiceDescriptor(
                294,
                "Radio Javan",
                "MService",
                "https://cf.ws.sonos.com/p/m/36f7f1a7-d101-4968-8135-12ca14a13105",
            ),
            ServiceDescriptor(500, "Sonos Backgrounds", "Preload", "https://example.invalid/x"),
        ]

    @patch("fetch_sonos_icons.download_icon", return_value=True)
    def test_matches_by_manifest_uuid(self, download_icon) -> None:
        rows = collect_icons(self.services, self.catalog, cache_dir="logocache")
        matched = [row for row in rows if row["matched"]]
        self.assertEqual([row["service_id"] for row in matched], [294])
        self.assertEqual(matched[0]["catalog_id"], "75271")
        self.assertTrue(matched[0]["downloaded"])

    @patch("fetch_sonos_icons.download_icon", return_value=True)
    def test_unmatched_service_reports_no_icon(self, download_icon) -> None:
        rows = collect_icons(self.services, self.catalog, cache_dir="logocache")
        backgrounds = next(row for row in rows if row["service_id"] == 500)
        self.assertFalse(backgrounds["matched"])
        self.assertEqual(backgrounds["icon_url"], "")

    @patch("fetch_sonos_icons.download_icon")
    def test_no_download_keeps_url_only(self, download_icon) -> None:
        rows = collect_icons(self.services, self.catalog, cache_dir="logocache", download=False)
        self.assertFalse(download_icon.called)
        self.assertEqual(rows[0]["icon_url"].endswith("75271-80x80.png"), True)


class FetchCatalogTests(unittest.TestCase):
    @patch("fetch_sonos_icons.urllib.request.urlopen")
    def test_follows_redirect_to_catalog(self, urlopen) -> None:
        urlopen.return_value.__enter__.return_value.read.return_value = CATALOG_XML
        payload = fetch_catalog("http://update-services.sonos.com/services/mslogo.xml")
        self.assertEqual(payload, CATALOG_XML)
        urlopen.assert_called_once()


if __name__ == "__main__":
    unittest.main()

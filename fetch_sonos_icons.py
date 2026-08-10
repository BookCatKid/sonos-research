#!/usr/bin/env python3
"""Fetch music-service icons using the desktop controller's logo pipeline.

The official desktop controller does not receive icons from the player's
``ListAvailableServices`` descriptor.  Instead its native core (``RMSLogoMgr``)
downloads Sonos's public music-service logo catalog and caches the icons
locally before the UI asks for them via ``SCILogoArtworkCache``.

This tool recreates that path:

1. Fetch the catalog.  The controller resolves ``MSLogoIndexLocation``, which
   defaults to ``http://update-services.sonos.com/services/mslogo.xml`` and
   permanently redirects to ``https://service-catalog.ws.sonos.com/mslogo``.
   The catalog is an ``<images>`` document whose ``<sized>`` and
   ``<presentationmap>`` sections list ``<service id="...">`` entries, each
   carrying ``<image placement="...">https://integration-image-assets.ws.sonos.com/<slug>/<uuid>/<file></image>``
   URLs for several placements (square sizes plus brand logos).

2. Bridge to household services.  Every ``ListAvailableServices`` descriptor
   carries ``<Manifest Uri="https://cf.ws.sonos.com/p/m/<uuid>">`` and the
   catalog image URLs embed the very same UUID, so each household service maps
   to exactly one catalog entry.

3. Download and cache.  Icons are stored under ``<cache-dir>/logos/`` as
   ``<catalog-id>-<placement><ext>`` (``ext`` preserved from the catalog URL,
   usually ``.png`` but occasionally ``.svg``), mirroring the controller's
   ``logocache`` layout.  Services missing from the catalog fall back to the
   app's bundled ``icn_missing_music_service`` placeholder (not shipped here).
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from list_music_services import discover

MUSIC_SERVICES = "urn:schemas-upnp-org:service:MusicServices:1"
CATALOG_URL = "http://update-services.sonos.com/services/mslogo.xml"
CACHE_SUBDIR = "logos"
MANIFEST_UUID_RE = re.compile(r"/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")

# Placement shorthands that mirror the controller's SCLogoArtSize ladder.
PLACEMENT_SIZES = {
    "small": "square:small",
    "medium": "square:medium",
    "large": "square:large",
    "x-large": "square:x-large",
}


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


@dataclass
class CatalogEntry:
    """One catalog service: its icon URLs per placement."""

    catalog_id: str
    manifest_uuids: set[str]
    placements: dict[str, str] = field(default_factory=dict)


def fetch_catalog(url: str = CATALOG_URL, timeout: float = 20.0) -> bytes:
    """Download the mslogo catalog, following the permanent redirect."""
    request = urllib.request.Request(url, headers={"Accept": "application/xml"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def parse_catalog(payload: bytes) -> dict[str, CatalogEntry]:
    """Parse the mslogo catalog into a UUID -> CatalogEntry map.

    The catalog's sections ``<sized>`` and ``<presentationmap>`` both list
    ``<service id="...">`` entries with ``<image placement="...">URL</image>``
    children.  Entries with the same id across sections are merged, and each
    entry is keyed by the manifest UUID embedded in its image URLs.
    """
    root = ET.fromstring(payload)
    merged: dict[str, CatalogEntry] = {}
    for section in root:
        for service in section:
            if local_name(service.tag) != "service":
                continue
            catalog_id = service.attrib.get("id", "")
            entry = merged.get(catalog_id, CatalogEntry(catalog_id=catalog_id, manifest_uuids=set()))
            for image in service:
                if local_name(image.tag) != "image":
                    continue
                placement = image.attrib.get("placement", "")
                url = (image.text or "").strip()
                if not placement or not url:
                    continue
                if placement not in entry.placements:
                    entry.placements[placement] = url
                match = re.search(r"/([0-9a-f]{8}-[0-9a-f-]{27})/", url)
                if match:
                    entry.manifest_uuids.add(match.group(1))
            merged[catalog_id] = entry
    by_uuid: dict[str, CatalogEntry] = {}
    for entry in merged.values():
        for uuid in entry.manifest_uuids:
            by_uuid.setdefault(uuid, entry)
    return by_uuid


@dataclass(frozen=True)
class ServiceDescriptor:
    service_id: int
    name: str
    container_type: str
    manifest_uri: str


def list_service_descriptors(host: str, timeout: float = 8.0) -> list[ServiceDescriptor]:
    """Call ListAvailableServices and return the full descriptors, including
    the ``<Manifest Uri=...>`` child element used to bridge to the catalog."""
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'<s:Body><u:ListAvailableServices xmlns:u="{MUSIC_SERVICES}"/>'
        "</s:Body></s:Envelope>"
    ).encode("utf-8")
    connection = http.client.HTTPConnection(host, 1400, timeout=timeout)
    try:
        connection.request(
            "POST",
            "/MusicServices/Control",
            body=body,
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPACTION": f'"{MUSIC_SERVICES}#ListAvailableServices"',
            },
        )
        response = connection.getresponse()
        status = response.status
        result = response.read()
    finally:
        connection.close()
    if status != 200:
        raise RuntimeError(f"ListAvailableServices failed with HTTP {status}")

    outer = ET.fromstring(result)
    descriptor = ""
    for node in outer.iter():
        if local_name(node.tag) == "AvailableServiceDescriptorList":
            descriptor = "".join(node.itertext())
            break
    if not descriptor:
        return []
    catalog_root = ET.fromstring(descriptor)

    services: list[ServiceDescriptor] = []
    for service in catalog_root.iter():
        if local_name(service.tag) != "Service":
            continue
        manifest_uri = ""
        for child in service:
            if local_name(child.tag) == "Manifest":
                manifest_uri = child.attrib.get("Uri", "")
                break
        services.append(
            ServiceDescriptor(
                service_id=int(service.attrib.get("Id", "0") or 0),
                name=service.attrib.get("Name", ""),
                container_type=service.attrib.get("ContainerType", ""),
                manifest_uri=manifest_uri,
            )
        )
    return services


def manifest_uuid(manifest_uri: str) -> str:
    match = MANIFEST_UUID_RE.search(manifest_uri)
    return match.group(1) if match else ""


def resolve_placement(entry: CatalogEntry, placement: str) -> str:
    """Pick the closest available placement for the requested one."""
    if placement in entry.placements:
        return placement
    preferred = ["square:medium", "square", "square:small", "square:large", "square:x-small"]
    for candidate in preferred:
        if candidate in entry.placements:
            return candidate
    return next(iter(entry.placements), "")


def download_icon(url: str, destination: str, timeout: float = 20.0) -> bool:
    """Download one icon to ``destination``; returns False on failure."""
    try:
        request = urllib.request.Request(url, headers={"Accept": "image/*"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except Exception:
        return False
    if not payload:
        return False
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    with open(destination, "wb") as handle:
        handle.write(payload)
    return True


def icon_cache_path(cache_dir: str, catalog_id: str, placement: str, url: str) -> str:
    """Cache path for one icon.  The controller writes ``%s/logos/<id>.png``,
    but catalog entries sometimes point at SVG assets, so keep the URL's real
    extension instead of forcing ``.png``."""
    extension = os.path.splitext(urllib.parse.urlsplit(url).path)[1] or ".png"
    return os.path.join(cache_dir, CACHE_SUBDIR, f"{catalog_id}-{placement}{extension}")


def collect_icons(
    services: list[ServiceDescriptor],
    catalog: dict[str, CatalogEntry],
    *,
    placement: str = "square:medium",
    cache_dir: str,
    download: bool = True,
) -> list[dict[str, Any]]:
    """Match household services to catalog entries and (optionally) fetch icons."""
    rows: list[dict[str, Any]] = []
    for service in services:
        uuid = manifest_uuid(service.manifest_uri)
        entry = catalog.get(uuid) if uuid else None
        row: dict[str, Any] = {
            "service_id": service.service_id,
            "name": service.name,
            "container_type": service.container_type,
            "manifest_uuid": uuid,
            "catalog_id": entry.catalog_id if entry else "",
            "icon_url": "",
            "icon_path": "",
            "placement": "",
            "matched": entry is not None,
        }
        if entry:
            resolved = resolve_placement(entry, placement)
            url = entry.placements.get(resolved, "")
            row["icon_url"] = url
            row["placement"] = resolved
            if download and url:
                path = icon_cache_path(cache_dir, entry.catalog_id, resolved, url)
                row["icon_path"] = path
                row["downloaded"] = download_icon(url, path)
        rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch music-service icons via the desktop controller's mslogo pipeline."
    )
    parser.add_argument("--host", help="Sonos player IP (skips SSDP discovery)")
    parser.add_argument("--timeout", type=float, default=8.0, help="SSDP/SOAP timeout")
    parser.add_argument("--catalog-url", default=CATALOG_URL, help="mslogo catalog URL")
    parser.add_argument(
        "--placement",
        choices=[*PLACEMENT_SIZES, "square", "square:x-small", "square:small",
                 "square:medium", "square:large", "square:x-large",
                 "AttributionFullLogo", "AttributionBrandmark",
                 "BrandLogo-v2", "BrandLogo-v2:small", "BrandLogo-v2:medium", "BrandLogo-v2:large"],
        default="square:medium",
        help="Requested icon placement (shorthands: small/medium/large/x-large)",
    )
    parser.add_argument("--cache-dir", default="logocache", help="Local icon cache directory")
    parser.add_argument("--no-download", action="store_true", help="Only build the index, skip downloads")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Print results as JSON")
    args = parser.parse_args()

    if args.host:
        host = args.host
        household = "unknown"
    else:
        host, household = discover(args.timeout)
    placement = PLACEMENT_SIZES.get(args.placement, args.placement)

    catalog = parse_catalog(fetch_catalog(args.catalog_url, args.timeout))
    services = list_service_descriptors(host, args.timeout)
    rows = collect_icons(
        services,
        catalog,
        placement=placement,
        cache_dir=args.cache_dir,
        download=not args.no_download,
    )
    matched = sum(1 for row in rows if row["matched"])
    downloaded = sum(1 for row in rows if row.get("downloaded"))

    if args.as_json:
        print(
            json.dumps(
                {
                    "speaker": host,
                    "household": household,
                    "catalog_url": args.catalog_url,
                    "placement": placement,
                    "services_total": len(rows),
                    "services_matched": matched,
                    "icons_downloaded": downloaded,
                    "icons": rows,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    print(f"Speaker: {host}  Household: {household}")
    print(f"Catalog: {args.catalog_url} -> placement {placement}")
    print(f"Services: {len(rows)} total, {matched} matched, {downloaded} icons downloaded")
    for row in rows:
        marker = "✓" if row.get("downloaded") else ("·" if row["matched"] else "✗")
        path = row["icon_path"] or row["icon_url"] or "no icon"
        print(f"  {marker} {row['service_id']:>4d}  {row['name']}")
        print(f"      {path}")
    if matched < len(rows):
        print(
            f"\n{len(rows) - matched} services have no catalog entry; "
            "the controller falls back to its bundled icn_missing_music_service icon."
        )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Discover and enumerate available Sonos music services via ListAvailableServices."""

from __future__ import annotations

import argparse
import http.client
import json
import socket
import time
import urllib.parse
import xml.etree.ElementTree as ET


SSDP_ADDRESS = ("239.255.255.250", 1900)


def parse_headers(packet: bytes) -> dict[str, str]:
    text = packet.decode("iso-8859-1", errors="replace")
    headers: dict[str, str] = {}
    for line in text.split("\r\n")[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    return headers


def discover(timeout: float = 3.0) -> tuple[str, str]:
    request = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 1\r\n"
        "ST: urn:schemas-upnp-org:device:ZonePlayer:1\r\n\r\n"
    ).encode("ascii")
    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.settimeout(0.5)
        sock.sendto(request, SSDP_ADDRESS)
        while time.monotonic() < deadline:
            try:
                packet, address = sock.recvfrom(65535)
            except socket.timeout:
                continue
            headers = parse_headers(packet)
            household = headers.get("x-rincon-household", "")
            location = headers.get("location", "")
            host = urllib.parse.urlparse(location).hostname or address[0]
            if household and host:
                return host, household
    raise RuntimeError("No Sonos SSDP response supplied X-RINCON-HOUSEHOLD")


def list_available_services(host: str) -> list[dict[str, str]]:
    service_type = "urn:schemas-upnp-org:service:MusicServices:1"
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'<s:Body><u:ListAvailableServices xmlns:u="{service_type}"/>'
        '</s:Body></s:Envelope>'
    ).encode("utf-8")
    connection = http.client.HTTPConnection(host, 1400, timeout=8)
    connection.request(
        "POST",
        "/MusicServices/Control",
        body=body,
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"{service_type}#ListAvailableServices"',
        },
    )
    response = connection.getresponse()
    result = response.read()
    connection.close()
    if response.status != 200:
        raise RuntimeError(f"ListAvailableServices failed with HTTP {response.status}")
    outer = ET.fromstring(result)
    descriptor = ""
    for node in outer.iter():
        if node.tag.rsplit("}", 1)[-1] == "AvailableServiceDescriptorList":
            descriptor = "".join(node.itertext())
            break
    if not descriptor:
        return []
    catalog_root = ET.fromstring(descriptor)
    services: list[dict[str, str]] = []
    for service in catalog_root.iter():
        if service.tag.rsplit("}", 1)[-1] != "Service":
            continue
        services.append(dict(service.attrib))
    return services


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=3, help="SSDP discovery timeout")
    parser.add_argument("--host", help="Sonos player IP (skip SSDP discovery)")
    args = parser.parse_args()

    if args.host:
        host = args.host
        household = "unknown"
    else:
        host, household = discover(args.timeout)
    print(f"Speaker: {host}")
    print(f"Household: {household}")
    print()

    services = list_available_services(host)
    print(f"Available services: {len(services)}")
    print()

    # Collect all unique attribute keys across services
    all_keys: set[str] = set()
    for svc in services:
        all_keys.update(svc.keys())

    # Preferred key order
    key_order = ["Id", "Name", "Version", "Uri", "SecureMode", "Policy",
                 "Capabilities", "ContainerType"]
    other_keys = [k for k in sorted(all_keys) if k not in key_order]
    ordered_keys = [k for k in key_order if k in all_keys] + other_keys

    for svc in services:
        print(f"  Service ID {svc.get('Id', '?'):>4s}  {svc.get('Name', '?')}")
        for key in ordered_keys:
            if key in svc:
                print(f"    {key}: {svc[key]}")
        print()

    print(json.dumps(services, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

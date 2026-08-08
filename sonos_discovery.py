#!/usr/bin/env python3
"""Resilient Sonos LAN discovery shared by standalone tools."""

from __future__ import annotations

import ipaddress
import os
import socket
import subprocess
import time
import urllib.parse
from dataclasses import dataclass


ZONE_PLAYER_TARGET = "urn:schemas-upnp-org:device:ZonePlayer:1"


@dataclass(frozen=True)
class DiscoveredPlayer:
    host: str
    household_id: str
    location: str
    server: str = ""
    boot_seq: str = ""


def parse_ssdp_headers(packet: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in packet.decode("iso-8859-1", errors="replace").split("\r\n")[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            result[name.strip().lower()] = value.strip()
    return result


def local_ipv4_addresses() -> list[str]:
    """Return usable IPv4 interface addresses without third-party packages."""
    found: set[str] = set()
    try:
        for _, interface_name in socket.if_nameindex():
            if os.uname().sysname == "Darwin":
                result = subprocess.run(
                    ["ipconfig", "getifaddr", interface_name],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                value = result.stdout.strip()
                if value:
                    found.add(value)
    except (AttributeError, OSError):
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(info[4][0])
    except socket.gaierror:
        pass
    return sorted(
        value
        for value in found
        if not ipaddress.ip_address(value).is_loopback
        and not ipaddress.ip_address(value).is_link_local
    )


def discover_players(timeout: float = 3.2) -> list[DiscoveredPlayer]:
    """Repeat multicast and limited-broadcast discovery on every IPv4 interface."""
    request = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 1\r\n"
        f"ST: {ZONE_PLAYER_TARGET}\r\n\r\n"
    ).encode("ascii")
    sockets: list[socket.socket] = []
    addresses = local_ipv4_addresses() or ["0.0.0.0"]
    try:
        for address in addresses:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setblocking(False)
            try:
                sock.bind((address, 0))
                if address != "0.0.0.0":
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(address))
            except OSError:
                sock.close()
                continue
            sockets.append(sock)
        if not sockets:
            raise RuntimeError("No usable IPv4 interface for Sonos discovery")
        found: dict[str, DiscoveredPlayer] = {}
        deadline = time.monotonic() + timeout
        next_send = 0.0
        sends = 0
        while time.monotonic() < deadline:
            now = time.monotonic()
            if sends < 3 and now >= next_send:
                for sock in sockets:
                    for target in (("239.255.255.250", 1900), ("255.255.255.255", 1900)):
                        try:
                            sock.sendto(request, target)
                        except OSError:
                            pass
                sends += 1
                next_send = now + 1.0
            for sock in sockets:
                while True:
                    try:
                        packet, sender = sock.recvfrom(65535)
                    except BlockingIOError:
                        break
                    headers = parse_ssdp_headers(packet)
                    location = headers.get("location", "")
                    host = urllib.parse.urlparse(location).hostname or sender[0]
                    household = headers.get("x-rincon-household", "")
                    if host and household:
                        found[host] = DiscoveredPlayer(
                            host=host,
                            household_id=household,
                            location=location,
                            server=headers.get("server", ""),
                            boot_seq=headers.get("x-rincon-bootseq", ""),
                        )
            time.sleep(0.02)
        return sorted(found.values(), key=lambda player: ipaddress.ip_address(player.host))
    finally:
        for sock in sockets:
            sock.close()


def discover_one(
    timeout: float = 3.2,
    *,
    requested_host: str | None = None,
) -> tuple[str, str]:
    players = discover_players(timeout)
    if requested_host:
        for player in players:
            if player.host == requested_host:
                return player.host, player.household_id
        raise RuntimeError(f"No Sonos SSDP response supplied X-RINCON-HOUSEHOLD for {requested_host}")
    if players:
        return players[0].host, players[0].household_id
    raise RuntimeError("No Sonos SSDP response supplied X-RINCON-HOUSEHOLD")

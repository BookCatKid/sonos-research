"""Desktop UI for discovering Sonos services and inspecting account metadata.

The app combines two Sonos UPnP workflows:
1. MusicServices.ListAvailableServices
2. ZoneGroupTopology ThirdPartyMediaServersX capture/decryption

Credential values are masked in the UI and exports by default. They can be
revealed locally with an explicit checkbox.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import html
import http.client
import io
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import tkinter
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tkinter import (
    BooleanVar,
    END,
    HORIZONTAL,
    LEFT,
    RIGHT,
    StringVar,
    Text,
    Tk,
    filedialog,
    messagebox,
)
from tkinter import ttk
from typing import Any, Callable

import smapi_browser as smapi

try:
    from PIL import Image as PILImage
    from PIL import ImageTk
except ImportError:  # The startup runtime check provides the actionable install command.
    PILImage = None  # type: ignore[assignment]
    ImageTk = None  # type: ignore[assignment]


SSDP_ADDRESS = ("239.255.255.250", 1900)
ZONE_PLAYER_ST = "urn:schemas-upnp-org:device:ZonePlayer:1"
ZGT_EVENT_PATH = "/ZoneGroupTopology/Event"
MUSIC_SERVICES_TYPE = "urn:schemas-upnp-org:service:MusicServices:1"
SALT = bytes.fromhex("1a01a731c96e9ebde8475182b274b70e")

BG = "#101318"
PANEL = "#171b22"
PANEL_2 = "#1f2530"
TEXT = "#eef2f7"
MUTED = "#9aa6b2"
ACCENT = "#5aa9ff"
ACCENT_HOVER = "#7bbaff"
SUCCESS = "#55d68b"
ERROR = "#ff6b6b"
BORDER = "#303846"
SELECTED = "#294d73"


@dataclass(frozen=True)
class DiscoveredPlayer:
    host: str
    household: str
    name: str
    location: str = ""

    @property
    def label(self) -> str:
        return f"{self.name} — {self.host}" if self.name else self.host


class SonosError(RuntimeError):
    """Friendly error raised for Sonos/network failures."""


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_headers(packet: bytes) -> dict[str, str]:
    text = packet.decode("iso-8859-1", errors="replace")
    headers: dict[str, str] = {}
    for line in text.split("\r\n")[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    return headers


def http_request(
    host: str,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 8,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection(host, 1400, timeout=timeout)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        response_headers = {key.lower(): value for key, value in response.getheaders()}
        return response.status, response_headers, data
    except (OSError, http.client.HTTPException) as exc:
        raise SonosError(f"Could not contact Sonos player {host}: {exc}") from exc
    finally:
        connection.close()


def get_player_name(host: str) -> str:
    try:
        status, _headers, data = http_request(
            host,
            "GET",
            "/xml/device_description.xml",
            timeout=3,
        )
        if status != 200:
            return "Sonos player"
        root = ET.fromstring(data)
        preferred = ("roomName", "friendlyName", "displayName")
        values: dict[str, str] = {}
        for node in root.iter():
            name = local_name(node.tag)
            value = (node.text or "").strip()
            if name in preferred and value:
                values[name] = value
        return next((values[key] for key in preferred if key in values), "Sonos player")
    except (ET.ParseError, SonosError):
        return "Sonos player"


def discover_players(timeout: float = 3.0) -> list[DiscoveredPlayer]:
    request = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 1\r\n"
        f"ST: {ZONE_PLAYER_ST}\r\n\r\n"
    ).encode("ascii")

    deadline = time.monotonic() + timeout
    found: dict[str, tuple[str, str]] = {}

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.35)
        try:
            sock.sendto(request, SSDP_ADDRESS)
        except OSError as exc:
            raise SonosError(f"Could not send SSDP discovery request: {exc}") from exc

        while time.monotonic() < deadline:
            try:
                packet, address = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError as exc:
                raise SonosError(f"SSDP discovery failed: {exc}") from exc

            headers = parse_headers(packet)
            household = headers.get("x-rincon-household", "")
            location = headers.get("location", "")
            host = urllib.parse.urlparse(location).hostname or address[0]
            if host and household:
                found[host] = (household, location)

    if not found:
        raise SonosError(
            "No Sonos players responded. Confirm this computer is on the same LAN "
            "and local-network access/firewall rules allow SSDP multicast."
        )

    players = [
        DiscoveredPlayer(
            host=host,
            household=household,
            name=get_player_name(host),
            location=location,
        )
        for host, (household, location) in found.items()
    ]
    return sorted(players, key=lambda player: (player.name.lower(), player.host))


def discover_matching_player(host: str, timeout: float) -> DiscoveredPlayer:
    players = discover_players(timeout)
    for player in players:
        if player.host == host:
            return player
    raise SonosError(
        f"Found Sonos players, but none matched the manually entered host {host}. "
        "Use Discover so the household ID can be filled automatically."
    )


def list_available_services(host: str) -> list[dict[str, str]]:
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'<s:Body><u:ListAvailableServices xmlns:u="{MUSIC_SERVICES_TYPE}"/>'
        "</s:Body></s:Envelope>"
    ).encode("utf-8")

    status, _headers, result = http_request(
        host,
        "POST",
        "/MusicServices/Control",
        body=body,
        headers={
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPACTION": f'"{MUSIC_SERVICES_TYPE}#ListAvailableServices"',
        },
        timeout=8,
    )
    if status != 200:
        raise SonosError(f"ListAvailableServices failed with HTTP {status}")

    try:
        outer = ET.fromstring(result)
    except ET.ParseError as exc:
        raise SonosError(f"Sonos returned invalid SOAP XML: {exc}") from exc

    descriptor = ""
    for node in outer.iter():
        if local_name(node.tag) == "AvailableServiceDescriptorList":
            descriptor = "".join(node.itertext()).strip()
            break
    if not descriptor:
        return []

    try:
        catalog_root = ET.fromstring(descriptor)
    except ET.ParseError as exc:
        raise SonosError(f"Sonos returned an invalid service catalog: {exc}") from exc

    services: list[dict[str, str]] = []
    for service in catalog_root.iter():
        if local_name(service.tag) == "Service":
            services.append(dict(service.attrib))
    return services


def service_catalog(services: list[dict[str, str]]) -> dict[str, str]:
    catalog: dict[str, str] = {}
    for service in services:
        service_id = service.get("Id") or service.get("ServiceType")
        name = service.get("Name")
        if service_id and name:
            catalog[str(service_id)] = name
    return catalog


def local_ip_for(host: str) -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((host, 1400))
            return sock.getsockname()[0]
    except OSError as exc:
        raise SonosError(f"Could not determine callback IP for {host}: {exc}") from exc


class CaptureHandler(BaseHTTPRequestHandler):
    captured: queue.Queue[str] = queue.Queue(maxsize=1)

    def do_NOTIFY(self) -> None:  # noqa: N802
        length_text = self.headers.get("Content-Length", "0")
        try:
            body = self.rfile.read(int(length_text))
            root = ET.fromstring(body)
            for node in root.iter():
                if local_name(node.tag) == "ThirdPartyMediaServersX":
                    value = "".join(node.itertext()).strip()
                    try:
                        self.captured.put_nowait(value)
                    except queue.Full:
                        pass
                    break
            self.send_response(200)
        except (ValueError, ET.ParseError):
            self.send_response(400)
        finally:
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


def subscribe(host: str, callback: str, timeout: int) -> str:
    status, headers, _data = http_request(
        host,
        "SUBSCRIBE",
        ZGT_EVENT_PATH,
        headers={
            "CALLBACK": f"<{callback}>",
            "NT": "upnp:event",
            "TIMEOUT": f"Second-{timeout}",
        },
        timeout=5,
    )
    sid = headers.get("sid", "")
    if status != 200 or not sid:
        raise SonosError(f"SUBSCRIBE failed with HTTP {status}")
    return sid


def unsubscribe(host: str, sid: str) -> None:
    status, _headers, _data = http_request(
        host,
        "UNSUBSCRIBE",
        ZGT_EVENT_PATH,
        headers={"SID": sid},
        timeout=5,
    )
    if status not in (200, 204):
        raise SonosError(f"UNSUBSCRIBE failed with HTTP {status}")


def aes_128_cbc_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    openssl = shutil.which("openssl")
    if not openssl:
        raise SonosError(
            "OpenSSL was not found. Install it or make sure the openssl command is in PATH."
        )

    result = subprocess.run(
        [openssl, "enc", "-d", "-aes-128-cbc", "-K", key.hex(), "-iv", iv.hex()],
        input=ciphertext,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        detail = f": {stderr}" if stderr else ""
        raise SonosError(f"AES-CBC decryption or PKCS#7 validation failed{detail}")
    return result.stdout


def decrypt_blob(encoded: str, household: str) -> bytes:
    encoded = html.unescape(encoded).strip()
    if not encoded.startswith("2:"):
        raise SonosError("Unsupported ThirdPartyMediaServersX version")

    try:
        raw = base64.b64decode(encoded[2:], validate=True)
    except ValueError as exc:
        raise SonosError("ThirdPartyMediaServersX contained invalid base64") from exc

    if len(raw) < 32 or len(raw[16:]) % 16:
        raise SonosError("Invalid encrypted payload dimensions")

    iv, ciphertext = raw[:16], raw[16:]
    global_key = hashlib.md5(household.encode("utf-8") + SALT).digest()  # noqa: S324
    blob_key = hashlib.md5(iv + global_key).digest()  # noqa: S324
    checked = aes_128_cbc_decrypt(ciphertext, blob_key, iv)

    if len(checked) < 4:
        raise SonosError("Decrypted payload is too short")

    payload, checksum = checked[:-4], checked[-4:]
    if hashlib.md5(payload).digest()[:4] != checksum:  # noqa: S324
        raise SonosError("Embedded MD5 checksum mismatch")
    return payload


def scalar_summary(value: Any) -> dict[str, Any]:
    if value is None or isinstance(value, (bool, int, float)):
        return {"type": type(value).__name__, "value": value}
    if isinstance(value, bytes):
        return {"type": "bytes", "length": len(value), "value": value.hex()}
    return {"type": "string", "length": len(str(value)), "value": str(value)}


def structure(value: Any, path: str = "$") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, dict):
        rows.append({"path": path, "type": "object", "keys": sorted(map(str, value.keys()))})
        for key, child in value.items():
            rows.extend(structure(child, f"{path}.{key}"))
    elif isinstance(value, list):
        rows.append({"path": path, "type": "array", "length": len(value)})
        for index, child in enumerate(value):
            rows.extend(structure(child, f"{path}[{index}]"))
    else:
        rows.append({"path": path, **scalar_summary(value)})
    return rows


def parse_payload(payload: bytes) -> tuple[str, Any]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SonosError(f"Decrypted payload is not valid UTF-8: {exc}") from exc

    try:
        return "json", json.loads(text)
    except json.JSONDecodeError:
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise SonosError(f"Decrypted payload is neither JSON nor XML: {exc}") from exc

        def xml_shape(node: ET.Element) -> dict[str, Any]:
            text_value = (node.text or "").strip()
            return {
                "tag": local_name(node.tag),
                "attributes": {
                    local_name(key): scalar_summary(value)
                    for key, value in node.attrib.items()
                },
                "text": scalar_summary(text_value) if text_value else None,
                "children": [xml_shape(child) for child in node],
            }

        return "xml", xml_shape(root)


def account_report(payload: bytes, catalog: dict[str, str]) -> dict[str, Any]:
    try:
        root = ET.fromstring(payload.decode("utf-8"))
    except (UnicodeDecodeError, ET.ParseError) as exc:
        raise SonosError(f"Could not parse account XML: {exc}") from exc

    instances: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    for instance_index, service in enumerate(root):
        attrs = service.attrib
        udn = attrs.get("UDN", "")
        match = re.match(r"^SA_RINCON(\d+)", udn)
        encoded_type = int(match.group(1)) if match else None
        service_id = str(encoded_type // 256) if encoded_type is not None else "special/local"
        schema_revision = encoded_type % 256 if encoded_type is not None else None
        counts[service_id] = counts.get(service_id, 0) + 1

        credential_fields = sorted(
            key
            for key in attrs
            if re.match(r"^(Token|Key|Username)\d+$", key) and bool(attrs[key])
        )

        serial_indexes = sorted(
            int(value)
            for key, value in attrs.items()
            if re.match(r"^SerialNum\d+$", key) and value.isdigit()
        )

        instances.append(
            {
                "instance_index": instance_index,
                "service_id": service_id,
                "service_name": catalog.get(service_id, "unmapped"),
                "udn": udn,
                "udn_schema_revision": schema_revision,
                "account_slots_declared": int(attrs.get("NumAccounts", "0") or 0),
                "serial_indexes": serial_indexes,
                "credential_fields": credential_fields,
                "credential_values": {key: attrs[key] for key in credential_fields},
                "other_attributes": {
                    key: value
                    for key, value in attrs.items()
                    if key not in credential_fields
                    and key != "UDN"
                    and not re.match(r"^SerialNum\d+$", key)
                },
            }
        )

    return {
        "instance_count": len(instances),
        "service_type_counts": counts,
        "multiple_instance_service_ids": sorted(
            service_id for service_id, count in counts.items() if count > 1
        ),
        "instances": instances,
    }


def inspect_accounts(
    host: str,
    household: str,
    catalog: dict[str, str],
    *,
    port: int = 3411,
    wait_seconds: int = 8,
) -> dict[str, Any]:
    CaptureHandler.captured = queue.Queue(maxsize=1)
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), CaptureHandler)
    except OSError as exc:
        raise SonosError(f"Could not open callback port {port}: {exc}") from exc

    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    actual_port = server.server_address[1]
    sid = ""

    try:
        callback = f"http://{local_ip_for(host)}:{actual_port}{ZGT_EVENT_PATH}"
        sid = subscribe(host, callback, wait_seconds + 10)
        try:
            encoded = CaptureHandler.captured.get(timeout=wait_seconds)
        except queue.Empty as exc:
            raise SonosError(
                f"No ThirdPartyMediaServersX event arrived within {wait_seconds} seconds."
            ) from exc

        payload = decrypt_blob(encoded, household)
        payload_type, parsed = parse_payload(payload)
        accounts = account_report(payload, catalog) if payload_type == "xml" else None
        structure_report: Any
        if payload_type == "json":
            structure_report = structure(parsed)
        else:
            structure_report = {
                "root_tag": parsed["tag"],
                "child_tags": sorted({child["tag"] for child in parsed["children"]}),
            }

        return {
            "discovery": {
                "player_ip": host,
                "household_id": household,
                "callback_port": actual_port,
            },
            "envelope": {
                "version": 2,
                "encoded_length": len(encoded),
                "decrypted_length": len(payload),
                "integrity_valid": True,
                "cipher": "AES-128-CBC with PKCS#7 padding",
                "hash": "MD5 (protocol-defined key derivation and 32-bit integrity prefix)",
            },
            "payload_type": payload_type,
            "accounts": accounts,
            "structure": structure_report,
        }
    finally:
        if sid:
            try:
                unsubscribe(host, sid)
            except SonosError:
                pass
        server.shutdown()
        server.server_close()


def redact_report(report: dict[str, Any], reveal: bool = False) -> dict[str, Any]:
    result = copy.deepcopy(report)
    accounts = result.get("accounts")
    if reveal or not isinstance(accounts, dict):
        return result

    for instance in accounts.get("instances", []):
        values = instance.get("credential_values", {})
        instance["credential_values"] = {
            key: f"<redacted: {len(str(value))} characters>"
            for key, value in values.items()
        }
    return result


def format_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, sort_keys=True)
    return str(value)


class SonosExplorerApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("Sonos Service Explorer")
        self.root.geometry("1180x760")
        self.root.minsize(920, 620)
        self.root.configure(bg=BG)

        self.players: list[DiscoveredPlayer] = []
        self.services: list[dict[str, str]] = []
        self.account_data: dict[str, Any] | None = None
        self.browser_contexts: dict[str, smapi.DesktopBrowseSession] = {}
        self.browser_stack: list[tuple[str, str, bool, int, dict[str, Any]]] = []
        self.browser_rows: dict[str, dict[str, Any]] = {}
        self.browser_art_cache: dict[str, Any] = {}
        self.browser_art_pending: set[str] = set()
        self.browser_detail_image: Any = None
        self.last_export: dict[str, Any] = {}
        self.busy = False

        self.host_var = StringVar()
        self.household_var = StringVar()
        self.timeout_var = StringVar(value="3")
        self.wait_var = StringVar(value="8")
        self.port_var = StringVar(value="3411")
        self.search_var = StringVar()
        self.reveal_var = BooleanVar(value=False)
        self.status_var = StringVar(value="Ready")
        self.summary_var = StringVar(value="No Sonos data loaded yet")
        self.browser_account_var = StringVar()
        self.browser_path_var = StringVar(value="Load browser accounts to begin")
        self.browser_transport_var = StringVar(value="")

        self.ui_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._configure_styles()
        self._build_ui()
        self._bind_events()
        self.root.after(120, self._poll_ui_queue)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure("App.TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("Panel2.TFrame", background=PANEL_2)
        style.configure("Header.TLabel", background=BG, foreground=TEXT, font=("Helvetica", 22, "bold"))
        style.configure("Subheader.TLabel", background=BG, foreground=MUTED, font=("Helvetica", 11))
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
        style.configure("Muted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("Status.TLabel", background=BG, foreground=MUTED)
        style.configure("Accent.TButton", background=ACCENT, foreground="#08111c", padding=(14, 8), borderwidth=0)
        style.map("Accent.TButton", background=[("active", ACCENT_HOVER), ("disabled", BORDER)])
        style.configure("Secondary.TButton", background=PANEL_2, foreground=TEXT, padding=(12, 8), borderwidth=1)
        style.map("Secondary.TButton", background=[("active", BORDER)])
        style.configure("TEntry", fieldbackground=PANEL_2, foreground=TEXT, insertcolor=TEXT, bordercolor=BORDER)
        style.configure("TCombobox", fieldbackground=PANEL_2, foreground=TEXT, arrowcolor=TEXT, bordercolor=BORDER)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL_2)], foreground=[("readonly", TEXT)])
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", PANEL)])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(16, 9), borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", PANEL_2)], foreground=[("selected", TEXT)])
        style.configure(
            "Treeview",
            background=PANEL,
            fieldbackground=PANEL,
            foreground=TEXT,
            rowheight=28,
            bordercolor=BORDER,
            borderwidth=0,
        )
        style.map("Treeview", background=[("selected", SELECTED)], foreground=[("selected", TEXT)])
        style.configure("Browser.Treeview", rowheight=60)
        style.configure("Treeview.Heading", background=PANEL_2, foreground=TEXT, relief="flat", padding=(8, 7))
        style.map("Treeview.Heading", background=[("active", BORDER)])
        style.configure("Horizontal.TProgressbar", troughcolor=PANEL_2, background=ACCENT, bordercolor=PANEL_2)
        style.configure("Vertical.TScrollbar", background=PANEL_2, troughcolor=PANEL, arrowcolor=TEXT)
        style.configure("Horizontal.TScrollbar", background=PANEL_2, troughcolor=PANEL, arrowcolor=TEXT)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, style="App.TFrame", padding=18)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer, style="App.TFrame")
        header.pack(fill="x", pady=(0, 14))
        header.columnconfigure(0, weight=1)

        ttk.Label(header, text="Sonos Service Explorer", style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Discover music-service metadata and inspect account structure locally.",
            style="Subheader.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))

        self.progress = ttk.Progressbar(header, mode="indeterminate", length=160)
        self.progress.grid(row=0, column=1, rowspan=2, sticky="e")

        connection = ttk.Frame(outer, style="Panel.TFrame", padding=14)
        connection.pack(fill="x", pady=(0, 14))
        for column in range(8):
            connection.columnconfigure(column, weight=0)
        connection.columnconfigure(1, weight=2)
        connection.columnconfigure(3, weight=1)

        ttk.Label(connection, text="Player", style="Panel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.host_combo = ttk.Combobox(connection, textvariable=self.host_var, state="normal")
        self.host_combo.grid(row=0, column=1, sticky="ew", padx=(0, 14))

        ttk.Label(connection, text="Household", style="Panel.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 8))
        self.household_entry = ttk.Entry(connection, textvariable=self.household_var)
        self.household_entry.grid(row=0, column=3, sticky="ew", padx=(0, 14))

        self.discover_button = ttk.Button(connection, text="Discover", style="Secondary.TButton", command=self.discover)
        self.discover_button.grid(row=0, column=4, padx=(0, 8))
        self.services_button = ttk.Button(connection, text="Load services", style="Secondary.TButton", command=self.load_services)
        self.services_button.grid(row=0, column=5, padx=(0, 8))
        self.accounts_button = ttk.Button(connection, text="Inspect accounts", style="Secondary.TButton", command=self.load_accounts)
        self.accounts_button.grid(row=0, column=6, padx=(0, 8))
        self.run_all_button = ttk.Button(connection, text="Run all", style="Accent.TButton", command=self.run_all)
        self.run_all_button.grid(row=0, column=7)

        options = ttk.Frame(connection, style="Panel.TFrame")
        options.grid(row=1, column=0, columnspan=8, sticky="ew", pady=(12, 0))
        ttk.Label(options, text="Discovery timeout", style="Muted.TLabel").pack(side=LEFT)
        ttk.Entry(options, textvariable=self.timeout_var, width=5).pack(side=LEFT, padx=(7, 16))
        ttk.Label(options, text="Event wait", style="Muted.TLabel").pack(side=LEFT)
        ttk.Entry(options, textvariable=self.wait_var, width=5).pack(side=LEFT, padx=(7, 16))
        ttk.Label(options, text="Callback port", style="Muted.TLabel").pack(side=LEFT)
        ttk.Entry(options, textvariable=self.port_var, width=7).pack(side=LEFT, padx=(7, 18))
        ttk.Checkbutton(
            options,
            text="Reveal credential values locally",
            variable=self.reveal_var,
            command=self._refresh_sensitive_views,
        ).pack(side=LEFT)
        ttk.Label(
            options,
            text="Exports stay redacted unless this is enabled.",
            style="Muted.TLabel",
        ).pack(side=LEFT, padx=(8, 0))

        toolbar = ttk.Frame(outer, style="App.TFrame")
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Label(toolbar, textvariable=self.summary_var, style="Subheader.TLabel").pack(side=LEFT)
        ttk.Button(toolbar, text="Copy JSON", style="Secondary.TButton", command=self.copy_json).pack(side=RIGHT)
        ttk.Button(toolbar, text="Export JSON…", style="Secondary.TButton", command=self.export_json).pack(side=RIGHT, padx=(0, 8))

        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)

        self._build_services_tab()
        self._build_accounts_tab()
        self._build_browser_tab()
        self._build_json_tab()
        self._build_log_tab()

        footer = ttk.Frame(outer, style="App.TFrame")
        footer.pack(fill="x", pady=(10, 0))
        ttk.Label(footer, textvariable=self.status_var, style="Status.TLabel").pack(side=LEFT)
        ttk.Label(
            footer,
            text="Sensitive fields are masked by default",
            style="Status.TLabel",
        ).pack(side=RIGHT)

    def _build_services_tab(self) -> None:
        tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=10)
        self.notebook.add(tab, text="Services")

        filter_bar = ttk.Frame(tab, style="Panel.TFrame")
        filter_bar.pack(fill="x", pady=(0, 8))
        ttk.Label(filter_bar, text="Filter", style="Panel.TLabel").pack(side=LEFT)
        ttk.Entry(filter_bar, textvariable=self.search_var).pack(side=LEFT, fill="x", expand=True, padx=(8, 0))

        pane = ttk.Panedwindow(tab, orient=HORIZONTAL)
        pane.pack(fill="both", expand=True)

        table_frame = ttk.Frame(pane, style="Panel.TFrame")
        details_frame = ttk.Frame(pane, style="Panel.TFrame")
        pane.add(table_frame, weight=3)
        pane.add(details_frame, weight=2)

        columns = ("id", "name", "version", "secure", "capabilities")
        self.services_tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "id": ("ID", 70),
            "name": ("Name", 190),
            "version": ("Version", 80),
            "secure": ("Secure", 80),
            "capabilities": ("Capabilities", 180),
        }
        for key, (label, width) in headings.items():
            self.services_tree.heading(key, text=label)
            self.services_tree.column(key, width=width, minwidth=55, stretch=key in ("name", "capabilities"))

        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.services_tree.yview)
        xscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.services_tree.xview)
        self.services_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.services_tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        ttk.Label(details_frame, text="Selected service", style="Panel.TLabel").pack(anchor="w", pady=(0, 7))
        self.service_details = self._make_text(details_frame)
        self.service_details.pack(fill="both", expand=True)
        self._set_text(self.service_details, "Select a service to view all returned attributes.")

    def _build_accounts_tab(self) -> None:
        tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=10)
        self.notebook.add(tab, text="Accounts")

        pane = ttk.Panedwindow(tab, orient=HORIZONTAL)
        pane.pack(fill="both", expand=True)

        table_frame = ttk.Frame(pane, style="Panel.TFrame")
        details_frame = ttk.Frame(pane, style="Panel.TFrame")
        pane.add(table_frame, weight=3)
        pane.add(details_frame, weight=2)

        columns = ("index", "service", "service_id", "slots", "credentials")
        self.accounts_tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "index": ("#", 45),
            "service": ("Service", 190),
            "service_id": ("ID", 70),
            "slots": ("Slots", 60),
            "credentials": ("Credential fields", 220),
        }
        for key, (label, width) in headings.items():
            self.accounts_tree.heading(key, text=label)
            self.accounts_tree.column(key, width=width, minwidth=45, stretch=key in ("service", "credentials"))

        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.accounts_tree.yview)
        xscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.accounts_tree.xview)
        self.accounts_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.accounts_tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        ttk.Label(details_frame, text="Selected account instance", style="Panel.TLabel").pack(anchor="w", pady=(0, 7))
        self.account_details = self._make_text(details_frame)
        self.account_details.pack(fill="both", expand=True)
        self._set_text(self.account_details, "Run account inspection, then select an instance.")

    def _build_browser_tab(self) -> None:
        tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=10)
        self.notebook.add(tab, text="Browse music")

        toolbar = ttk.Frame(tab, style="Panel.TFrame")
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Label(toolbar, text="Account", style="Panel.TLabel").pack(side=LEFT)
        self.browser_account_combo = ttk.Combobox(
            toolbar,
            textvariable=self.browser_account_var,
            state="readonly",
            width=38,
        )
        self.browser_account_combo.pack(side=LEFT, padx=(8, 8))
        self.browser_load_button = ttk.Button(
            toolbar,
            text="Load accounts",
            style="Secondary.TButton",
            command=self.load_browser_accounts,
        )
        self.browser_load_button.pack(side=LEFT, padx=(0, 8))
        self.browser_back_button = ttk.Button(
            toolbar,
            text="Back",
            style="Secondary.TButton",
            command=self.browser_back,
        )
        self.browser_back_button.pack(side=LEFT, padx=(0, 8))
        self.browser_previous_button = ttk.Button(
            toolbar,
            text="Previous page",
            style="Secondary.TButton",
            command=self.browser_previous,
        )
        self.browser_previous_button.pack(side=LEFT, padx=(0, 8))
        self.browser_next_button = ttk.Button(
            toolbar,
            text="Next page",
            style="Secondary.TButton",
            command=self.browser_next,
        )
        self.browser_next_button.pack(side=LEFT, padx=(0, 8))
        self.browser_refresh_button = ttk.Button(
            toolbar,
            text="Refresh",
            style="Secondary.TButton",
            command=self.browser_refresh,
        )
        self.browser_refresh_button.pack(side=LEFT)
        ttk.Label(toolbar, textvariable=self.browser_transport_var, style="Muted.TLabel").pack(side=RIGHT)

        path = ttk.Frame(tab, style="Panel2.TFrame", padding=(10, 7))
        path.pack(fill="x", pady=(0, 8))
        ttk.Label(path, textvariable=self.browser_path_var, style="Muted.TLabel").pack(anchor="w")

        pane = ttk.Panedwindow(tab, orient=HORIZONTAL)
        pane.pack(fill="both", expand=True)
        table_frame = ttk.Frame(pane, style="Panel.TFrame")
        details_frame = ttk.Frame(pane, style="Panel.TFrame")
        pane.add(table_frame, weight=4)
        pane.add(details_frame, weight=2)

        columns = ("title", "artist", "type", "open")
        self.browser_tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="tree headings",
            selectmode="browse",
            style="Browser.Treeview",
        )
        self.browser_tree.heading("#0", text="Art")
        self.browser_tree.column("#0", width=66, minwidth=66, stretch=False)
        for key, label, width in (
            ("title", "Title", 350),
            ("artist", "Artist", 180),
            ("type", "Type", 110),
            ("open", "", 36),
        ):
            self.browser_tree.heading(key, text=label)
            self.browser_tree.column(
                key,
                width=width,
                minwidth=30 if key == "open" else 70,
                stretch=key in {"title", "artist"},
                anchor="center" if key == "open" else "w",
            )
        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.browser_tree.yview)
        xscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.browser_tree.xview)
        self.browser_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.browser_tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        ttk.Label(details_frame, text="Selected music item", style="Panel.TLabel").pack(anchor="w", pady=(0, 7))
        self.browser_art_label = ttk.Label(details_frame, text="", style="Panel.TLabel")
        self.browser_art_label.pack(anchor="w", pady=(0, 7))
        self.browser_details = self._make_text(details_frame)
        self.browser_details.pack(fill="both", expand=True)
        self._set_text(
            self.browser_details,
            "Double-click a collection to browse it. The transport label shows whether the official content or SMAPI path was used.",
        )

    def _build_json_tab(self) -> None:
        tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=10)
        self.notebook.add(tab, text="JSON")
        self.json_text = self._make_text(tab, wrap="none")
        self.json_text.pack(fill="both", expand=True)
        self._set_text(self.json_text, "{}")

    def _build_log_tab(self) -> None:
        tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=10)
        self.notebook.add(tab, text="Activity")
        self.log_text = self._make_text(tab, wrap="word")
        self.log_text.pack(fill="both", expand=True)
        self._set_text(self.log_text, "Sonos Service Explorer ready.\n")

    def _make_text(self, parent: ttk.Frame, wrap: str = "word") -> Text:
        return Text(
            parent,
            wrap=wrap,
            background=PANEL_2,
            foreground=TEXT,
            insertbackground=TEXT,
            selectbackground=SELECTED,
            selectforeground=TEXT,
            relief="flat",
            borderwidth=0,
            padx=12,
            pady=10,
            font=("Menlo", 11),
            undo=False,
        )

    def _bind_events(self) -> None:
        self.host_combo.bind("<<ComboboxSelected>>", self._player_selected)
        self.services_tree.bind("<<TreeviewSelect>>", self._service_selected)
        self.accounts_tree.bind("<<TreeviewSelect>>", self._account_selected)
        self.browser_account_combo.bind("<<ComboboxSelected>>", self._browser_account_selected)
        self.browser_tree.bind("<<TreeviewSelect>>", self._browser_item_selected)
        self.browser_tree.bind("<Double-1>", self._browser_item_open)
        self.search_var.trace_add("write", lambda *_args: self._render_services())

    def _parse_positive_number(self, value: str, label: str, *, allow_zero: bool = False) -> int:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise SonosError(f"{label} must be an integer") from exc
        minimum = 0 if allow_zero else 1
        if parsed < minimum:
            comparator = "zero or greater" if allow_zero else "greater than zero"
            raise SonosError(f"{label} must be {comparator}")
        return parsed

    def _connection_values(self, *, require_household: bool) -> tuple[str, str, int, int, int]:
        host = self.host_var.get().strip()
        household = self.household_var.get().strip()
        if not host:
            raise SonosError("Enter a Sonos player IP or run Discover first")
        if require_household and not household:
            raise SonosError("A household ID is required for account inspection; run Discover first")
        timeout = self._parse_positive_number(self.timeout_var.get(), "Discovery timeout")
        wait_seconds = self._parse_positive_number(self.wait_var.get(), "Event wait")
        port = self._parse_positive_number(self.port_var.get(), "Callback port", allow_zero=True)
        if port > 65535:
            raise SonosError("Callback port must be between 0 and 65535")
        return host, household, timeout, wait_seconds, port

    def _run_task(self, label: str, work: Callable[[], Any], success: Callable[[Any], None]) -> None:
        if self.busy:
            return
        self.busy = True
        self._set_controls_enabled(False)
        self.progress.start(12)
        self.status_var.set(label)
        self._log(label)

        def runner() -> None:
            try:
                result = work()
                self.ui_queue.put(("success", (label, success, result)))
            except Exception as exc:  # UI boundary: show a friendly error and keep full trace in activity log.
                self.ui_queue.put(("error", (label, exc, traceback.format_exc())))

        threading.Thread(target=runner, daemon=True).start()

    def _poll_ui_queue(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "album_art":
                    url, image_data = payload
                    self._album_art_ready(url, image_data)
                    continue
                if kind == "success":
                    label, callback, result = payload
                    callback(result)
                    self.status_var.set(f"Done — {label}")
                    self._log(f"Completed: {label}")
                else:
                    label, exc, trace = payload
                    self.status_var.set(f"Failed — {label}")
                    self._log(f"ERROR during {label}: {exc}\n{trace}")
                    messagebox.showerror("Sonos Service Explorer", str(exc), parent=self.root)

                self.busy = False
                self.progress.stop()
                self._set_controls_enabled(True)
        except queue.Empty:
            pass
        self.root.after(120, self._poll_ui_queue)

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in (
            self.discover_button,
            self.services_button,
            self.accounts_button,
            self.run_all_button,
            self.browser_load_button,
            self.browser_back_button,
            self.browser_previous_button,
            self.browser_next_button,
            self.browser_refresh_button,
        ):
            button.configure(state=state)

    def discover(self) -> None:
        try:
            timeout = self._parse_positive_number(self.timeout_var.get(), "Discovery timeout")
        except SonosError as exc:
            messagebox.showerror("Sonos Service Explorer", str(exc), parent=self.root)
            return
        self._run_task(
            "Discovering Sonos players…",
            lambda: discover_players(timeout),
            self._discovery_complete,
        )

    def _discovery_complete(self, players: list[DiscoveredPlayer]) -> None:
        self.players = players
        labels = [player.label for player in players]
        self.host_combo.configure(values=labels)
        self.host_combo.set(labels[0])
        self.host_var.set(players[0].host)
        self.household_var.set(players[0].household)
        self.summary_var.set(f"Discovered {len(players)} Sonos player{'s' if len(players) != 1 else ''}")
        self._log("Discovered: " + ", ".join(player.label for player in players))

    def _player_selected(self, _event: object | None = None) -> None:
        selected = self.host_combo.get()
        for player in self.players:
            if selected in (player.label, player.host):
                self.host_var.set(player.host)
                self.household_var.set(player.household)
                return

    def load_services(self) -> None:
        try:
            host, _household, _timeout, _wait, _port = self._connection_values(require_household=False)
        except SonosError as exc:
            messagebox.showerror("Sonos Service Explorer", str(exc), parent=self.root)
            return
        self._run_task(
            "Loading available music services…",
            lambda: list_available_services(host),
            self._services_complete,
        )

    def _services_complete(self, services: list[dict[str, str]]) -> None:
        self.services = services
        self._render_services()
        self._refresh_export()
        self.summary_var.set(f"Loaded {len(services)} available music services")
        self.notebook.select(0)

    def load_accounts(self) -> None:
        try:
            host, household, timeout, wait_seconds, port = self._connection_values(require_household=False)
        except SonosError as exc:
            messagebox.showerror("Sonos Service Explorer", str(exc), parent=self.root)
            return

        def work() -> tuple[DiscoveredPlayer | None, list[dict[str, str]], dict[str, Any]]:
            player: DiscoveredPlayer | None = None
            actual_household = household
            if not actual_household:
                player = discover_matching_player(host, timeout)
                actual_household = player.household
            services = self.services or list_available_services(host)
            report = inspect_accounts(
                host,
                actual_household,
                service_catalog(services),
                port=port,
                wait_seconds=wait_seconds,
            )
            return player, services, report

        self._run_task("Inspecting Sonos account metadata…", work, self._accounts_complete)

    def _accounts_complete(
        self,
        result: tuple[DiscoveredPlayer | None, list[dict[str, str]], dict[str, Any]],
    ) -> None:
        player, services, report = result
        if player:
            self.household_var.set(player.household)
        self.services = services
        self.account_data = report
        self._render_services()
        self._render_accounts()
        self._refresh_export()
        count = ((report.get("accounts") or {}).get("instance_count", 0))
        self.summary_var.set(f"Loaded {len(services)} services and {count} account instance{'s' if count != 1 else ''}")
        self.notebook.select(1)

    def run_all(self) -> None:
        try:
            timeout = self._parse_positive_number(self.timeout_var.get(), "Discovery timeout")
            wait_seconds = self._parse_positive_number(self.wait_var.get(), "Event wait")
            port = self._parse_positive_number(self.port_var.get(), "Callback port", allow_zero=True)
        except SonosError as exc:
            messagebox.showerror("Sonos Service Explorer", str(exc), parent=self.root)
            return

        def work() -> tuple[list[DiscoveredPlayer], list[dict[str, str]], dict[str, Any]]:
            players = discover_players(timeout)
            selected_host = self.host_var.get().strip()
            player = next((item for item in players if item.host == selected_host), players[0])
            services = list_available_services(player.host)
            report = inspect_accounts(
                player.host,
                player.household,
                service_catalog(services),
                port=port,
                wait_seconds=wait_seconds,
            )
            return players, services, report

        self._run_task("Discovering and loading all Sonos data…", work, self._all_complete)

    def _all_complete(
        self,
        result: tuple[list[DiscoveredPlayer], list[dict[str, str]], dict[str, Any]],
    ) -> None:
        players, services, report = result
        self.players = players
        selected_host = report["discovery"]["player_ip"]
        selected_player = next(player for player in players if player.host == selected_host)
        self.host_combo.configure(values=[player.label for player in players])
        self.host_combo.set(selected_player.label)
        self.host_var.set(selected_player.host)
        self.household_var.set(selected_player.household)
        self.services = services
        self.account_data = report
        self._render_services()
        self._render_accounts()
        self._refresh_export()
        count = ((report.get("accounts") or {}).get("instance_count", 0))
        self.summary_var.set(
            f"{selected_player.name}: {len(services)} services, {count} account instance{'s' if count != 1 else ''}"
        )
        self.notebook.select(0)

    def load_browser_accounts(self) -> None:
        try:
            host, household, timeout, _wait, _port = self._connection_values(require_household=False)
        except SonosError as exc:
            messagebox.showerror("Sonos Service Explorer", str(exc), parent=self.root)
            return

        def work() -> tuple[str, dict[str, smapi.DesktopBrowseSession]]:
            actual_household = household
            if not actual_household:
                actual_household = discover_matching_player(host, timeout).household
            services, accounts = smapi.inventory(host, actual_household)
            player_id = smapi.player_device_id(host)
            zone_id = smapi.player_zone_id(host)
            contexts: dict[str, smapi.DesktopBrowseSession] = {}
            for account in accounts:
                service = services.get(account.service_id)
                if not service:
                    continue
                label = smapi.account_label(service, account)
                contexts[label] = smapi.DesktopBrowseSession(
                    smapi.SmapiClient(
                        service,
                        account,
                        actual_household,
                        player_id,
                        zone_id,
                        host,
                        allow_credential_refresh=True,
                    )
                )
            return actual_household, contexts

        self._run_task("Loading browsable music accounts…", work, self._browser_accounts_complete)

    def _browser_accounts_complete(
        self,
        result: tuple[str, dict[str, smapi.DesktopBrowseSession]],
    ) -> None:
        household, contexts = result
        if not contexts:
            messagebox.showerror(
                "Sonos Service Explorer",
                "No configured music-service accounts were found",
                parent=self.root,
            )
            return
        self.household_var.set(household)
        self.browser_contexts = contexts
        labels = list(contexts)
        self.browser_account_combo.configure(values=labels)
        self.browser_account_var.set(labels[0])
        self.notebook.select(2)
        self.root.after(0, self._browse_root)

    def _browser_account_selected(self, _event: object | None = None) -> None:
        if self.browser_account_var.get() in self.browser_contexts:
            self._browse_root()

    def _browse_root(self) -> None:
        label = self.browser_account_var.get()
        session = self.browser_contexts.get(label)
        if not session:
            return
        self._run_task(
            f"Browsing {label}…",
            lambda: session.browse("root", 0, 100),
            lambda page: self._browser_page_complete("root", session.client.service.name, False, page, reset=True),
        )

    def _browser_page_complete(
        self,
        object_id: str,
        title: str,
        from_content: bool,
        page: dict[str, Any],
        *,
        reset: bool = False,
    ) -> None:
        entry = (object_id, title, from_content, int(page.get("index", 0) or 0), page)
        if reset:
            self.browser_stack = [entry]
        else:
            self.browser_stack.append(entry)
        self._render_browser_page()

    def _render_browser_page(self) -> None:
        for iid in self.browser_tree.get_children():
            self.browser_tree.delete(iid)
        self.browser_rows = {}
        self.browser_detail_image = None
        self.browser_art_label.configure(image="", text="")
        if not self.browser_stack:
            return
        object_id, _title, _from_content, page_index, page = self.browser_stack[-1]
        items = page.get("items", [])
        if not isinstance(items, list):
            items = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            iid = f"browse-{index}"
            self.browser_rows[iid] = item
            art_url = str(item.get("album_art_uri", ""))
            self.browser_tree.insert(
                "",
                END,
                iid=iid,
                image=self.browser_art_cache.get(art_url, ""),
                values=(
                    item.get("title", item.get("id", "untitled")),
                    item.get("artist", ""),
                    item.get("item_type", item.get("kind", "")),
                    "›" if item.get("kind") == "mediaCollection" else "",
                ),
            )
        self.browser_path_var.set("  /  ".join(entry[1] for entry in self.browser_stack))
        transport = page.get("transport", "smapi")
        first = page_index + 1 if items else 0
        last = page_index + len(items)
        total = int(page.get("total", len(items)) or 0)
        self.browser_transport_var.set(f"Transport: {transport} · {first}-{last} of {total}")
        self.summary_var.set(f"Browsing {self.browser_account_var.get()}: {len(items)} items")
        self._set_text(
            self.browser_details,
            json.dumps(
                {
                    "object_id": object_id,
                    "transport": transport,
                    "requested_id": page.get("requested_id", object_id),
                    "index": page_index,
                    "total": total,
                    "endpoint": page.get("endpoint", ""),
                },
                indent=2,
            ),
        )
        self._load_browser_art()

    def _load_browser_art(self) -> None:
        if PILImage is None or ImageTk is None:
            return
        urls = []
        for item in self.browser_rows.values():
            url = str(item.get("album_art_uri", ""))
            if url and url not in self.browser_art_cache and url not in self.browser_art_pending:
                self.browser_art_pending.add(url)
                urls.append(url)
        if not urls:
            return

        def worker() -> None:
            for url in urls:
                try:
                    request = urllib.request.Request(url, headers={"User-Agent": smapi.DESKTOP_USER_AGENT})
                    with urllib.request.urlopen(request, timeout=10) as response:
                        source = response.read(5 * 1024 * 1024 + 1)
                    if len(source) > 5 * 1024 * 1024:
                        raise ValueError("artwork response exceeds 5 MB")
                    with PILImage.open(io.BytesIO(source)) as image:
                        image.thumbnail((52, 52), PILImage.Resampling.LANCZOS)
                        converted = image.convert("RGBA")
                        output = io.BytesIO()
                        converted.save(output, format="PNG")
                    self.ui_queue.put(("album_art", (url, output.getvalue())))
                except Exception:
                    self.ui_queue.put(("album_art", (url, None)))

        threading.Thread(target=worker, daemon=True).start()

    def _album_art_ready(self, url: str, image_data: bytes | None) -> None:
        self.browser_art_pending.discard(url)
        if not image_data or ImageTk is None:
            return
        try:
            photo = ImageTk.PhotoImage(data=image_data)
        except Exception:
            return
        self.browser_art_cache[url] = photo
        for iid, item in self.browser_rows.items():
            if str(item.get("album_art_uri", "")) == url and self.browser_tree.exists(iid):
                self.browser_tree.item(iid, image=photo)
        selection = self.browser_tree.selection()
        if selection:
            selected = self.browser_rows.get(selection[0], {})
            if str(selected.get("album_art_uri", "")) == url:
                self.browser_detail_image = photo
                self.browser_art_label.configure(image=photo, text="")

    def _browser_item_selected(self, _event: object | None = None) -> None:
        selection = self.browser_tree.selection()
        if not selection:
            return
        item = self.browser_rows.get(selection[0])
        if item:
            art_url = str(item.get("album_art_uri", ""))
            photo = self.browser_art_cache.get(art_url)
            self.browser_detail_image = photo
            self.browser_art_label.configure(
                image=photo or "",
                text="Loading artwork…" if art_url and not photo else "",
            )
            self._set_text(self.browser_details, json.dumps(item, indent=2, ensure_ascii=False))

    def _browser_item_open(self, _event: object | None = None) -> None:
        selection = self.browser_tree.selection()
        if not selection:
            return
        item = self.browser_rows.get(selection[0])
        if not item or item.get("kind") != "mediaCollection":
            return
        session = self.browser_contexts.get(self.browser_account_var.get())
        if not session:
            return
        object_id = str(item.get("id", ""))
        if not object_id:
            return
        title = str(item.get("title", object_id))
        from_content = item.get("source_transport") == "content"
        self._run_task(
            f"Browsing {title}…",
            lambda: session.browse(object_id, 0, 100, from_content_page=from_content),
            lambda page: self._browser_page_complete(object_id, title, from_content, page),
        )

    def browser_back(self) -> None:
        if len(self.browser_stack) > 1:
            self.browser_stack.pop()
            self._render_browser_page()

    def browser_refresh(self) -> None:
        if not self.browser_stack:
            self._browse_root()
            return
        session = self.browser_contexts.get(self.browser_account_var.get())
        if not session:
            return
        object_id, title, from_content, page_index, _page = self.browser_stack[-1]
        self._run_task(
            f"Refreshing {title}…",
            lambda: session.browse(object_id, page_index, 100, from_content_page=from_content),
            self._browser_refresh_complete,
        )

    def _browser_refresh_complete(self, page: dict[str, Any]) -> None:
        object_id, title, from_content, _page_index, _old_page = self.browser_stack[-1]
        self.browser_stack[-1] = (
            object_id,
            title,
            from_content,
            int(page.get("index", 0) or 0),
            page,
        )
        self._render_browser_page()

    def _browser_change_page(self, page_index: int) -> None:
        if not self.browser_stack:
            return
        session = self.browser_contexts.get(self.browser_account_var.get())
        if not session:
            return
        object_id, title, from_content, _current_index, page = self.browser_stack[-1]
        if page.get("transport") == "content":
            return
        target = max(0, page_index)
        self._run_task(
            f"Loading {title} page…",
            lambda: session.browse(object_id, target, 100, from_content_page=from_content),
            self._browser_refresh_complete,
        )

    def browser_previous(self) -> None:
        if self.browser_stack:
            self._browser_change_page(self.browser_stack[-1][3] - 100)

    def browser_next(self) -> None:
        if not self.browser_stack:
            return
        _object_id, _title, _from_content, page_index, page = self.browser_stack[-1]
        items = page.get("items", [])
        item_count = len(items) if isinstance(items, list) else 0
        total = int(page.get("total", item_count) or 0)
        if page_index + item_count < total:
            self._browser_change_page(page_index + max(1, item_count))

    def _render_services(self) -> None:
        for item in self.services_tree.get_children():
            self.services_tree.delete(item)

        query = self.search_var.get().strip().lower()
        for index, service in enumerate(self.services):
            haystack = " ".join(f"{key} {value}" for key, value in service.items()).lower()
            if query and query not in haystack:
                continue
            self.services_tree.insert(
                "",
                END,
                iid=f"service-{index}",
                values=(
                    service.get("Id", service.get("ServiceType", "?")),
                    service.get("Name", "?"),
                    service.get("Version", ""),
                    service.get("SecureMode", ""),
                    service.get("Capabilities", ""),
                ),
            )

        visible = len(self.services_tree.get_children())
        if self.services:
            self.summary_var.set(
                f"Showing {visible} of {len(self.services)} available music services"
                if query
                else f"Loaded {len(self.services)} available music services"
            )

    def _service_selected(self, _event: object | None = None) -> None:
        selection = self.services_tree.selection()
        if not selection:
            return
        index = int(selection[0].split("-", 1)[1])
        service = self.services[index]
        ordered_keys = [
            "Id",
            "Name",
            "Version",
            "Uri",
            "SecureMode",
            "Policy",
            "Capabilities",
            "ContainerType",
        ]
        keys = [key for key in ordered_keys if key in service]
        keys.extend(sorted(key for key in service if key not in keys))
        lines = [f"{key}: {service[key]}" for key in keys]
        self._set_text(self.service_details, "\n".join(lines) or "No attributes")

    def _render_accounts(self) -> None:
        for item in self.accounts_tree.get_children():
            self.accounts_tree.delete(item)

        if not self.account_data:
            self._set_text(self.account_details, "Run account inspection, then select an instance.")
            return

        accounts = self.account_data.get("accounts")
        if not isinstance(accounts, dict):
            self._set_text(
                self.account_details,
                "The decrypted payload was not XML account data. See the JSON tab for its structure.",
            )
            return

        for instance in accounts.get("instances", []):
            index = instance.get("instance_index", 0)
            self.accounts_tree.insert(
                "",
                END,
                iid=f"account-{index}",
                values=(
                    index,
                    instance.get("service_name", "unmapped"),
                    instance.get("service_id", ""),
                    instance.get("account_slots_declared", 0),
                    ", ".join(instance.get("credential_fields", [])) or "None",
                ),
            )

        if not accounts.get("instances"):
            self._set_text(self.account_details, "No account instances were present in the payload.")

    def _account_selected(self, _event: object | None = None) -> None:
        selection = self.accounts_tree.selection()
        if not selection or not self.account_data:
            return
        accounts = self.account_data.get("accounts")
        if not isinstance(accounts, dict):
            return
        index = int(selection[0].split("-", 1)[1])
        instances = accounts.get("instances", [])
        instance = next((item for item in instances if item.get("instance_index") == index), None)
        if instance is None:
            return

        display = copy.deepcopy(instance)
        if not self.reveal_var.get():
            display["credential_values"] = {
                key: f"<redacted: {len(str(value))} characters>"
                for key, value in display.get("credential_values", {}).items()
            }
        self._set_text(self.account_details, json.dumps(display, indent=2, sort_keys=True))

    def _refresh_sensitive_views(self) -> None:
        if self.reveal_var.get():
            confirmed = messagebox.askyesno(
                "Reveal credential values?",
                "This may display account tokens, keys, or usernames on screen and include them in copied/exported JSON. Continue?",
                parent=self.root,
                icon="warning",
            )
            if not confirmed:
                self.reveal_var.set(False)
        self._account_selected()
        self._refresh_export()

    def _refresh_export(self) -> None:
        data: dict[str, Any] = {
            "player": {
                "host": self.host_var.get().strip(),
                "household": self.household_var.get().strip(),
            },
            "services": self.services,
            "account_inspection": self.account_data,
        }
        if self.account_data:
            data["account_inspection"] = redact_report(
                self.account_data,
                reveal=self.reveal_var.get(),
            )
        self.last_export = data
        self._set_text(self.json_text, json.dumps(data, indent=2, sort_keys=True))

    def copy_json(self) -> None:
        self._refresh_export()
        text = json.dumps(self.last_export, indent=2, sort_keys=True)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set("Copied JSON to clipboard")
        self._log("Copied current JSON view to clipboard")

    def export_json(self) -> None:
        self._refresh_export()
        host = self.host_var.get().strip().replace(".", "-") or "sonos"
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export Sonos report",
            defaultextension=".json",
            initialfile=f"sonos-report-{host}.json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            Path(path).write_text(
                json.dumps(self.last_export, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            messagebox.showerror("Export failed", str(exc), parent=self.root)
            return
        self.status_var.set(f"Exported {Path(path).name}")
        self._log(f"Exported JSON to {path}")

    def _set_text(self, widget: Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", END)
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def _log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert(END, f"[{timestamp}] {message}\n")
        self.log_text.see(END)
        self.log_text.configure(state="disabled")


def main() -> None:
    # Apple's system Python is linked to Tk 8.5.9. On current macOS dark mode
    # that runtime creates windows but fails to draw even basic label/button
    # text. Transparently restart under the Homebrew Python when it has a
    # supported Tk, so the documented /usr/bin/python3 command cannot produce
    # a deceptive blank window.
    if sys.platform == "darwin" and tkinter.TkVersion < 8.6:
        candidate = Path("/opt/homebrew/bin/python3")
        if candidate.exists() and Path(sys.executable).resolve() != candidate.resolve():
            check = subprocess.run(
                [
                    str(candidate),
                    "-c",
                    "import tkinter; from PIL import Image, ImageTk; assert tkinter.TkVersion >= 8.6",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if check.returncode == 0:
                os.execv(str(candidate), [str(candidate), *sys.argv])
        raise SystemExit(
            "This Mac's system Tk 8.5 cannot render the GUI. Install the current runtime with "
            "`brew install python-tk@3.14 pillow`, then run this command again."
        )
    if PILImage is None or ImageTk is None:
        raise SystemExit(
            "Album artwork requires Pillow. Install it with `brew install pillow`, then run again."
        )
    root = Tk()
    SonosExplorerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

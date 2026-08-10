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
import secrets
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
import webbrowser
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
    simpledialog,
)
from tkinter import ttk
from typing import Any, Callable

import smapi_browser as smapi
import sonos_account_onboarding as onboarding

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
        self.onboarding_services: dict[str, smapi.Service] = {}
        self.onboarding_session: onboarding.LinkSession | None = None
        self.manage_services: dict[int, smapi.Service] = {}
        self.manage_accounts: dict[str, tuple[smapi.Service, smapi.Account]] = {}
        self.manage_reauthorize_session: onboarding.LinkSession | None = None
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
        self.onboarding_service_var = StringVar()
        self.onboarding_auth_var = StringVar(value="Load services to inspect their account flow")
        self.onboarding_username_var = StringVar()
        self.onboarding_password_var = StringVar()
        self.onboarding_nickname_var = StringVar()
        self.onboarding_url_var = StringVar()

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
        self._build_add_account_tab()
        self._build_manage_tab()
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

    def _build_add_account_tab(self) -> None:
        tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=18)
        self.notebook.add(tab, text="Add account")
        tab.columnconfigure(1, weight=1)

        ttk.Label(tab, text="Music service", style="Panel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 12), pady=6)
        self.onboarding_service_combo = ttk.Combobox(
            tab, textvariable=self.onboarding_service_var, state="readonly"
        )
        self.onboarding_service_combo.grid(row=0, column=1, sticky="ew", pady=6)
        self.onboarding_load_button = ttk.Button(
            tab, text="Load services", style="Secondary.TButton", command=self.load_onboarding_services
        )
        self.onboarding_load_button.grid(row=0, column=2, padx=(10, 0), pady=6)

        ttk.Label(tab, text="Flow", style="Panel.TLabel").grid(row=1, column=0, sticky="nw", padx=(0, 12), pady=6)
        ttk.Label(tab, textvariable=self.onboarding_auth_var, style="Muted.TLabel", wraplength=760).grid(
            row=1, column=1, columnspan=2, sticky="w", pady=6
        )

        ttk.Label(tab, text="Username", style="Panel.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 12), pady=6)
        self.onboarding_username_entry = ttk.Entry(tab, textvariable=self.onboarding_username_var)
        self.onboarding_username_entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=6)
        ttk.Label(tab, text="Password", style="Panel.TLabel").grid(row=3, column=0, sticky="w", padx=(0, 12), pady=6)
        self.onboarding_password_entry = ttk.Entry(tab, textvariable=self.onboarding_password_var, show="•")
        self.onboarding_password_entry.grid(row=3, column=1, columnspan=2, sticky="ew", pady=6)
        ttk.Label(tab, text="Nickname", style="Panel.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 12), pady=6)
        ttk.Entry(tab, textvariable=self.onboarding_nickname_var).grid(
            row=4, column=1, columnspan=2, sticky="ew", pady=6
        )

        actions = ttk.Frame(tab, style="Panel.TFrame")
        actions.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(14, 10))
        self.onboarding_start_button = ttk.Button(
            actions, text="Start provider sign-in", style="Accent.TButton", command=self.begin_account_onboarding
        )
        self.onboarding_start_button.pack(side=LEFT)
        self.onboarding_commit_button = ttk.Button(
            actions, text="Commit authorized account", style="Secondary.TButton", command=self.commit_account_onboarding
        )
        self.onboarding_commit_button.pack(side=LEFT, padx=(8, 0))

        ttk.Label(tab, text="Provider URL", style="Panel.TLabel").grid(row=6, column=0, sticky="nw", padx=(0, 12), pady=6)
        url_entry = ttk.Entry(tab, textvariable=self.onboarding_url_var, state="readonly")
        url_entry.grid(row=6, column=1, columnspan=2, sticky="ew", pady=6)
        ttk.Label(
            tab,
            text=(
                "Nothing is written to the speakers until Commit. The confirmation names the exact household, "
                "service, and UPnP operation. Provider credentials are never saved by this GUI."
            ),
            style="Muted.TLabel",
            wraplength=840,
        ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(16, 0))

        self.onboarding_service_combo.bind("<<ComboboxSelected>>", self._onboarding_service_selected)

    def _build_manage_tab(self) -> None:
        tab = ttk.Frame(self.notebook, style="Panel.TFrame", padding=10)
        self.notebook.add(tab, text="Manage accounts")

        toolbar = ttk.Frame(tab, style="Panel.TFrame")
        toolbar.pack(fill="x", pady=(0, 8))
        ttk.Label(toolbar, text="Configured accounts", style="Panel.TLabel").pack(side=LEFT)
        self.manage_load_button = ttk.Button(
            toolbar,
            text="Load accounts",
            style="Secondary.TButton",
            command=self.load_manage_accounts,
        )
        self.manage_load_button.pack(side=LEFT, padx=(10, 0))
        ttk.Label(
            toolbar,
            text="Every action below requires explicit confirmation and names the exact household and operation.",
            style="Muted.TLabel",
        ).pack(side=RIGHT)

        pane = ttk.Panedwindow(tab, orient=HORIZONTAL)
        pane.pack(fill="both", expand=True)
        table_frame = ttk.Frame(pane, style="Panel.TFrame")
        details_frame = ttk.Frame(pane, style="Panel.TFrame")
        pane.add(table_frame, weight=3)
        pane.add(details_frame, weight=2)

        columns = ("service", "auth", "serial", "nickname", "username", "state")
        self.manage_tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "service": ("Service", 180),
            "auth": ("Auth", 90),
            "serial": ("Serial", 60),
            "nickname": ("Nickname", 140),
            "username": ("Username", 150),
            "state": ("State", 110),
        }
        for key, (label, width) in headings.items():
            self.manage_tree.heading(key, text=label)
            self.manage_tree.column(key, width=width, minwidth=45, stretch=key in ("service", "nickname", "username"))

        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.manage_tree.yview)
        xscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.manage_tree.xview)
        self.manage_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.manage_tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        actions = ttk.Frame(details_frame, style="Panel.TFrame")
        actions.pack(fill="x", pady=(0, 8))
        self.manage_remove_button = ttk.Button(
            actions, text="Remove…", style="Secondary.TButton", command=self.manage_remove_account
        )
        self.manage_remove_button.pack(fill="x", pady=(0, 6))
        self.manage_rename_button = ttk.Button(
            actions, text="Set nickname…", style="Secondary.TButton", command=self.manage_set_nickname
        )
        self.manage_rename_button.pack(fill="x", pady=(0, 6))
        self.manage_password_button = ttk.Button(
            actions, text="Change password…", style="Secondary.TButton", command=self.manage_change_password
        )
        self.manage_password_button.pack(fill="x", pady=(0, 6))
        self.manage_reauthorize_button = ttk.Button(
            actions, text="Reauthorize…", style="Secondary.TButton", command=self.manage_reauthorize
        )
        self.manage_reauthorize_button.pack(fill="x", pady=(0, 6))
        ttk.Label(
            actions,
            text=(
                "RemoveAccount uses the account UDN as the native AccountID. Edit operations use the "
                "account key (Username0) — the player rejects the full UDN for them. Set nickname is "
                "disabled: this firmware rejects SetAccountNicknameX (UPnP 402); the Sonos apps rename "
                "via cloud. Keyless records (empty-key anonymous adds) are removed with the "
                "empty-key RemoveAccount contract, verified live."
            ),
            style="Muted.TLabel",
            wraplength=320,
        ).pack(anchor="w", pady=(8, 0))

        ttk.Label(details_frame, text="Selected account", style="Panel.TLabel").pack(anchor="w", pady=(0, 7))
        self.manage_details = self._make_text(details_frame)
        self.manage_details.pack(fill="both", expand=True)
        self._set_text(self.manage_details, "Load accounts, then select one to manage.")

        self.manage_tree.bind("<<TreeviewSelect>>", self._manage_account_selected)
        self.manage_remove_button.configure(state="disabled")
        self.manage_rename_button.configure(state="disabled")
        self.manage_password_button.configure(state="disabled")
        self.manage_reauthorize_button.configure(state="disabled")

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
            self.onboarding_load_button,
            self.onboarding_start_button,
            self.onboarding_commit_button,
            self.manage_load_button,
            self.manage_remove_button,
            self.manage_rename_button,
            self.manage_password_button,
            self.manage_reauthorize_button,
            self.browser_load_button,
            self.browser_back_button,
            self.browser_previous_button,
            self.browser_next_button,
            self.browser_refresh_button,
        ):
            button.configure(state=state)
        self.onboarding_service_combo.configure(state="readonly" if enabled else "disabled")
        if enabled and self.manage_accounts:
            # Restore selection-driven enablement after a background task.
            self._manage_account_selected()

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

    def load_onboarding_services(self) -> None:
        try:
            host, household, timeout, _wait, _port = self._connection_values(require_household=False)
        except SonosError as exc:
            messagebox.showerror("Sonos Service Explorer", str(exc), parent=self.root)
            return

        def work() -> tuple[str, dict[int, smapi.Service]]:
            actual_household = household
            if not actual_household:
                actual_household = discover_matching_player(host, timeout).household
            return actual_household, smapi.parse_services(host)

        self._run_task("Loading account-onboarding descriptors…", work, self._onboarding_services_complete)

    def _onboarding_services_complete(self, result: tuple[str, dict[int, smapi.Service]]) -> None:
        household, services = result
        self.household_var.set(household)
        self.onboarding_services = {
            f"{service.name} — {service.service_id}": service
            for service in sorted(services.values(), key=lambda value: value.name.lower())
        }
        labels = list(self.onboarding_services)
        self.onboarding_service_combo.configure(values=labels)
        if labels:
            self.onboarding_service_var.set(labels[0])
            self._onboarding_service_selected()
        self.notebook.select(2)

    def _onboarding_service_selected(self, _event: object | None = None) -> None:
        service = self.onboarding_services.get(self.onboarding_service_var.get())
        self.onboarding_session = None
        self.onboarding_url_var.set("")
        self.onboarding_username_var.set("")
        self.onboarding_password_var.set("")
        self.onboarding_nickname_var.set("")
        if not service:
            return
        descriptions = {
            "Anonymous": "Anonymous: no provider login. Commit creates the household service record (keyless, browsable, removable via the empty-key contract).",
            "UserId": "Legacy credentials: username is committed through AddAccountX.",
            "UserIdPassword": "Legacy credentials: username/password are committed through AddAccountX.",
            "DeviceLink": "Legacy device link: getAppLink is attempted, then getDeviceLinkCode as the official fallback.",
            "AppLink": "Modern provider link: getAppLink chooses browser or provider-app authorization.",
        }
        self.onboarding_auth_var.set(descriptions.get(service.auth, f"Unsupported descriptor auth type: {service.auth}"))
        credentials = service.auth in {"UserId", "UserIdPassword"}
        self.onboarding_username_entry.configure(state="normal" if credentials else "disabled")
        self.onboarding_password_entry.configure(
            state="normal" if service.auth == "UserIdPassword" else "disabled"
        )

    def begin_account_onboarding(self) -> None:
        service = self.onboarding_services.get(self.onboarding_service_var.get())
        if not service:
            messagebox.showerror("Sonos Service Explorer", "Load and select a music service first", parent=self.root)
            return
        if service.auth not in onboarding.AUTH_OPERATIONS:
            messagebox.showerror(
                "Sonos Service Explorer",
                f"{service.name} uses unsupported authentication type {service.auth!r}",
                parent=self.root,
            )
            return
        try:
            host, household, _timeout, _wait, _port = self._connection_values(require_household=True)
        except SonosError as exc:
            messagebox.showerror("Sonos Service Explorer", str(exc), parent=self.root)
            return
        if service.auth in {"Anonymous", "UserId", "UserIdPassword"}:
            self.onboarding_auth_var.set(
                f"{service.auth} is ready. Review the target and use Commit authorized account."
            )
            return

        callback = f"sonos://addAccount?state={secrets.token_urlsafe(24)}"
        self._run_task(
            f"Requesting {service.name} authorization…",
            lambda: onboarding.begin_link(host, household, service, callback_path=callback),
            self._onboarding_link_ready,
        )

    def _onboarding_link_ready(self, session: onboarding.LinkSession) -> None:
        self.onboarding_session = session
        self.onboarding_url_var.set(session.registration_url or session.app_url)
        if session.standalone_supported:
            self.onboarding_auth_var.set(
                f"{session.source_action} returned a browser link. Finish provider sign-in, then click Commit."
            )
            webbrowser.open(session.registration_url)
        elif session.app_url:
            self.onboarding_auth_var.set(
                "The provider returned only an app deep link. This desktop cannot guarantee that callback; "
                "use a compatible provider app or another service authorization path."
            )
        else:
            self.onboarding_auth_var.set(
                "The provider returned no standalone browser/device-link path. This is provider policy, not a LAN failure."
            )

    def commit_account_onboarding(self) -> None:
        service = self.onboarding_services.get(self.onboarding_service_var.get())
        if not service:
            messagebox.showerror("Sonos Service Explorer", "Load and select a music service first", parent=self.root)
            return
        try:
            host, household, _timeout, _wait, _port = self._connection_values(require_household=True)
        except SonosError as exc:
            messagebox.showerror("Sonos Service Explorer", str(exc), parent=self.root)
            return
        operation = onboarding.AUTH_OPERATIONS.get(service.auth)
        if not operation:
            messagebox.showerror(
                "Sonos Service Explorer",
                f"{service.name} uses unsupported authentication type {service.auth!r}",
                parent=self.root,
            )
            return
        if operation == "AddOAuthAccountX" and (
            not self.onboarding_session or not self.onboarding_session.standalone_supported
        ):
            messagebox.showerror(
                "Sonos Service Explorer",
                "Start and finish a provider browser sign-in before committing this account.",
                parent=self.root,
            )
            return
        try:
            actual_household = onboarding.player_household(host)
        except (onboarding.OnboardingError, OSError, smapi.LocalSoapFault, ET.ParseError) as exc:
            messagebox.showerror(
                "Sonos Service Explorer",
                f"Could not verify the target player's household: {exc}",
                parent=self.root,
            )
            return
        if actual_household != household:
            messagebox.showerror(
                "Sonos Service Explorer",
                f"The selected player now belongs to {actual_household}, not {household}. Reload before adding an account.",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            "Commit music-service account",
            f"Household: {actual_household}\nPlayer: {host}\nService: {service.name} ({service.service_id})\n"
            f"Operation: {operation}\n\nThis writes a new account to every player in the household. Continue?",
            parent=self.root,
        ):
            return
        username = self.onboarding_username_var.get()
        password = self.onboarding_password_var.get()
        nickname = self.onboarding_nickname_var.get().strip()
        session = self.onboarding_session

        def work() -> onboarding.AddedAccount:
            if operation == "AddAccountX":
                added = onboarding.add_credentials(
                    host,
                    service,
                    username,
                    password,
                    household_id=actual_household,
                )
            else:
                assert session is not None
                added = onboarding.commit_link(host, service, session)
            if nickname:
                onboarding.set_nickname(host, added.account_udn, nickname)
                return onboarding.AddedAccount(added.service_id, added.service_name, added.account_udn, nickname)
            return added

        self._run_task(f"Adding {service.name} account…", work, self._onboarding_commit_complete)

    def _onboarding_commit_complete(self, result: onboarding.AddedAccount) -> None:
        self.onboarding_password_var.set("")
        self.onboarding_session = None
        self.onboarding_auth_var.set(
            f"Added {result.service_name}: {result.nickname or result.account_udn}. Reload Accounts to verify replication."
        )
        messagebox.showinfo(
            "Sonos Service Explorer",
            f"{result.service_name} was added to the household.\n\nAccount UDN: {result.account_udn}",
            parent=self.root,
        )

    def load_manage_accounts(self) -> None:
        try:
            host, household, timeout, _wait, _port = self._connection_values(require_household=False)
        except SonosError as exc:
            messagebox.showerror("Sonos Service Explorer", str(exc), parent=self.root)
            return

        def work() -> tuple[str, dict[int, smapi.Service], list[smapi.Account]]:
            actual_household = household
            if not actual_household:
                actual_household = discover_matching_player(host, timeout).household
            services, accounts = smapi.inventory(host, actual_household)
            return actual_household, services, accounts

        self._run_task("Loading configured accounts…", work, self._manage_accounts_complete)

    def _manage_accounts_complete(
        self,
        result: tuple[str, dict[int, smapi.Service], list[smapi.Account]],
    ) -> None:
        household, services, accounts = result
        self.household_var.set(household)
        self.manage_services = services
        self.manage_accounts = {}
        for item in self.manage_tree.get_children():
            self.manage_tree.delete(item)
        if not accounts:
            self._set_text(self.manage_details, "No configured accounts were found in this household.")
            self.summary_var.set("No configured music-service accounts")
            return
        for account in sorted(accounts, key=lambda value: (value.service_id, value.serial)):
            service = services.get(account.service_id)
            if not service:
                continue
            iid = f"manage-{account.service_id}-{account.serial}"
            self.manage_accounts[iid] = (service, account)
            if account.token == "needs_reauth":
                state = "needs reauth"
            elif account.keyless:
                state = "keyless"
            elif account.token:
                state = "linked"
            else:
                state = "credential"
            self.manage_tree.insert(
                "",
                END,
                iid=iid,
                values=(
                    service.name,
                    service.auth,
                    account.serial,
                    account.nickname,
                    account.username,
                    state,
                ),
            )
        self.summary_var.set(f"Loaded {len(self.manage_accounts)} configured account{'s' if len(self.manage_accounts) != 1 else ''}")
        self.notebook.select(3)

    def _selected_manage_account(self) -> tuple[smapi.Service, smapi.Account] | None:
        selection = self.manage_tree.selection()
        if not selection:
            return None
        return self.manage_accounts.get(selection[0])

    def _manage_account_selected(self, _event: object | None = None) -> None:
        pair = self._selected_manage_account()
        enabled = pair is not None
        # Remove works for every record: keyed accounts resolve by their UDN, and
        # keyless records resolve with the empty-key contract (verified live).
        self.manage_remove_button.configure(state="normal" if enabled else "disabled")
        # Local SetAccountNicknameX is rejected by this firmware (UPnP 402 on
        # every input, verified against the live player); the Sonos apps rename
        # through their cloud, so the local rename affordance stays disabled.
        self.manage_rename_button.configure(state="disabled")
        self.manage_reauthorize_button.configure(state="normal" if enabled else "disabled")
        if pair is None:
            return
        service, account = pair
        password_ok = service.auth == "UserIdPassword" and enabled
        self.manage_password_button.configure(state="normal" if password_ok else "disabled")
        keyless = account.keyless
        details = {
            "service_id": service.service_id,
            "service": service.name,
            "auth": service.auth,
            "serial": account.serial,
            "udn": account.udn,
            "username": account.username,
            "nickname": account.nickname,
            "has_token": bool(account.token),
            "needs_reauth": account.token == "needs_reauth",
        }
        details["rename_note"] = (
            "Set nickname is disabled: this player's firmware rejects SetAccountNicknameX with UPnP "
            "error 402 for every account; the Sonos apps rename accounts through their cloud."
        )
        if keyless:
            details["keyless_record"] = (
                "This record has no provider key or username (empty-key anonymous record). Remove "
                "uses the empty-key RemoveAccount contract, which the player resolves for this "
                "service's keyless record (verified live); Set nickname stays disabled."
            )
        self._set_text(self.manage_details, json.dumps(details, indent=2, sort_keys=True))

    def _manage_confirmation(self, operation: str, service: smapi.Service, extra: str = "") -> str | None:
        try:
            host, household, _timeout, _wait, _port = self._connection_values(require_household=True)
        except SonosError as exc:
            messagebox.showerror("Sonos Service Explorer", str(exc), parent=self.root)
            return None
        try:
            actual_household = onboarding.player_household(host)
        except (onboarding.OnboardingError, OSError, smapi.LocalSoapFault, ET.ParseError) as exc:
            messagebox.showerror(
                "Sonos Service Explorer",
                f"Could not verify the target player's household: {exc}",
                parent=self.root,
            )
            return None
        if actual_household != household:
            messagebox.showerror(
                "Sonos Service Explorer",
                f"The selected player now belongs to {actual_household}, not {household}. Reload before managing accounts.",
                parent=self.root,
            )
            return None
        message = (
            f"Household: {actual_household}\nPlayer: {host}\nService: {service.name} ({service.service_id})\n"
            f"Operation: {operation}\n"
        )
        if extra:
            message += f"{extra}\n"
        message += "\nThis writes to every player in the household. Continue?"
        if not messagebox.askyesno("Sonos account management", message, parent=self.root):
            return None
        return actual_household

    def manage_remove_account(self) -> None:
        pair = self._selected_manage_account()
        if not pair:
            return
        service, account = pair
        keyless_note = (
            "\n\nThis record has no provider key; removal uses the empty-key contract for this "
            "service only." if account.keyless else ""
        )
        household_id = self._manage_confirmation(
            "RemoveAccount",
            service,
            f"Account UDN: {account.udn}\n\nThis removes the account from the household. Its queue entries stop resolving."
            + keyless_note,
        )
        if not household_id:
            return
        host = self.host_var.get().strip()

        def work() -> None:
            onboarding.remove_account(host, service, account.udn, household_id=household_id)

        self._run_task(f"Removing {service.name} account…", work, self._manage_remove_complete)

    def _manage_remove_complete(self, _result: None) -> None:
        self.summary_var.set("Account removed. Reload Manage accounts to confirm replication.")
        self._log("RemoveAccount completed for the selected household account")
        messagebox.showinfo(
            "Sonos Service Explorer",
            "The account was removed from the household. Reload Manage accounts to verify.",
            parent=self.root,
        )

    def manage_set_nickname(self) -> None:
        # Kept only so the disabled button's command binding does not dangle; the
        # action itself is firmware-blocked and never invoked through the UI.
        messagebox.showinfo(
            "Sonos Service Explorer",
            "Set nickname is disabled: this player's firmware (90.0) rejects the local "
            "SetAccountNicknameX action with UPnP error 402 for every account (verified against the "
            "live player). The Sonos apps rename accounts through their cloud instead.",
            parent=self.root,
        )

    def manage_change_password(self) -> None:
        pair = self._selected_manage_account()
        if not pair:
            return
        service, account = pair
        if service.auth != "UserIdPassword":
            messagebox.showinfo(
                "Sonos Service Explorer",
                f"{service.name} uses {service.auth}; EditAccountPasswordX applies to UserIdPassword services.",
                parent=self.root,
            )
            return
        new_password = simpledialog.askstring(
            "Change stored password",
            f"New password for {service.name} (serial {account.serial}):",
            show="•",
            parent=self.root,
        )
        if not new_password:
            return
        household_id = self._manage_confirmation(
            "EditAccountPasswordX",
            service,
            f"Account UDN: {account.udn}",
        )
        if not household_id:
            return
        host = self.host_var.get().strip()

        def work() -> None:
            onboarding.edit_account_password(host, service, account.udn, new_password, household_id=household_id)

        self._run_task(f"Updating {service.name} password…", work, self._manage_password_complete)

    def _manage_password_complete(self, _result: None) -> None:
        self.summary_var.set("Stored password updated.")
        self._log("EditAccountPasswordX completed for the selected account")
        messagebox.showinfo(
            "Sonos Service Explorer",
            "The stored password was updated on the household players.",
            parent=self.root,
        )

    def manage_reauthorize(self) -> None:
        pair = self._selected_manage_account()
        if not pair:
            return
        service, account = pair
        if service.auth not in onboarding.AUTH_OPERATIONS:
            messagebox.showerror(
                "Sonos Service Explorer",
                f"{service.name} uses unsupported authentication type {service.auth!r}",
                parent=self.root,
            )
            return
        if onboarding.AUTH_OPERATIONS[service.auth] != "AddOAuthAccountX":
            if service.auth == "Anonymous":
                messagebox.showinfo(
                    "Sonos Service Explorer",
                    f"{service.name} is an anonymous service: no provider credentials exist, so there "
                    "is nothing to reauthorize or change.",
                    parent=self.root,
                )
            else:
                messagebox.showinfo(
                    "Sonos Service Explorer",
                    f"{service.name} uses {service.auth}; use Change password for legacy credential accounts.",
                    parent=self.root,
                )
            return
        try:
            host, household, _timeout, _wait, _port = self._connection_values(require_household=True)
        except SonosError as exc:
            messagebox.showerror("Sonos Service Explorer", str(exc), parent=self.root)
            return
        if not messagebox.askyesno(
            "Reauthorize account",
            f"Household: {household}\nPlayer: {host}\nService: {service.name} ({service.service_id})\n"
            f"Account: {account.udn}\n\n"
            "This starts the provider link flow and, after you authorize, commits a fresh "
            "AddOAuthAccountX record to the household. Continue?",
            parent=self.root,
        ):
            return
        callback = f"sonos://addAccount?state={secrets.token_urlsafe(24)}"
        self._run_task(
            f"Requesting {service.name} reauthorization…",
            lambda: onboarding.begin_link(host, household, service, callback_path=callback),
            lambda session: self._manage_reauthorize_link_ready(session, host, service),
        )

    def _manage_reauthorize_link_ready(
        self,
        session: onboarding.LinkSession,
        host: str,
        service: smapi.Service,
    ) -> None:
        self.manage_reauthorize_session = session
        if not session.standalone_supported:
            messagebox.showerror(
                "Sonos Service Explorer",
                "The provider returned no standalone browser path for reauthorization.",
                parent=self.root,
            )
            return
        webbrowser.open(session.registration_url)
        if not messagebox.askyesno(
            "Finish provider sign-in",
            f"Open in the browser and finish signing in. After the provider confirms, commit the new account?\n\n"
            f"Provider URL: {session.registration_url}",
            parent=self.root,
        ):
            return

        def work() -> onboarding.AddedAccount:
            return onboarding.commit_link(host, service, session)

        self._run_task(f"Committing reauthorized {service.name} account…", work, self._manage_reauthorize_complete)

    def _manage_reauthorize_complete(self, result: onboarding.AddedAccount) -> None:
        self.manage_reauthorize_session = None
        self.summary_var.set(
            f"Reauthorized {result.service_name}. Reload Manage accounts; the old account can be removed there."
        )
        self._log(f"Reauthorization committed a new account: {result.account_udn}")
        messagebox.showinfo(
            "Sonos Service Explorer",
            f"A fresh {result.service_name} account was committed.\n\nAccount UDN: {result.account_udn}\n\n"
            "Reload Manage accounts to see it; the previous account can be removed with Remove.",
            parent=self.root,
        )

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
        self.notebook.select(4)
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

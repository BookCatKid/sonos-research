#!/usr/bin/env python3
"""Discover and structurally inspect Sonos ThirdPartyMediaServersX without leaking values."""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import http.client
import json
import queue
import re
import socket
import subprocess
import threading
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from sonos_discovery import discover_one, parse_ssdp_headers


SSDP_ADDRESS = ("239.255.255.250", 1900)
ZGT_EVENT_PATH = "/ZoneGroupTopology/Event"
SALT = bytes.fromhex("1a01a731c96e9ebde8475182b274b70e")



def parse_headers(packet: bytes) -> dict[str, str]:
    return parse_ssdp_headers(packet)


def discover(timeout: float = 3.0, *, requested_host: str | None = None) -> tuple[str, str]:
    return discover_one(timeout, requested_host=requested_host)


def local_ip_for(host: str) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((host, 1400))
        return sock.getsockname()[0]


class CaptureHandler(BaseHTTPRequestHandler):
    captured: queue.Queue[str] = queue.Queue(maxsize=1)

    def do_NOTIFY(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            root = ET.fromstring(body)
            for node in root.iter():
                if node.tag.rsplit("}", 1)[-1] == "ThirdPartyMediaServersX":
                    value = "".join(node.itertext()).strip()
                    try:
                        self.captured.put_nowait(value)
                    except queue.Full:
                        pass
                    break
        finally:
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


def subscribe(host: str, callback: str, timeout: int) -> str:
    connection = http.client.HTTPConnection(host, 1400, timeout=5)
    connection.request(
        "SUBSCRIBE",
        ZGT_EVENT_PATH,
        headers={"CALLBACK": f"<{callback}>", "NT": "upnp:event", "TIMEOUT": f"Second-{timeout}"},
    )
    response = connection.getresponse()
    response.read()
    sid = response.getheader("SID")
    connection.close()
    if response.status != 200 or not sid:
        raise RuntimeError(f"SUBSCRIBE failed with HTTP {response.status}")
    return sid


def unsubscribe(host: str, sid: str) -> None:
    connection = http.client.HTTPConnection(host, 1400, timeout=5)
    connection.request("UNSUBSCRIBE", ZGT_EVENT_PATH, headers={"SID": sid})
    response = connection.getresponse()
    response.read()
    connection.close()


def list_available_services(host: str) -> dict[str, str]:
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
        return {}
    # ElementTree has already unescaped the descriptor text from the SOAP envelope.
    catalog_root = ET.fromstring(descriptor)
    catalog: dict[str, str] = {}
    for service in catalog_root.iter():
        if service.tag.rsplit("}", 1)[-1] != "Service":
            continue
        service_id = service.attrib.get("Id") or service.attrib.get("ServiceType")
        name = service.attrib.get("Name")
        if service_id and name:
            catalog[str(service_id)] = name
    return catalog


def aes_128_cbc_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    result = subprocess.run(
        ["openssl", "enc", "-d", "-aes-128-cbc", "-K", key.hex(), "-iv", iv.hex()],
        input=ciphertext,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("AES-CBC decryption or PKCS#7 validation failed")
    return result.stdout


def decrypt_blob(encoded: str, household: str) -> bytes:
    encoded = html.unescape(encoded).strip()
    if not encoded.startswith("2:"):
        raise RuntimeError("Unsupported ThirdPartyMediaServersX version")
    raw = base64.b64decode(encoded[2:], validate=True)
    if len(raw) < 32 or len(raw[16:]) % 16:
        raise RuntimeError("Invalid encrypted payload dimensions")
    iv, ciphertext = raw[:16], raw[16:]
    global_key = hashlib.md5(household.encode("utf-8") + SALT).digest()  # protocol primitive
    blob_key = hashlib.md5(iv + global_key).digest()  # protocol primitive
    checked = aes_128_cbc_decrypt(ciphertext, blob_key, iv)
    if len(checked) < 4:
        raise RuntimeError("Decrypted payload is too short")
    payload, checksum = checked[:-4], checked[-4:]
    if hashlib.md5(payload).digest()[:4] != checksum:  # protocol integrity field
        raise RuntimeError("Embedded MD5 checksum mismatch")
    return payload


def scalar_summary(value: Any, key: str = "") -> dict[str, Any]:
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
        key = path.rsplit(".", 1)[-1]
        rows.append({"path": path, **scalar_summary(value, key)})
    return rows


def parse_payload(payload: bytes) -> tuple[str, Any]:
    text = payload.decode("utf-8")
    try:
        return "json", json.loads(text)
    except json.JSONDecodeError:
        root = ET.fromstring(text)

        def xml_shape(node: ET.Element) -> dict[str, Any]:
            return {
                "tag": node.tag.rsplit("}", 1)[-1],
                "attributes": {key.rsplit("}", 1)[-1]: scalar_summary(val, key) for key, val in node.attrib.items()},
                "text": scalar_summary((node.text or "").strip(), node.tag) if (node.text or "").strip() else None,
                "children": [xml_shape(child) for child in node],
            }

        return "xml", xml_shape(root)


def account_report(payload: bytes, catalog: dict[str, str]) -> dict[str, Any]:
    root = ET.fromstring(payload.decode("utf-8"))
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
            key for key in attrs
            if re.match(r"^(Token|Key|Username)\d+$", key) and bool(attrs[key])
        )
        all_attrs = {k: v for k, v in attrs.items() if k != "UDN"}
        instances.append(
            {
                "instance_index": instance_index,
                "service_id": service_id,
                "service_name": catalog.get(service_id, "unmapped"),
                "udn": attrs.get("UDN", ""),
                "udn_schema_revision": schema_revision,
                "account_slots_declared": int(attrs.get("NumAccounts", "0") or 0),
                "serial_indexes": sorted(
                    int(value) for key, value in attrs.items()
                    if re.match(r"^SerialNum\d+$", key) and value.isdigit()
                ),
                "credential_fields": credential_fields,
                "credential_values": {
                    key: attrs[key] for key in credential_fields
                },
                "other_attributes": {
                    key: value for key, value in attrs.items()
                    if key not in credential_fields and key != "UDN" and not re.match(r"^SerialNum\d+$", key)
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3411)
    parser.add_argument("--seconds", type=int, default=8)
    args = parser.parse_args()

    host, household = discover()
    CaptureHandler.captured = queue.Queue(maxsize=1)
    server = ThreadingHTTPServer(("0.0.0.0", args.port), CaptureHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    sid = ""
    try:
        callback = f"http://{local_ip_for(host)}:{args.port}{ZGT_EVENT_PATH}"
        sid = subscribe(host, callback, args.seconds + 10)
        encoded = CaptureHandler.captured.get(timeout=args.seconds)
        payload = decrypt_blob(encoded, household)
        payload_type, parsed = parse_payload(payload)
        catalog = list_available_services(host)
        report = {
            "discovery": {
                "player_ip": host,
                "household_id": household,
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
            "accounts": account_report(payload, catalog) if payload_type == "xml" else None,
            "structure": structure(parsed) if payload_type == "json" else {
                "root_tag": parsed["tag"],
                "child_tags": sorted({child["tag"] for child in parsed["children"]}),
            },
        }
        print(json.dumps(report, indent=2, sort_keys=True))
    finally:
        if sid:
            try:
                unsubscribe(host, sid)
            except Exception:
                pass
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()

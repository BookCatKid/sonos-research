#!/usr/bin/env python3
"""Temporarily subscribe to Sonos UPnP events and print redacted structure."""

from __future__ import annotations

import argparse
import hashlib
import html
import http.client
import json
import socket
import threading
import time
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


EVENT_PATHS = (
    "/ZoneGroupTopology/Event",
    "/MusicServices/Event",
    "/SystemProperties/Event",
    "/MediaServer/ContentDirectory/Event",
)


def local_ip_for(host: str) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((host, 1400))
        return sock.getsockname()[0]


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:12]


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def summarize_nested(value: str, property_name: str = "") -> dict[str, object]:
    decoded = html.unescape(value).strip()

    try:
        json_value = json.loads(decoded)
    except (json.JSONDecodeError, TypeError):
        json_value = None
    if isinstance(json_value, (dict, list)):
        rows: list[dict[str, object]] = []

        def walk_json(item: object, path: str) -> None:
            if isinstance(item, dict):
                rows.append(
                    {
                        "path": path,
                        "kind": "object",
                        "keys": sorted(str(key) for key in item),
                    }
                )
                for key, child in item.items():
                    walk_json(child, f"{path}.{key}")
            elif isinstance(item, list):
                rows.append({"path": path, "kind": "array", "length": len(item)})
                for index, child in enumerate(item):
                    walk_json(child, f"{path}[{index}]")
            elif isinstance(item, str):
                rows.append(
                    {
                        "path": path,
                        "kind": "string",
                        "length": len(item),
                        "sha256": digest(item),
                    }
                )
            else:
                rows.append({"path": path, "kind": type(item).__name__, "value": item})

        walk_json(json_value, "$")
        return {"kind": "json", "nodes": rows}

    try:
        root = ET.fromstring(decoded)
    except ET.ParseError:
        sensitive_name = property_name.lower() in {
            "customerid",
            "sid",
            "musehouseholdid",
            "areasupdateid",
            "sourceareasupdateid",
            "zonegroupid",
            "zoneplayeruuidsingroup",
        }
        contains_device_id = "RINCON_" in decoded or "Sonos_" in decoded
        if (
            not sensitive_name
            and not contains_device_id
            and len(decoded) <= 80
            and all(ch.isprintable() for ch in decoded)
        ):
            return {"kind": "text", "value": decoded}
        printable = sum(ch.isprintable() for ch in decoded)
        return {
            "kind": "opaque",
            "length": len(decoded),
            "sha256": digest(decoded),
            "prefix_codepoints": [ord(ch) for ch in decoded[:16]],
            "printable_fraction": round(printable / max(1, len(decoded)), 4),
        }

    rows: list[dict[str, object]] = []

    def walk(node: ET.Element, path: str) -> None:
        text = (node.text or "").strip()
        row: dict[str, object] = {
            "path": path,
            "attributes": sorted(local_name(key) for key in node.attrib),
            "children": len(node),
        }
        if text:
            row["text_length"] = len(text)
            row["text_sha256"] = digest(text)
        rows.append(row)
        for index, child in enumerate(node):
            walk(child, f"{path}/{local_name(child.tag)}[{index}]")

    walk(root, local_name(root.tag))
    return {"kind": "xml", "nodes": rows}


class EventHandler(BaseHTTPRequestHandler):
    server_version = "SonosEventProbe/1"

    def do_NOTIFY(self) -> None:  # noqa: N802 - HTTP method dispatch naming
        size = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(size)
        sid = self.headers.get("SID", "")
        summary: dict[str, object] = {
            "event_path": self.path,
            "sid_length": len(sid),
            "sid_sha256": digest(sid),
            "properties": [],
        }
        try:
            root = ET.fromstring(body)
            properties = summary["properties"]
            assert isinstance(properties, list)
            for prop in root:
                for value_node in prop:
                    value = "".join(value_node.itertext())
                    properties.append(
                        {
                            "name": local_name(value_node.tag),
                            "summary": summarize_nested(value, local_name(value_node.tag)),
                        }
                    )
        except ET.ParseError as error:
            summary["parse_error"] = str(error)
            summary["body_length"] = len(body)
            summary["body_sha256"] = hashlib.sha256(body).hexdigest()[:12]

        print(json.dumps(summary, indent=2, sort_keys=True), flush=True)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


def subscribe(host: str, path: str, callback: str, timeout: int) -> str:
    connection = http.client.HTTPConnection(host, 1400, timeout=5)
    connection.request(
        "SUBSCRIBE",
        path,
        headers={
            "CALLBACK": f"<{callback}{path}>",
            "NT": "upnp:event",
            "TIMEOUT": f"Second-{timeout}",
        },
    )
    response = connection.getresponse()
    response.read()
    if response.status != 200:
        raise RuntimeError(f"SUBSCRIBE {path} failed: HTTP {response.status}")
    sid = response.getheader("SID")
    if not sid:
        raise RuntimeError(f"SUBSCRIBE {path} returned no SID")
    connection.close()
    return sid


def unsubscribe(host: str, path: str, sid: str) -> None:
    connection = http.client.HTTPConnection(host, 1400, timeout=5)
    connection.request("UNSUBSCRIBE", path, headers={"SID": sid})
    response = connection.getresponse()
    response.read()
    connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("host", help="Sonos player IPv4 address")
    parser.add_argument("--port", type=int, default=3410)
    parser.add_argument("--seconds", type=int, default=10)
    parser.add_argument(
        "--path",
        action="append",
        dest="paths",
        help="Event path to subscribe to (repeatable; defaults to the known relevant services)",
    )
    args = parser.parse_args()

    callback_ip = local_ip_for(args.host)
    server = ThreadingHTTPServer(("0.0.0.0", args.port), EventHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    callback = f"http://{callback_ip}:{args.port}"
    subscriptions: list[tuple[str, str]] = []
    try:
        for path in args.paths or EVENT_PATHS:
            sid = subscribe(args.host, path, callback, args.seconds + 10)
            subscriptions.append((path, sid))
            print(
                json.dumps(
                    {
                        "subscribed": path,
                        "sid_length": len(sid),
                        "sid_sha256": digest(sid),
                    }
                ),
                flush=True,
            )
        time.sleep(args.seconds)
    finally:
        for path, sid in subscriptions:
            try:
                unsubscribe(args.host, path, sid)
            except Exception as error:  # best-effort cleanup
                print(json.dumps({"unsubscribe_error": path, "error": str(error)}))
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()

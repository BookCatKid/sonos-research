#!/usr/bin/env python3
"""Compare two credential-redacted Sonos system-inspection snapshots offline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sonos_system_inspector import write_private


def _player_id(player: dict[str, Any]) -> str:
    udn = str(player.get("device", {}).get("UDN", "")).removeprefix("uuid:")
    return udn or str(player.get("host", "unknown"))


def _account_id(account: dict[str, Any]) -> str:
    return f"{account.get('service_id')}:{account.get('account_uid') or account.get('serial')}"


def _action_id(action: dict[str, Any]) -> str:
    return f"{action.get('service_type')}#{action.get('action')}"


def _group_shape(report: dict[str, Any]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for group in report.get("topology", {}).get("groups", []):
        members = []
        for member in group.get("members", []):
            members.append(
                {
                    "uuid": str(member.get("UUID", "")),
                    "satellites": sorted(
                        str(satellite.get("UUID", ""))
                        for satellite in member.get("satellites", [])
                    ),
                }
            )
        groups.append(
            {
                "coordinator": str(group.get("coordinator", "")),
                "members": sorted(members, key=lambda item: (item["uuid"], item["satellites"])),
            }
        )
    return sorted(
        groups,
        key=lambda item: (
            item["coordinator"],
            tuple((member["uuid"], tuple(member["satellites"])) for member in item["members"]),
        ),
    )


def _changed_fields(before: dict[str, Any], after: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    changes = {}
    for field in fields:
        if before.get(field) != after.get(field):
            changes[field] = {"before": before.get(field), "after": after.get(field)}
    return changes


def compare_reports(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {
        "household_changed": before.get("discovery", {}).get("household_id")
        != after.get("discovery", {}).get("household_id"),
        "players": {"added": [], "removed": [], "changed": []},
        "topology": {},
        "music_accounts": {"added": [], "removed": [], "changed": []},
        "music_services": {"added": [], "removed": [], "changed": []},
        "capabilities": {"added": [], "removed": []},
    }
    before_players = {_player_id(player): player for player in before.get("players", [])}
    after_players = {_player_id(player): player for player in after.get("players", [])}
    changes["players"]["added"] = sorted(after_players.keys() - before_players.keys())
    changes["players"]["removed"] = sorted(before_players.keys() - after_players.keys())
    for player_id in sorted(before_players.keys() & after_players.keys()):
        old = before_players[player_id]
        new = after_players[player_id]
        fields = _changed_fields(
            old.get("device", {}),
            new.get("device", {}),
            ("friendlyName", "roomName", "modelName", "softwareVersion", "hardwareVersion"),
        )
        old_zone = old.get("reads", {}).get("GetZoneAttributes", {})
        new_zone = new.get("reads", {}).get("GetZoneAttributes", {})
        fields.update(_changed_fields(old_zone, new_zone, ("CurrentZoneName", "CurrentConfiguration")))
        if fields:
            changes["players"]["changed"].append({"id": player_id, "fields": fields})
    old_groups = _group_shape(before)
    new_groups = _group_shape(after)
    if old_groups != new_groups:
        changes["topology"] = {"before": old_groups, "after": new_groups}

    before_accounts = {
        _account_id(account): account for account in before.get("music", {}).get("accounts", [])
    }
    after_accounts = {
        _account_id(account): account for account in after.get("music", {}).get("accounts", [])
    }
    changes["music_accounts"]["added"] = sorted(after_accounts.keys() - before_accounts.keys())
    changes["music_accounts"]["removed"] = sorted(before_accounts.keys() - after_accounts.keys())
    for account_id in sorted(before_accounts.keys() & after_accounts.keys()):
        fields = _changed_fields(
            before_accounts[account_id],
            after_accounts[account_id],
            ("nickname", "tier", "credential_state", "schema_revision"),
        )
        if fields:
            changes["music_accounts"]["changed"].append({"id": account_id, "fields": fields})

    before_services = {
        str(service.get("id")): service for service in before.get("music", {}).get("services", [])
    }
    after_services = {
        str(service.get("id")): service for service in after.get("music", {}).get("services", [])
    }
    changes["music_services"]["added"] = sorted(after_services.keys() - before_services.keys())
    changes["music_services"]["removed"] = sorted(before_services.keys() - after_services.keys())
    for service_id in sorted(before_services.keys() & after_services.keys()):
        fields = _changed_fields(
            before_services[service_id],
            after_services[service_id],
            ("name", "auth", "capabilities", "policy", "secure_endpoint_host", "manifest_host"),
        )
        if fields:
            changes["music_services"]["changed"].append({"id": service_id, "fields": fields})

    before_actions = {
        _action_id(action) for action in before.get("capabilities", {}).get("actions", [])
    }
    after_actions = {
        _action_id(action) for action in after.get("capabilities", {}).get("actions", [])
    }
    changes["capabilities"]["added"] = sorted(after_actions - before_actions)
    changes["capabilities"]["removed"] = sorted(before_actions - after_actions)
    sections = [changes[section] for section in ("players", "topology", "music_accounts", "music_services", "capabilities")]
    changes["has_changes"] = changes["household_changed"] or any(
        any(value for value in section.values()) if isinstance(section, dict) else bool(section)
        for section in sections
    )
    return changes


def markdown(changes: dict[str, Any]) -> str:
    lines = ["# Sonos system snapshot comparison", ""]
    if not changes["has_changes"]:
        return "\n".join(lines + ["No material changes detected.", ""])
    if changes["household_changed"]:
        lines.extend(["- Household identity changed.", ""])
    labels = (
        ("players", "Players"),
        ("music_accounts", "Music accounts"),
        ("music_services", "Music-service catalog"),
        ("capabilities", "Advertised capabilities"),
    )
    for key, title in labels:
        section = changes[key]
        if not any(section.values()):
            continue
        lines.extend([f"## {title}", ""])
        for kind in ("added", "removed"):
            for value in section.get(kind, []):
                lines.append(f"- {kind.capitalize()}: `{value}`")
        for value in section.get("changed", []):
            lines.append(f"- Changed: `{value['id']}` — {', '.join(value['fields'])}")
        lines.append("")
    if changes["topology"]:
        lines.extend(["## Topology", "", "Room grouping/bond membership changed.", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before")
    parser.add_argument("after")
    parser.add_argument("--output", help="write JSON diff")
    parser.add_argument("--markdown", help="write Markdown summary")
    args = parser.parse_args()
    before = json.loads(Path(args.before).read_text(encoding="utf-8"))
    after = json.loads(Path(args.after).read_text(encoding="utf-8"))
    changes = compare_reports(before, after)
    rendered = json.dumps(changes, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        write_private(Path(args.output).expanduser().resolve(), rendered)
    if args.markdown:
        write_private(Path(args.markdown).expanduser().resolve(), markdown(changes))
    print(rendered, end="")


if __name__ == "__main__":
    main()

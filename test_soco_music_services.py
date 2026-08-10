from __future__ import annotations

import argparse
import sys

import soco
from soco.music_services import Account, MusicService


def describe_item(item) -> str:
    title = getattr(item, "title", None) or ""
    item_id = getattr(item, "item_id", None) or ""
    return f"{item_id!r} {title!r}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "service",
        nargs="?",
        default=None,
        help="service name to browse, eg 'Audible' (default: all linked)",
    )
    args = parser.parse_args()

    print("soco", soco.__version__, soco.__file__)
    players = soco.discover(timeout=4)
    if not players:
        print("No Sonos players discovered")
        return 1
    players = sorted(players, key=lambda player: player.ip_address)
    device = players[0]
    print("Players:", ", ".join(f"{p.player_name} ({p.ip_address})" for p in players))

    print("\nPlayer state (stock SoCo API):")
    print("  zone name:", device.player_name)
    print("  household:", device.household_id)
    try:
        track = device.get_current_track_info()
        print("  now playing:", track.get("artist"), "-", track.get("title"))
    except Exception as exc:  # noqa: BLE001 - smoke test surfaces all failures
        print("  now playing: <unavailable: {}>".format(type(exc).__name__))

    print("\nMusic services (local integration):")
    accounts = Account.get_accounts(device)
    if not accounts:
        print("  No linked accounts")
        return 0
    failures = 0
    for serial_number, account in sorted(accounts.items()):
        if account.deleted:
            continue
        try:
            service = MusicService.from_account(account, device=device)
        except Exception as exc:  # noqa: BLE001
            print(f"  {account.service_type}: <skip: {exc}>")
            continue
        if args.service and args.service.lower() not in (
            service.service_name.lower(),
            account.service_type,
        ):
            continue
        print(f"  {service.service_name} [{account.service_type}] auth={service.auth_type}")
        try:
            result = service.get_metadata("root", 0, 10)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"    root browse FAILED: {type(exc).__name__}: {exc}")
            continue
        items = list(result or ())
        print(f"    root OK: {len(items)} items")
        for item in items[:5]:
            print("      -", describe_item(item))
    if failures:
        print(f"\n{failures} service(s) failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

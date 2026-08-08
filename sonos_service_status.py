"""Fetch Sonos's public component and music-service outage status."""

from __future__ import annotations

import json
import urllib.request
from typing import Any

BASE = "https://status.sonos.com/api/v2"


def _get(name: str) -> dict[str, Any]:
    request = urllib.request.Request(f"{BASE}/{name}.json", headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def current_status() -> dict[str, Any]:
    snapshot = _get("summary")
    components = snapshot.get("components", [])
    incidents = snapshot.get("incidents", [])
    return {
        "page": snapshot.get("page", {}),
        "status": snapshot.get("status", {}),
        "degraded_components": [
            component
            for component in components
            if isinstance(component, dict) and component.get("status") != "operational"
        ],
        "unresolved_incidents": incidents,
        "source": "public Sonos Statuspage API",
    }


if __name__ == "__main__":
    print(json.dumps(current_status(), indent=2))

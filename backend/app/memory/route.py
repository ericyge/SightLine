"""Deterministic path selection over taught place_connections. No vision I/O.

Owner: A. See docs/MEMORY.md.
"""
from collections import deque
from dataclasses import asdict, dataclass, field

from .. import db
from .store import destination_for, get_map


@dataclass
class RouteHop:
    """One directed edge to speak and follow."""
    from_place_id: str
    to_place_id: str
    instruction: str
    departure_view: str
    arrival_view: str
    inferred_reverse: bool = False  # Retained in API responses; routes only use saved edges.


@dataclass
class RoutePlan:
    """BFS result from current place to destination."""
    status: str  # ok | already_there | no_path | unknown_current | unknown_destination
    path: list[str] = field(default_factory=list)  # place ids, current -> destination
    hops: list[RouteHop] = field(default_factory=list)

    @property
    def next_hop(self) -> RouteHop | None:
        return self.hops[0] if self.hops else None


def plan_route(current_place_id: str | None, destination_place_id: str | None,
               connections: list[dict] | None = None,
               places: list[dict] | None = None) -> RoutePlan:
    """Shortest path via BFS over saved directed edges; never invent a return route."""
    if places is None or connections is None:
        saved = get_map()
        places = places if places is not None else saved["places"]
        connections = connections if connections is not None else saved["connections"]
    place_ids = {p["id"] for p in places}
    if current_place_id is None or current_place_id not in place_ids:
        return RoutePlan("unknown_current")
    if destination_place_id is None or destination_place_id not in place_ids:
        return RoutePlan("unknown_destination")
    if current_place_id == destination_place_id:
        return RoutePlan("already_there", [current_place_id])

    edges: dict[str, list[dict]] = {}
    for edge in connections:
        edges.setdefault(edge["from_place_id"], []).append(edge)

    prev: dict[str, tuple[str, dict]] = {}
    queue = deque([current_place_id])
    seen = {current_place_id}
    while queue:
        node = queue.popleft()
        if node == destination_place_id:
            path = [destination_place_id]
            hops_rev: list[RouteHop] = []
            while node != current_place_id:
                parent, edge = prev[node]
                hops_rev.append(RouteHop(
                    from_place_id=edge["from_place_id"], to_place_id=edge["to_place_id"],
                    instruction=edge["instruction"], departure_view=edge["departure_view"],
                    arrival_view=edge["arrival_view"]))
                path.append(parent)
                node = parent
            return RoutePlan("ok", list(reversed(path)), list(reversed(hops_rev)))
        for edge in edges.get(node, []):
            nxt = edge["to_place_id"]
            if nxt in seen or nxt not in place_ids:
                continue
            seen.add(nxt)
            prev[nxt] = (node, edge)
            queue.append(nxt)

    return RoutePlan("no_path", [current_place_id])


def directions_to_item(item: str, *, from_place_id: str | None = None) -> dict:
    """Route from an explicit start to the newest sighting's assigned destination only."""
    name = db.resolve_item(item)
    history = db.recent_sightings(name, 1) if name else []
    saved = get_map()
    places = saved["places"]
    by_id = {p["id"]: p for p in places}

    if not history:
        label = name or item.strip().lower()
        return {
            "item": label,
            "found": False,
            "status": "item_not_seen",
            "current_place": by_id.get(from_place_id) if from_place_id else None,
            "destination": None,
            "path": [],
            "steps": [],
            "hops": [],
            "say": f"I haven't seen your {label} yet.",
        }

    last = history[0]
    destination = destination_for(last)

    current_place = by_id.get(from_place_id) if from_place_id else None
    if destination is None:
        spot_text = f" ({last['spot']})" if last.get("spot") else ""
        return {
            "item": name,
            "found": True,
            "status": "unknown_destination",
            "current_place": current_place,
            "destination": None,
            "path": [],
            "steps": [],
            "hops": [],
            "say": f"Your {name} was last seen{spot_text}, but it isn't linked to a mapped place yet.",
        }

    plan = plan_route(from_place_id, destination["id"], saved["connections"], places)
    steps = [h.instruction for h in plan.hops]
    if plan.status == "ok":
        steps.append(f"Arrive at {destination['name']}: look for your {name} ({last['spot']}).")
        numbered = " ".join(f"Step {i + 1}: {s}" for i, s in enumerate(steps))
        say = f"To reach your {name} at {destination['name']}: {numbered}"
    elif plan.status == "already_there":
        steps = [f"You are already at {destination['name']}. Look for your {name} ({last['spot']})."]
        say = steps[0]
    elif plan.status == "unknown_current":
        say = f"Your {name} was last seen at {destination['name']}, but your starting place is unknown."
    else:
        say = (f"Your {name} was last seen at {destination['name']}, but there is no mapped path from "
               f"{current_place['name'] if current_place else 'your current location'}.")

    return {
        "item": name,
        "found": True,
        "status": plan.status,
        "current_place": current_place,
        "destination": destination,
        "path": plan.path,
        "steps": steps,
        "hops": [asdict(h) for h in plan.hops],
        "say": say,
    }

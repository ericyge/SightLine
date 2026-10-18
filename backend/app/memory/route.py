"""Deterministic path selection over taught place_connections. No vision I/O.

Owner: A. See docs/MEMORY.md.
"""
from collections import deque
from dataclasses import asdict, dataclass, field
import re

from .. import db
from .store import destination_for, get_map, get_place


@dataclass
class RouteHop:
    """One directed edge to speak and follow."""
    from_place_id: str
    to_place_id: str
    instruction: str
    departure_view: str
    arrival_view: str
    inferred_reverse: bool = False


@dataclass
class RoutePlan:
    """BFS result from current place to destination."""
    status: str  # ok | already_there | no_path | unknown_current | unknown_destination
    path: list[str] = field(default_factory=list)  # place ids, current -> destination
    hops: list[RouteHop] = field(default_factory=list)

    @property
    def next_hop(self) -> RouteHop | None:
        return self.hops[0] if self.hops else None


def invert_instruction(instruction: str, from_place: dict, to_place: dict) -> str:
    """Turn a forward walking cue (A -> B) into a natural reverse cue (B -> A)."""
    text = (instruction or "").strip()
    lower = text.casefold()
    target_name = from_place["name"]
    if re.search(r"\bturn left\b", lower):
        return f"Turn right and walk toward {target_name}."
    if re.search(r"\bturn right\b", lower):
        return f"Turn left and walk toward {target_name}."
    return f"Turn around and walk toward {target_name}."


def _all_connections(places: list[dict], connections: list[dict], *, allow_reverse: bool) -> list[dict]:
    if not allow_reverse:
        return list(connections)
    by_id = {p["id"]: p for p in places}
    direct_pairs = {(e["from_place_id"], e["to_place_id"]) for e in connections}
    combined = [dict(e, inferred_reverse=False) for e in connections]
    for edge in connections:
        rev_pair = (edge["to_place_id"], edge["from_place_id"])
        if rev_pair in direct_pairs:
            continue
        src = by_id.get(edge["from_place_id"])
        dst = by_id.get(edge["to_place_id"])
        if not src or not dst:
            continue
        combined.append({
            "from_place_id": dst["id"],
            "to_place_id": src["id"],
            "departure_view": edge["arrival_view"],
            "instruction": invert_instruction(edge["instruction"], src, dst),
            "arrival_view": edge["departure_view"],
            "inferred_reverse": True,
        })
        direct_pairs.add(rev_pair)
    return combined


def plan_route(current_place_id: str | None, destination_place_id: str | None,
               connections: list[dict] | None = None,
               places: list[dict] | None = None,
               *, allow_reverse: bool = False) -> RoutePlan:
    """Shortest path via BFS. Uses taught directed edges first, then optional inferred reverse edges."""
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

    for use_reverse in ([False, True] if allow_reverse else [False]):
        active_edges = _all_connections(places, connections, allow_reverse=use_reverse)
        edges: dict[str, list[dict]] = {}
        for edge in active_edges:
            edges.setdefault(edge["from_place_id"], []).append(edge)

        prev: dict[str, tuple[str, dict]] = {}
        queue = deque([current_place_id])
        seen = {current_place_id}
        found = False
        while queue:
            node = queue.popleft()
            if node == destination_place_id:
                found = True
                break
            for edge in edges.get(node, []):
                nxt = edge["to_place_id"]
                if nxt in seen or nxt not in place_ids:
                    continue
                seen.add(nxt)
                prev[nxt] = (node, edge)
                queue.append(nxt)
        if found and destination_place_id in prev:
            path = [destination_place_id]
            hops_rev: list[RouteHop] = []
            node = destination_place_id
            while node != current_place_id:
                parent, edge = prev[node]
                hops_rev.append(RouteHop(
                    from_place_id=edge["from_place_id"], to_place_id=edge["to_place_id"],
                    instruction=edge["instruction"], departure_view=edge["departure_view"],
                    arrival_view=edge["arrival_view"],
                    inferred_reverse=bool(edge.get("inferred_reverse", False))))
                path.append(parent)
                node = parent
            path.reverse()
            hops_rev.reverse()
            return RoutePlan("ok", path, hops_rev)

    return RoutePlan("no_path", [current_place_id])


def infer_place_for_sighting(sighting: dict | None, places: list[dict] | None = None) -> dict | None:
    """Resolve a sighting's destination place, or match a single unambiguous room/name place."""
    if sighting is None:
        return None
    if sighting.get("place_id"):
        try:
            return get_place(sighting["place_id"])
        except Exception:  # noqa: BLE001
            pass
    places = places if places is not None else get_map()["places"]
    if not places:
        return None
    spot = (sighting.get("spot") or "").casefold()
    room = (sighting.get("room") or "").casefold()
    for p in places:
        if p["name"].casefold() in spot:
            return p
    for word in ("counter", "island", "desk", "table", "door", "shoe rack", "sofa", "bed"):
        if word in spot:
            matches = [p for p in places if word in p["name"].casefold() or word in p["description"].casefold()]
            if matches:
                return matches[0]
    if room and room != "unknown room":
        room_matches = [p for p in places if p["room"].casefold() == room]
        if len(room_matches) == 1:
            return room_matches[0]
    return None


def directions_to_item(item: str, *, from_place_id: str | None = None,
                       allow_reverse: bool = True) -> dict:
    """Build the ordered step-by-step directions list to reach an item's last seen place."""
    name = db.resolve_item(item)
    history = db.recent_sightings(name, 5) if name else []
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
            "steps": [],
            "hops": [],
            "say": f"I haven't seen your {label} yet.",
        }

    # Pick the newest sighting, and if its place_id is null, check if an earlier sighting or spot matches a place
    last = history[0]
    destination = destination_for(last) or infer_place_for_sighting(last, places)
    if destination is None:
        for older in history[1:]:
            destination = destination_for(older) or infer_place_for_sighting(older, places)
            if destination is not None:
                break

    if from_place_id is None and places:
        # Default current place to the most recently added/visited place in the map if not specified
        from_place_id = places[-1]["id"]

    current_place = by_id.get(from_place_id) if from_place_id else None
    if destination is None:
        spot_text = f" ({last['spot']})" if last.get("spot") else ""
        return {
            "item": name,
            "found": True,
            "status": "unknown_destination",
            "current_place": current_place,
            "destination": None,
            "steps": [],
            "hops": [],
            "say": f"Your {name} was last seen{spot_text}, but it isn't linked to a mapped place yet.",
        }

    plan = plan_route(from_place_id, destination["id"], saved["connections"], places,
                      allow_reverse=allow_reverse)
    steps = [h.instruction for h in plan.hops]
    if plan.status == "ok":
        steps.append(f"Arrive at {destination['name']}: look for your {name} ({last['spot']}).")
        numbered = " ".join(f"Step {i + 1}: {s}" for i, s in enumerate(steps))
        say = f"To reach your {name} at {destination['name']}: {numbered}"
    elif plan.status == "already_there":
        steps = [f"You are already at {destination['name']}. Look for your {name} ({last['spot']})."]
        say = steps[0]
    elif plan.status == "unknown_current":
        say = f"Your {name} is at {destination['name']}, but your starting place is unknown."
    else:
        say = (f"Your {name} is at {destination['name']}, but there is no mapped path from "
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

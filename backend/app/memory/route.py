"""Deterministic path selection over taught place_connections. No vision I/O.

Owner: A. See docs/MEMORY.md.
"""
from collections import deque
from dataclasses import dataclass, field

from .store import get_map


@dataclass
class RouteHop:
    """One directed edge to speak and follow."""
    from_place_id: str
    to_place_id: str
    instruction: str
    departure_view: str
    arrival_view: str


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
    """Shortest directed path via BFS. Connections do not imply a reverse edge."""
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
    if not found or destination_place_id not in prev:
        return RoutePlan("no_path", [current_place_id])

    path = [destination_place_id]
    hops_rev: list[RouteHop] = []
    node = destination_place_id
    while node != current_place_id:
        parent, edge = prev[node]
        hops_rev.append(RouteHop(
            from_place_id=edge["from_place_id"], to_place_id=edge["to_place_id"],
            instruction=edge["instruction"], departure_view=edge["departure_view"],
            arrival_view=edge["arrival_view"]))
        path.append(parent)
        node = parent
    path.reverse()
    hops_rev.reverse()
    return RoutePlan("ok", path, hops_rev)

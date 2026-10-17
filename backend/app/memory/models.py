"""Explicitly taught or discovered places and connections; no inferred movement instructions."""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

PlaceID = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class Place(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: PlaceID
    name: ShortText
    room: ShortText
    description: Description
    landmarks: list[ShortText] = Field(default_factory=list, max_length=20)


class PlaceConnection(BaseModel):
    """A directed, manually taught link; facing context belongs to each direction."""
    model_config = ConfigDict(extra="forbid")
    from_place_id: PlaceID
    to_place_id: PlaceID
    departure_view: Description
    instruction: Description
    arrival_view: Description

    @model_validator(mode="after")
    def different_places(self):
        if self.from_place_id == self.to_place_id:
            raise ValueError("A connection must join two different places.")
        return self


class MemoryMap(BaseModel):
    """Complete saved map, which can grow across multiple teaching requests."""
    model_config = ConfigDict(extra="forbid")
    places: list[Place] = Field(default_factory=list)
    connections: list[PlaceConnection] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [p.id for p in self.places]
        edges = [(e.from_place_id, e.to_place_id) for e in self.connections]
        if len(ids) != len(set(ids)) or len(edges) != len(set(edges)):
            raise ValueError("Duplicate places or directed connections in one map update.")
        return self


class MemoryMapUpdate(MemoryMap):
    """Bound one teaching request, not the accumulated map returned by the API."""
    places: list[Place] = Field(default_factory=list, max_length=100)
    connections: list[PlaceConnection] = Field(default_factory=list, max_length=500)


class SightingPlaceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    place_id: PlaceID | None

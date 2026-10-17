"""Teach and inspect location memory without invoking a vision model."""
from fastapi import APIRouter

from ..models import RememberedItem, Sighting
from . import store as memory
from .models import MemoryMap, MemoryMapUpdate, SightingPlaceUpdate

router = APIRouter(prefix="/api/memory", tags=["memory"])


@router.get("/map", response_model=MemoryMap)
def read_map():
    return memory.get_map()


@router.post("/map", response_model=MemoryMap)
def teach_map(update: MemoryMapUpdate):
    return memory.teach_map(update)


@router.get("/items", response_model=list[RememberedItem])
def read_items():
    return memory.recalled_items()


@router.put("/sightings/{sighting_id}/place", response_model=Sighting)
def assign_place(sighting_id: int, update: SightingPlaceUpdate):
    return memory.assign_sighting(sighting_id, update.place_id)

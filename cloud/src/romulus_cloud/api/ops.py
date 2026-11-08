"""Operator JSON routes (APIs §4): registry, incidents, batch, gate, data, fleet, work orders.

Empty in M0; M1 onwards fills it.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/v1", tags=["ops"])

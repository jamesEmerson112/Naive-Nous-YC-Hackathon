"""Device-facing routes (APIs §3): check-in, pulls, installs, incidents, overlays, recordings.

Empty in M0; M1 fills it.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/v1", tags=["device"])

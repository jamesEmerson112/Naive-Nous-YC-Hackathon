"""HTML pages (APIs §4.5): timeline, incident story, data, versions.

Empty in M0; M2 fills it.
"""

from fastapi import APIRouter

router = APIRouter(tags=["web"])

"""Fleet Doctor L1 route (docs/fleet-doctor/interfaces.md §3).

STUB (PR 2, doctor/foundation): an empty router, already mounted in `main.create_app`.
PR 3 (doctor/l1-ask) adds `POST /v1/devices/{device_id}/doctor/ask` here, backed by
`services/doctor_ask.ask(...)`. That route does not use `SessionDep` (FR11): it opens
short sessions from `request.app.state.sessionmaker` around a session-free LLM call.

`utcnow` is imported here so the flows `clock` fixture can patch
`romulus_cloud.api.doctor.utcnow`; the route must read the time through it.
"""

from fastapi import APIRouter

from romulus_cloud.ids import utcnow  # noqa: F401  (patched by the flows `clock` fixture)

router = APIRouter(prefix="/v1", tags=["doctor"])

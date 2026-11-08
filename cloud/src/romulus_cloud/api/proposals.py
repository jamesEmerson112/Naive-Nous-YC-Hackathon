"""Fleet Doctor proposal routes (docs/fleet-doctor/interfaces.md §3).

STUB (PR 2, doctor/foundation): an empty router, already mounted in `main.create_app`.
PR 4 (doctor/l2-consult) adds, backed by `services/proposals.py`:
- `GET  /v1/devices/{device_id}/proposals/{proposal_id}` → `Proposal`, with `X-Sha256`
  over the exact body bytes;
- `POST /v1/devices/{device_id}/proposals/{proposal_id}/result` → 204 (200 on a resend).

`utcnow` is imported here so the flows `clock` fixture can patch
`romulus_cloud.api.proposals.utcnow`; the routes must read the time through it.
"""

from fastapi import APIRouter

from romulus_cloud.ids import utcnow  # noqa: F401  (patched by the flows `clock` fixture)

router = APIRouter(prefix="/v1", tags=["doctor"])

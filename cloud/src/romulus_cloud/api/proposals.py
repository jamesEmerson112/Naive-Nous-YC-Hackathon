"""Fleet Doctor proposal routes (docs/fleet-doctor/interfaces.md §3; APIs routes 3.9-3.10).

Both are device routes, keyed by `{device_id}` from the path with no auth, like the rest.
The device treats any non-200 as "no answer".

- `GET  /v1/devices/{device_id}/proposals/{proposal_id}` → the `Proposal` body bytes, with
  `X-Sha256` over exactly those bytes. `404 unknown_proposal` (also another device's),
  `409 fleet_frozen`, `410 proposal_gone` (no longer offered, expired, or held back by the
  serve-time safety gate, `proposals.serve_refusal`).
- `POST /v1/devices/{device_id}/proposals/{proposal_id}/result` → `204`, or `200` for a
  resend of the recorded result. `404 unknown_proposal`, `409 proposal_result_conflict`,
  `422` when `applied` has no `overlay_id`.

`utcnow` is imported here so the flows `clock` fixture can patch
`romulus_cloud.api.proposals.utcnow`; the routes read the time through it.
"""

from typing import Any

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from romulus_cloud.api.deps import SessionDep, SettingsDep
from romulus_cloud.api.device import DUPLICATE, RawBody, check_contract, parse
from romulus_cloud.ids import utcnow
from romulus_cloud.services import proposals
from romulus_contracts import SHA256_HEADER, ProposalResult

router = APIRouter(prefix="/v1", tags=["doctor"])


@router.get("/devices/{device_id}/proposals/{proposal_id}")
def get_proposal(
    device_id: str, proposal_id: str, session: SessionDep, settings: SettingsDep
) -> Response:
    """The body bytes as dumped once by `proposal_body_and_sha256`, never re-encoded. Served
    only while `proposals.serve_refusal` passes under this request's settings."""
    body, sha256 = proposals.get_for_device(
        session, device_id, proposal_id, utcnow(), settings=settings
    )
    return Response(body, media_type="application/json", headers={SHA256_HEADER: sha256})


@router.post(
    "/devices/{device_id}/proposals/{proposal_id}/result", status_code=204, response_model=None
)
def proposal_result(
    device_id: str,
    proposal_id: str,
    session: SessionDep,
    settings: SettingsDep,
    raw: dict[str, Any] = RawBody,
):
    check_contract(raw, required=False)
    body = parse(ProposalResult, raw)
    created = proposals.record_result(
        session, device_id, proposal_id, body, raw, utcnow(), safety_paths=settings.safety_paths
    )
    session.commit()
    return Response(status_code=204) if created else JSONResponse(DUPLICATE, status_code=200)

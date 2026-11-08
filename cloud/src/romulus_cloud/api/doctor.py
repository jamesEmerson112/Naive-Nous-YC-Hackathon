"""Fleet Doctor L1 route (docs/fleet-doctor/interfaces.md §3; APIs §3 route 3.8).

`POST /v1/devices/{device_id}/doctor/ask`: the healer asks mid-heal and gets hypotheses back
(`services/doctor_ask.ask`). It does **not** use `SessionDep` (FR11): the service opens short
sessions from `request.app.state.sessionmaker` around a session-free LLM call. The route is a
sync `def`, so FastAPI runs it in its threadpool and a slow model never blocks the event loop.

The LLM comes from the `get_doctor_llm` dependency (built once per process and settings by
`build_doctor_llm`), so tests swap it with `app.dependency_overrides[get_doctor_llm]`.

`utcnow` is imported here so the flows `clock` fixture can patch
`romulus_cloud.api.doctor.utcnow`; the route reads the time through it.
"""

import threading
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from romulus_cloud.adapters.llm import Llm, LlmConfigError
from romulus_cloud.api.deps import SettingsDep
from romulus_cloud.api.device import RawBody, check_contract, parse
from romulus_cloud.ids import utcnow
from romulus_cloud.services import doctor_ask
from romulus_cloud.services.doctor_llm import build_doctor_llm
from romulus_contracts import DoctorAsk

router = APIRouter(prefix="/v1", tags=["doctor"])

_llms: dict[tuple[str, ...], Llm] = {}
_llms_lock = threading.Lock()


def get_doctor_llm(settings: SettingsDep) -> Llm | doctor_ask.Unconfigured | None:
    """The Doctor's LLM, cached per process for these settings; None while DOCTOR_MODE=off.
    A live LLM whose settings `build_doctor_llm` refuses comes back as
    `doctor_ask.Unconfigured` (not cached), so the service answers 502 after its 404, 503 and
    409 checks and puts the reason on the timeline."""
    if settings.doctor_mode == "off":
        return None
    key = (
        settings.doctor_mode,
        settings.llm_provider,
        settings.llm_base_url or "",
        settings.llm_model or "",
        settings.llm_api_key.get_secret_value(),
    )
    with _llms_lock:
        if key not in _llms:
            try:
                _llms[key] = build_doctor_llm(settings)
            except LlmConfigError as exc:
                return doctor_ask.Unconfigured(str(exc))
        return _llms[key]


DoctorLlmDep = Annotated[Llm | doctor_ask.Unconfigured | None, Depends(get_doctor_llm)]


@router.post("/devices/{device_id}/doctor/ask")
def ask(
    device_id: str,
    request: Request,
    settings: SettingsDep,
    llm: DoctorLlmDep,
    raw: dict[str, Any] = RawBody,
) -> dict:
    """`DoctorAsk` → `200 DoctorAdvice`. Any other status is "no answer" to the device."""
    check_contract(raw, required=False)
    body = parse(DoctorAsk, raw)
    advice = doctor_ask.ask(
        request.app.state.sessionmaker,
        device_id,
        body,
        raw,
        utcnow(),
        settings=settings,
        llm=llm,
    )
    return advice.model_dump(mode="json")

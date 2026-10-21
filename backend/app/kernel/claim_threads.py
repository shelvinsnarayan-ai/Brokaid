"""R3.4 — claim message threads: one inbox thread per claim (thread_id "claim-<claim_id>"), on the existing ledgered Ask model.

Client messages are ClientQuestionAsked events carrying claim_id (no AI auto-reply); broker replies use inbox.reply unchanged.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.db.tenant_safe import wrap
from app.kernel import inbox
from app.kernel.tenant_ctx import use_tenant

CLOSED = ("closed", "withdrawn")
CLOSED_NOTE = "This claim is closed. Ask your broker in the main thread if you need anything."
PREFIX = "claim-"


def thread_id_for(claim_id: str) -> str:
    return f"{PREFIX}{claim_id}"


def is_closed(c: dict) -> bool:
    return (c.get("stage") or c.get("status")) in CLOSED


_PRODUCT_CLASS = (("liability", "Liability"), ("cyber", "Cyber"), ("motor", "Motor"), ("vehicle", "Motor"), ("fleet", "Motor"),
                  ("tools", "Tools"), ("contents", "Property"), ("pack", "Property"), ("building", "Property"), ("property", "Property"),
                  ("marine", "Marine"))


def policy_class(product: str | None) -> str | None:
    p = (product or "").lower()
    return next((v for k, v in _PRODUCT_CLASS if k in p), None)


def label(c: dict, things: dict | None = None) -> str:
    """'Motor claim · Ford Ranger XLT XYZ-789'; without a vehicle, the policy name, and the policy's own class when the
    claim says motor but the policy isn't a motor/fleet one (e.g. 'Liability claim · Marine Liability')."""
    cls = (c.get("claim_class") or c.get("claim_type") or "other").split("_")[0].lower()
    rego = (c.get("incident") or {}).get("rego")
    if cls == "motor" and rego:
        thing = (things or {}).get(str(rego).upper())
        return f"Motor claim · {thing} {rego}" if thing else f"Motor claim · {rego}"
    product, pcls = c.get("product"), policy_class(c.get("product"))
    title = (pcls or "Other") if (cls == "motor" and pcls != "Motor") or cls == "other" else cls.title()
    return f"{title} claim · {product or c.get('claim_reference')}"


async def vehicle_names(store, tid: str, client_id: str) -> dict:
    out = {}
    async with use_tenant(tid):
        async for p in wrap(store).policies.find({"client_id": client_id, "schedule.vehicles": {"$exists": True}}, {"schedule.vehicles": 1}):
            for v in p["schedule"]["vehicles"]:
                if v.get("rego"):
                    out[str(v["rego"]).upper()] = " ".join(str(x) for x in (v.get("make"), v.get("model")) if x)
    return out


async def thread_for(store, tid: str, client_id: str, claim_id: str, include_tests: bool = False) -> dict | None:
    tid_ = thread_id_for(claim_id)
    return next((t for t in await inbox.load_threads(store, tid, client_id=client_id, include_tests=include_tests)
                 if t["thread_id"] == tid_), None)


async def post_client(store, tid: str, *, user_id: str, role: str, claim: dict, message: str, msg_id: str | None = None,
                      received_at: datetime | None = None) -> dict:
    """Ledgered client message on the claim thread. A reply-by promise starts only when nothing is already awaiting a reply."""
    thread_id = thread_id_for(claim["claim_id"])
    msg_id = msg_id or str(uuid.uuid4())
    received = received_at or datetime.now(timezone.utc)
    t = await thread_for(store, tid, claim["client_id"], claim["claim_id"], include_tests=True)
    payload = {"envelope_id": thread_id, "client_id": claim["client_id"], "claim_id": claim["claim_id"], "message": message,
               "received_at": inbox._utc(received).isoformat(), "msg_id": msg_id}
    if claim.get("test"):
        payload["test"] = True
    ev = await inbox._emit(store, tid, "ClientQuestionAsked", payload, f"ask:in:{thread_id}:{msg_id}",
                           actor={"kind": "human", "id": user_id, "name": role})
    if ev is not None and (t is None or t["answered"]):
        from app.kernel.holidays import state_for
        await inbox._emit(store, tid, "PromiseMade", {
            "thread_id": thread_id, "client_id": claim["client_id"], "claim_id": claim["claim_id"], "received_at": inbox._utc(received).isoformat(),
            "reply_by": inbox.reply_by_for(inbox._utc(received), await state_for(store, tid)).isoformat(), "data_basis": inbox.BASIS,
            "msg_id": msg_id},
            f"promise:{thread_id}:{msg_id}")
    return {"thread_id": thread_id, "msg_id": msg_id, "replayed": ev is None}

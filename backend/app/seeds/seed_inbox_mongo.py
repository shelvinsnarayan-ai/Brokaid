"""R0.7 — seed a few client Ask threads so the broker Inbox has content (idempotent)."""
from datetime import datetime, timedelta, timezone

from app.db.tenant_safe import wrap
from app.kernel import inbox
from app.kernel.tenant_ctx import use_tenant
from app.kernel.trail_dispatch import append_via_dispatch
from app.seeds.seed_things_mongo import NARAYAN

THREADS = [
    ("seed-ask-kai-1", None, [
        ("ClientQuestionAsked", "client", "When does my public liability policy expire?"),
        ("ClientMessageReleased", "broker", "Your public liability policy runs to the date shown on your Cover page. We will be in touch before then about renewal."),
    ]),
    ("seed-ask-kai-2", None, [
        ("ClientQuestionAsked", "client", "Is my trailer covered when it is hitched to the Hilux?"),
    ]),
    ("seed-ask-narayan-1", NARAYAN, [
        ("ClientQuestionAsked", "client", "We have a small roof leak after the storm. Should we lodge a claim?"),
    ]),
]


async def seed_mongo(db, tenant_id: str, client_id: str) -> int:
    safe, n = wrap(db), 0
    async with use_tenant(tenant_id):
        for env, cid, msgs in THREADS:
            cid = cid or client_id
            for i, (type_, who, text) in enumerate(msgs):
                key = f"{env}:{i}"
                if await safe.events.find_one({"idempotency_key": key}):
                    continue
                payload = {"envelope_id": env, "client_id": cid, "message": text}
                if type_ == "ClientMessageReleased":
                    payload["classifier_verdict"] = {"advice_classification": "factual"}
                await append_via_dispatch(
                    db, tenant_id, type_, idempotency_key=key, payload=payload,
                    actor={"kind": "human", "id": "seed", "name": "client_owner" if who == "client" else "ae"})
                n += 1
    await inbox.ask(db, tenant_id, user_id="seed", client_id=NARAYAN, thread_id="seed-ask-narayan-2",
                    message="Can you send a copy of our home building certificate to the bank?",
                    received_at=datetime.now(timezone.utc) - timedelta(days=4))
    await inbox.backfill(db, tenant_id)
    return n

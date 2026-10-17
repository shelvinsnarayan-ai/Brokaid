"""R0.6 — G1 registry: the agent registry (package × class × autonomy) and the
sole source of operational counts shown on operator surfaces (data_basis=registry).

Brain.invoke refuses any agent not registered here (AgentInvocationRefused).
"""
from __future__ import annotations

from app.db.tenant_safe import TenantSafeDatabase, wrap
from app.kernel.governance import AGENT_LABELS, AGENT_PACKAGES, PACKAGE_LABELS
from app.kernel.tenant_ctx import use_tenant
from app.kernel.test_data import real_policy_filter, test_client_ids, test_event_filter  # noqa: F401

AUTONOMY = {
    "L0": "Shadow — observes only, never acts",
    "L1": "Drafts — a broker approves every action",
    "L2": "Acts within a rule (straight-through), sampled by a broker",
}
CLASS_LABELS = {
    "commercial_motor": "Commercial motor", "marine_liability": "Marine liability",
    "cyber": "Cyber", "home": "Home", "private_motor": "Private motor", "all": "All classes",
}
_ENTRIES = {
    "intake": (["commercial_motor", "marine_liability", "cyber", "home", "private_motor"], "L1"),
    "placement": (["commercial_motor", "marine_liability", "cyber"], "L1"),
    "claims": (["commercial_motor", "marine_liability"], "L1"),
    "compliance": (["all"], "L1"), "liaison": (["all"], "L1"),
    "certs": (["commercial_motor", "marine_liability"], "L2"),
    "docs": (["all"], "L1"), "notify": (["all"], "L1"), "finance": (["all"], "L0"),
    "retention": (["all"], "L0"), "terrain": (["all"], "L0"), "campaign": (["all"], "L0"),
    "mock": (["all"], "L0"),
}
REGISTRY = {a: {"agent": a, "package": AGENT_PACKAGES[a], "classes": c, "autonomy": lvl}
            for a, (c, lvl) in _ENTRIES.items()}


class UnregisteredAgent(Exception):
    pass


def is_registered(agent_name: str) -> bool:
    return agent_name in REGISTRY


def listing() -> list[dict]:
    rows = []
    for a, e in REGISTRY.items():
        for c in e["classes"]:
            rows.append({"package": PACKAGE_LABELS[e["package"]], "assistant": AGENT_LABELS[a],
                         "class": CLASS_LABELS[c], "autonomy": e["autonomy"],
                         "autonomy_label": AUTONOMY[e["autonomy"]], "data_basis": "registry"})
    return rows


async def counts(store, tenant_id: str) -> dict:
    safe = store if isinstance(store, TenantSafeDatabase) else wrap(store)
    async with use_tenant(tenant_id):
        real = await test_event_filter(safe)
        events = await safe.events.count_documents(real)
        open_cards = await safe.proj_queue.count_documents({"resolved_at": None, "client_id": {"$nin": await test_client_ids(safe)}})
        matters = await safe.events.count_documents({**real, "type": "ClientChangeRequested"})
        things = await safe.things.count_documents({})
        refused = await safe.events.count_documents({"type": "AgentInvocationRefused"})
    # tenant-safe: system-only — tenant registry count
    tenants = await (store._db if isinstance(store, TenantSafeDatabase) else store).tenants.count_documents({})
    return {k: {"value": v, "data_basis": "registry"} for k, v in {
        "ledger_events": events, "open_queue_items": open_cards, "matters": matters,
        "things": things, "refused_agent_calls": refused, "tenants": tenants}.items()}

"""Notifications — dev-mode file rendering + optional Resend send.

Design invariant: whichever branch fires, the SAME payload also lands
as an `InAppNotification` ledger event so the Notifications tray reads
identical content to the email. `test_notification_dev_mode.py` audits
this equivalence.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from app.kernel.trail import append as trail_append


DEV_EMAIL_DIR = Path(os.environ.get("BROKAID_EMAIL_DEV_DIR") or "/tmp/brokaid_emails")


NotifyKind = Literal[
    "claim_lodged",
    "claim_status_update",
    "claim_resolved",
]


def _render_email(kind: NotifyKind, payload: dict[str, Any]) -> dict[str, str]:
    """Return `{subject, html, text}` for the given kind.

    Harbour palette, minimal. Every template ends in a broker-attributed
    next-step line.
    """
    subject = _subject_for(kind, payload)
    lines = _body_lines_for(kind, payload)
    text = "\n\n".join(lines)
    body_html = "".join(
        f"<p style=\"margin:0 0 12px 0;\">{_html_escape(l)}</p>" for l in lines
    )
    html = (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>"
        + _html_escape(subject) + "</title></head>"
        "<body style=\"font-family:-apple-system,Segoe UI,sans-serif;"
        "background:#F4EFE6;color:#0B3B4B;padding:24px;line-height:1.5\">"
        "<div style=\"max-width:560px;margin:0 auto;background:#fff;"
        "padding:32px;border-radius:8px\">"
        f"<h1 style=\"margin:0 0 16px 0;color:#0B3B4B;\">{_html_escape(subject)}</h1>"
        f"{body_html}"
        "<hr style=\"border:0;border-top:1px solid #E8E2D6;margin:24px 0\">"
        f"<p style=\"color:#5A5C60;font-size:12px;margin:0\">"
        f"Sent by {_html_escape(payload.get('brokerage_display') or 'your broker')}."
        "</p>"
        "</div></body></html>"
    )
    return {"subject": subject, "html": html, "text": text}


def _subject_for(kind: NotifyKind, p: dict[str, Any]) -> str:
    ref = p.get("claim_reference") or ""
    if kind == "claim_lodged":
        return f"We've lodged your claim {ref}"
    if kind == "claim_status_update":
        return f"Update on your claim {ref}"
    if kind == "claim_resolved":
        return f"Your claim {ref} is resolved"
    return f"Notification about claim {ref}"


def _body_lines_for(kind: NotifyKind, p: dict[str, Any]) -> list[str]:
    ref = p.get("claim_reference") or "(reference pending)"
    insurer = p.get("insurer_display")
    broker = p.get("brokerage_display") or "your broker"
    summary = p.get("summary") or ""
    if kind == "claim_lodged":
        return [
            f"Hi {p.get('client_display') or 'there'},",
            f"Your claim {ref} has been lodged with {insurer}.",
            summary or "We'll keep you posted as it moves.",
            f"Any questions, reply here — {broker} is across it.",
        ]
    if kind == "claim_status_update":
        return [
            f"Hi {p.get('client_display') or 'there'},",
            f"There's an update on claim {ref}.",
            summary or "See the timeline in your app for details.",
            f"— {broker}",
        ]
    if kind == "claim_resolved":
        return [
            f"Hi {p.get('client_display') or 'there'},",
            f"Your claim {ref} is now resolved.",
            summary or "The final outcome is recorded in your timeline.",
            f"Thanks for your patience — {broker}",
        ]
    return [summary or "(no content)"]


def _html_escape(s: str) -> str:
    return (
        (s or "").replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace("\"", "&quot;")
    )


async def render_and_send(
    *,
    pool,
    tenant_id: str,
    kind: NotifyKind,
    payload: dict[str, Any],
    recipient: dict[str, Any],
    source_event_id: str,
) -> dict[str, Any]:
    """Render the email and either send via Resend OR write to a dev file.

    Always ALSO emits an `InAppNotification` ledger event so the tray
    shows the same content as the email.
    """
    rendered = _render_email(kind, payload)
    notif_id = str(uuid.uuid4())

    # 1. Send path — Resend if key present, else dev-mode file.
    resend_key = (os.environ.get("RESEND_API_KEY") or "").strip()
    dev_mode = not resend_key
    file_path: str | None = None
    resend_id: str | None = None

    if dev_mode:
        DEV_EMAIL_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        safe_recipient = "".join(
            c if c.isalnum() or c in "._-@" else "_"
            for c in (recipient.get("email") or "unknown")
        )
        file_path = str(DEV_EMAIL_DIR / f"{ts}_{safe_recipient}.html")
        Path(file_path).write_text(rendered["html"], encoding="utf-8")
        # Also drop the text alongside for CI inspection.
        Path(file_path).with_suffix(".txt").write_text(
            rendered["text"], encoding="utf-8",
        )
        await trail_append(
            pool, tenant_id, "EmailRenderedDevMode",
            actor={"kind": "system", "id": "notify", "name": "notify"},
            payload={
                "notification_id": notif_id,
                "recipient": recipient,
                "kind": kind,
                "subject": rendered["subject"],
                "path": file_path,
                "source_event_id": source_event_id,
                "template_payload": payload,
                "mocked": True,
            },
            idempotency_key=f"emaildev:{notif_id}",
            mocked=True,
        )
    else:
        # Live Resend send. Called through the integration path.
        resend_id = await _resend_send(
            api_key=resend_key,
            to=recipient.get("email"),
            subject=rendered["subject"],
            html=rendered["html"],
            text=rendered["text"],
        )
        await trail_append(
            pool, tenant_id, "EmailSent",
            actor={"kind": "system", "id": "notify", "name": "notify"},
            payload={
                "notification_id": notif_id,
                "recipient": recipient,
                "kind": kind,
                "subject": rendered["subject"],
                "resend_id": resend_id,
                "source_event_id": source_event_id,
                "template_payload": payload,
                "mocked": False,
            },
            idempotency_key=f"emailsent:{notif_id}",
            mocked=False,
        )

    # 2. Always emit InAppNotification event — same source, same content.
    in_app_ev = await trail_append(
        pool, tenant_id, "InAppNotification",
        actor={"kind": "system", "id": "notify", "name": "notify"},
        payload={
            "notification_id": notif_id,
            "client_id": recipient.get("client_id"),
            "kind": kind,
            "subject": rendered["subject"],
            "body_text": rendered["text"],
            "source_event_id": source_event_id,
            "template_payload": payload,
        },
        idempotency_key=f"inapp:{notif_id}",
    )

    # 3. Project into proj_notifications.
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute(
                "SELECT set_config('app.tenant_id', $1, true)", tenant_id
            )
            await c.execute(
                """
                INSERT INTO proj_notifications
                  (notification_id, tenant_id, client_id, source_event_id,
                   ts, kind, subject, body_text)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid,
                        NOW(), $5, $6, $7)
                ON CONFLICT (notification_id) DO NOTHING
                """,
                notif_id, tenant_id, recipient.get("client_id"),
                source_event_id, kind, rendered["subject"], rendered["text"],
            )

    return {
        "notification_id": notif_id,
        "dev_mode": dev_mode,
        "path": file_path,
        "resend_id": resend_id,
        "in_app_event_id": in_app_ev.event_id,
        "subject": rendered["subject"],
    }


async def _resend_send(
    *, api_key: str, to: str | None, subject: str, html: str, text: str,
) -> str:
    """Thin Resend adapter. Kept local to keep the module test-friendly."""
    import httpx

    if not to:
        raise ValueError("recipient email required for Resend send")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": os.environ.get(
                    "RESEND_FROM", "brokAId <notifications@brokaid.example>"
                ),
                "to": [to],
                "subject": subject,
                "html": html,
                "text": text,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return str(data.get("id") or "")

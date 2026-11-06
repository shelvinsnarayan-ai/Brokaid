"""R3.11 multi-model review runner. Each model reviews the same context pack independently (no shared output), at low temperature,
returning strict JSON (one retry on invalid). Then deterministic consolidation, report (MD + PDF) to Object Storage, ledgered ReviewPublished.
Usage: python -m app.scripts.r311_review /tmp/r311  (after scripts/review/r311_capture.py)"""
# tenant-safe: system-only (Harbourline resolved by slug; review stored in that tenant)
import asyncio
import base64
import io
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from PIL import Image

from app.kernel import reviews as rv

MODELS = [("GPT-5.5", "openai", "gpt-5.5"), ("Claude Opus 5.5", "anthropic", "claude-opus-5-5"),
          ("Gemini 3.1 Pro", "gemini", "gemini-3.1-pro-preview"), ("Claude Sonnet 5.5", "anthropic", "claude-sonnet-5-5")]

FEATURES = """brokAId is an AI-assisted insurance broker platform (Australia). Two surfaces: the client app (/c, mobile-first, bottom bar Home/Activity/+/Messages/Profile)
and the broker app (/o: AE, Principal, Operator roles). Built features relevant here:
- Client: Home with cover cards and attention items; policy detail (what is covered, sum insured, excess, period); Activity with payments due and Pay;
  + sheet (certificate, change cover, claim, documents); certificate of currency requests naming a holder; change-of-cover requests; claim lodgement;
  claim tracker with stages, "What we need from you", and a Repair quote card (repairer, total incl GST, View quote, fixed insurer disclaimer);
  Messages: broker thread and per-claim threads with photo/video attachments (metadata stripped) and broker PDF attachments (e.g. repair quotes);
  Profile, Documents, Notification settings, Security (password + authenticator); renewal review/accept; client roles incl. bookkeeper/contact.
- Broker: AE inbox and claim Messages panel (reply with photos/PDFs, labelled files); Principal approval of advice-like replies including the exact files;
  queue/Today with endorsement approvals; client 360 view; renewal pipeline and review; claims list/detail with Documents (quote summary edit);
  Operator Governance: Release Check, kill switches, exposure report; Alerts settings with a push test card (device checklist, test send, per-device result).
Not in scope of this evidence: anything not shown in the screenshots."""
MOCKED = """MOCKED / demo in this preview (not production): insurer integrations (quotes, binding, claim submission to insurers) are MOCKED;
payments are MOCKED (no real money moves); email delivery is off; the data is the fictional Harbourline demo brokerage and fictional clients;
the login page shows a preview-only "Demo sign-in" panel and a "DEV AUTH — NOT FOR PRODUCTION" banner that do not appear in production."""

SCHEMA = """Return ONLY one JSON object, no prose, no code fences:
{"heuristics": {"<row_key>": {"client": {"score": 0-4 | "insufficient evidence", "evidence": ["<screenshot id>", ...], "reasoning": "<one line>", "fix": "<single most valuable fix>"},
                              "broker": { same; use "n/a" only for row am_i_covered } } ... all 14 rows ...},
 "tasks": {"J1": {"farmer": {"success": "Y"|"Partial"|"N"|"n/a", "measured_taps": <int from the measured data or null>, "friction": "<one line>"}, ... all 6 archetypes}, ... J1..J7}}"""


def pack(man: dict) -> tuple[str, list[Path]]:
    rows = "\n".join(f"- {k}: {label} (A20 baseline client {bc}, broker {bb if bb is not None else 'n/a'})" for k, label, bc, bb in rv.ROWS)
    arch = ", ".join(f"{k} = {v}" for k, v in rv.ARCHETYPES)
    jobs = "\n".join(f"- {j}: {t}" for j, t in rv.JOBS)
    shots = [s for s in man["screens"]]
    listing = "\n".join(f"Image {i + 1} = {s['id']}: {s['caption']} (route {s['url']})" for i, s in enumerate(shots))
    tasks = "\n".join(f"- {t['job']} {t['title']}: measured {t['taps']} tap(s), {t['screens']} screen change(s), reached goal screen: {t['reached_goal_screen']}; "
                      f"{t['note']}; sequence {', '.join(t['sequence'])}" for t in man["tasks"])
    text = f"""You are an independent senior UX reviewer. Review the brokAId PREVIEW build (not production) against the A20 Production UX Review criteria.

FEATURE SUMMARY (factual):
{FEATURES}

{MOCKED}

A20 §2 HEURISTIC MATRIX: Nielsen's ten plus four insurance-specific heuristics, scored {rv.SCALE}
Rows (use these keys):
{rows}

A20 §3 TASK MATRIX: can each archetype do the job (taps to success from client Home; ✗ = cannot). Archetypes: {arch}.
A20 marks J1×professional and J2×household as n/a. Jobs:
{jobs}

MEASURED TASK PATHS (Playwright at 390 px from client Home, stopping before any final submit; do NOT guess tap counts, use these):
{tasks}

EVIDENCE: {len(shots)} screenshots, attached in this order:
{listing}

RULES: Score only from the evidence. Every numeric score must cite screenshot ids from the list above. Do not invent screens that aren't in the evidence;
if a row can't be judged from the evidence, use "insufficient evidence". One-line reasoning; one single most valuable fix per cell.
For tasks, judge each archetype's likelihood of success (Y/Partial/N) and friction from the measured data and the screenshots.

{SCHEMA}"""
    return text, [Path(man["dir"], "shots", s["file"]) for s in shots]


def _jpeg(p: Path) -> str:
    im = Image.open(p).convert("RGB")
    if im.height > 2.6 * im.width:  # very long full-page shots: keep the top part legible
        im = im.crop((0, 0, im.width, int(2.6 * im.width)))
    im.thumbnail((1100, 1100))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=60)
    return base64.b64encode(b.getvalue()).decode()


def _parse(txt: str):
    t = txt.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1].rsplit("```", 1)[0]
    i, j = t.find("{"), t.rfind("}")
    return json.loads(t[i:j + 1])


async def ask(name, provider, model, text, images, ids) -> dict:
    from emergentintegrations.llm.chat import ImageContent, LlmChat, UserMessage
    imgs = [ImageContent(image_base64=_jpeg(p)) for p in images]
    log, prompt = [], text
    for attempt in (1, 2):
        chat = LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"r311-{model}-{uuid.uuid4()}",
                       system_message="You are a meticulous UX reviewer. Output strict JSON only.").with_model(provider, model)
        temp = 0.2
        try:
            chat = chat.with_params(temperature=temp, max_tokens=20000)
            raw = await chat.send_message(UserMessage(text=prompt, file_contents=imgs))
        except Exception as e:  # noqa: BLE001
            if "temperature" not in str(e).lower():
                raise
            temp = "provider default (temperature not accepted)"
            chat = LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"r311-{model}-{uuid.uuid4()}",
                           system_message="You are a meticulous UX reviewer. Output strict JSON only.").with_model(provider, model).with_params(max_tokens=20000)
            raw = await chat.send_message(UserMessage(text=prompt, file_contents=imgs))
        try:
            out = _parse(raw)
            errs = rv.validate(out, ids)
        except Exception as e:  # noqa: BLE001
            out, errs = None, [f"not JSON: {e}"]
        log.append({"attempt": attempt, "errors": errs[:20], "temperature": temp})
        if not errs:
            return {"model": name, "provider": provider, "model_id": model, "output": out, "raw": raw, "attempts": log, "valid": True}
        prompt = text + "\n\nYOUR PREVIOUS ANSWER WAS INVALID: " + "; ".join(errs[:15]) + "\nReturn the full corrected JSON object only."
    return {"model": name, "provider": provider, "model_id": model, "output": out, "raw": raw, "attempts": log, "valid": False}


async def main(d: str) -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    man = json.loads(Path(d, "manifest.json").read_text())
    man["dir"] = d
    import subprocess
    man["sha"] = subprocess.run(["git", "-C", "/app", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    text, images = pack(man)
    print("images:", len(images), "prompt chars:", len(text))
    ids = {s["id"] for s in man["screens"]}
    prev = {r["model"]: r for r in json.loads(Path(d, "models.json").read_text())["results"]} if Path(d, "models.json").exists() else {}
    todo = [x for x in MODELS if not (prev.get(x[0]) or {}).get("valid")]  # re-run only models without a valid result
    print("running:", [x[0] for x in todo])
    res = await asyncio.gather(*(ask(n, p, m, text, images, ids) for n, p, m in todo), return_exceptions=True)
    got = {x[0]: (r if isinstance(r, dict) else {"model": x[0], "provider": x[1], "model_id": x[2], "valid": False, "error": repr(r)[:500]})
           for x, r in zip(todo, res)}
    for n, r in got.items():
        if n in prev:
            r["previous_runs"] = (prev[n].get("previous_runs") or []) + [{"attempts": prev[n].get("attempts"), "error": prev[n].get("error")}]
    out = [got.get(n) or prev[n] for n, _p, _m in MODELS]
    Path(d, "models.json").write_text(json.dumps({"prompt": text, "results": out}, indent=1, default=str))
    for r in out:
        print(r["model"], "valid" if r.get("valid") else "INVALID", r.get("attempts") or r.get("error"))
    if not all(r.get("valid") for r in out):
        raise SystemExit("not all models returned valid JSON")
    from app.kernel import review_publish
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    print(await review_publish.publish(db, d, man, out, text, datetime.now(timezone.utc)))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/r311"))

"""Application tracker: statuses, dated events, follow-up timing and reports."""
import csv
import html
import io
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .ai import WorkflowError

STATUSES = ("drafted", "applied", "interview", "offer", "hired", "rejected", "no_response", "offer_declined", "withdrawn")
LABELS = {"drafted": "Drafted", "applied": "Applied", "interview": "Interviewing", "offer": "Offer",
          "hired": "Hired", "rejected": "Rejected", "no_response": "No response",
          "offer_declined": "Offer declined", "withdrawn": "Withdrawn"}
FINAL = {"hired", "rejected", "no_response", "offer_declined", "withdrawn"}
SENT = {"applied", "interview", "offer", "hired", "rejected", "no_response", "offer_declined"}
REACHED_INTERVIEW = {"interview", "offer", "hired", "offer_declined"}
REACHED_OFFER = {"offer", "hired", "offer_declined"}
CHANNELS = ("email", "portal", "linkedin", "referral", "other")
STAGES = ("Phone screen", "First interview", "Technical or case interview", "Final interview", "Assessment centre", "Other")
FOLLOWUP_DAYS = 10
CALIBRATION_MINIMUM = 3
MAX_FOLLOWUPS = 2


def today():
    return datetime.now(ZoneInfo("Europe/Zurich")).date()


def parse_day(value, default=None):
    if not value:
        return default or today()
    try:
        day = date.fromisoformat(str(value)[:10])
    except ValueError as e:
        raise WorkflowError("Use a date in the form YYYY-MM-DD.") from e
    if (day - today()).days > 366:
        raise WorkflowError("That date is too far in the future.")
    return day


def text_field(value, name, limit):
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise WorkflowError(f"Please keep the {name} under {limit:,} characters.")
    return value.strip()


def job_summary(job):
    keys = ("id", "title", "company", "location", "url", "fit", "overall", "requirements", "keywords",
            "gaps", "language", "deadline", "deadline_date", "posting_text")
    return {k: job[k] for k in keys if k in job}


def record(application, job, status, day=None, note="", channel=None, contact=None, materials=None):
    """Add a dated status event. The first move to a sent status freezes what was sent."""
    if status not in STATUSES:
        raise WorkflowError("Choose a valid application status.")
    if channel is not None and channel not in CHANNELS:
        raise WorkflowError("Choose how you applied: email, portal, LinkedIn, referral or other.")
    day = parse_day(day)
    application = application or {"job": job_summary(job), "status": "drafted", "events": [], "followups": [],
                                  "channel": "", "contact": "", "submitted": None, "interview": None,
                                  "draft_followup": None, "created_at": day.isoformat()}
    if status in SENT and application["submitted"] is None and materials:
        application["submitted"] = {k: materials["materials"][k] for k in ("cv", "cover_letter", "email")}
        application["submitted"]["language"] = materials["language"]
    application["status"] = status
    application["events"].append({"date": day.isoformat(), "kind": "status", "status": status, "note": note})
    if channel is not None:
        application["channel"] = channel
    if contact is not None:
        application["contact"] = contact
    return application


def add_note(application, note, day=None):
    application["events"].append({"date": parse_day(day).isoformat(), "kind": "note", "status": application["status"], "note": note})
    return application


def last_activity(application):
    # Drafting sends nothing, so it never resets the quiet clock of a sent application.
    days = [parse_day(e["date"]) for e in application["events"] if e.get("status") != "drafted"]
    days += [parse_day(f["date"]) for f in application.get("followups", [])]
    return max(days) if days else parse_day(application.get("created_at"))


def quiet_days(application, now=None):
    return ((now or today()) - last_activity(application)).days


def followup_due(application, now=None):
    return (application["status"] not in FINAL and application["status"] != "drafted"
            and quiet_days(application, now) >= FOLLOWUP_DAYS
            and sum(1 for f in application.get("followups", []) if f["kind"] == "followup") < MAX_FOLLOWUPS)


def reached(application, statuses):
    return application["status"] in statuses or any(e.get("status") in statuses for e in application["events"])


def stats(applications, now=None):
    values = list(applications.values())
    counts = {s: sum(1 for a in values if a["status"] == s) for s in STATUSES}
    sent = [a for a in values if reached(a, SENT)]
    interviews = sum(1 for a in sent if reached(a, REACHED_INTERVIEW))
    offers = sum(1 for a in sent if reached(a, REACHED_OFFER))
    responded = sum(1 for a in sent if reached(a, REACHED_INTERVIEW | {"rejected"}))
    return {"tracked": len(values), "applied": len(sent), "interviews": interviews, "offers": offers,
            "active": sum(1 for a in values if a["status"] not in FINAL),
            "response_rate": round(100 * responded / len(sent)) if sent else 0,
            "interview_rate": round(100 * interviews / len(sent)) if sent else 0,
            "followups_due": sum(1 for a in values if followup_due(a, now)),
            "counts": counts}


def history_for_ranking(applications):
    """Outcome signal for calibration: which roles got traction, without personal notes."""
    return [{"title": a["job"].get("title", ""), "company": a["job"].get("company", ""),
             "fit_score": a["job"].get("overall"), "furthest_stage": furthest(a)}
            for a in applications.values() if reached(a, SENT)][:40]


def furthest(application):
    order = ("applied", "rejected", "no_response", "interview", "offer", "offer_declined", "hired")
    seen = {e.get("status") for e in application["events"]} | {application["status"]}
    return next((s for s in reversed(order) if s in seen), application["status"])


def safe_cell(value):
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def csv_text(applications, now=None):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["company", "role", "location", "status", "date_created", "last_activity", "days_quiet",
                     "channel", "contact", "fit_score", "followups", "listing", "notes"])
    for a in sorted(applications.values(), key=lambda a: a["created_at"]):
        notes = "; ".join(f'{e["date"]} {e["note"]}' for e in a["events"] if e.get("note"))
        writer.writerow([safe_cell(v) for v in (
            a["job"].get("company"), a["job"].get("title"), a["job"].get("location"), a["status"], a["created_at"],
            last_activity(a).isoformat(), quiet_days(a, now), a.get("channel"), a.get("contact"),
            a["job"].get("overall", ""), len(a.get("followups", [])), a["job"].get("url"), notes)])
    return output.getvalue()


def bar_chart(rows, color):
    top = max([v for _, v in rows] + [1])
    height = 28 * len(rows) + 8
    parts = [f'<svg viewBox="0 0 420 {height}" width="100%" role="img">']
    for i, (label, value) in enumerate(rows):
        y = 8 + i * 28
        width = 250 * value / top
        parts.append(f'<text x="0" y="{y + 14}" font-size="12" fill="#26333d">{html.escape(label)}</text>'
                     f'<rect x="130" y="{y + 2}" width="{width:.1f}" height="16" rx="3" fill="{color}"/>'
                     f'<text x="{136 + width:.1f}" y="{y + 14}" font-size="12" fill="#61717d">{value}</text>')
    return "".join(parts) + "</svg>"


def report_html(applications, title, now=None):
    now = now or today()
    s = stats(applications, now)
    e = html.escape
    cards = "".join(f'<div class="card"><b>{v}</b><span>{e(k)}</span></div>' for k, v in (
        ("Tracked", s["tracked"]), ("Applied", s["applied"]), ("Interviews", s["interviews"]),
        ("Offers", s["offers"]), ("Response rate", f'{s["response_rate"]}%'), ("Follow-ups due", s["followups_due"])))
    funnel = bar_chart([("Applied", s["applied"]), ("Interviewed", s["interviews"]), ("Offers", s["offers"])], "#294e3e")
    status_rows = [(LABELS[k], v) for k, v in s["counts"].items() if v]
    status_chart = bar_chart(status_rows or [("None yet", 0)], "#8fae6f")
    rows = []
    for a in sorted(applications.values(), key=lambda a: last_activity(a), reverse=True):
        job = a["job"]
        link = f'<a href="{e(job["url"])}">{e(job.get("title", ""))}</a>' if job.get("url", "").startswith(("https://", "http://")) else e(job.get("title", ""))
        due = " · follow-up due" if followup_due(a, now) else ""
        rows.append(f'<tr data-status="{e(a["status"])}"><td>{e(job.get("company", ""))}</td><td>{link}</td>'
                    f'<td>{e(LABELS[a["status"]])}</td><td>{e(a["created_at"])}</td>'
                    f'<td>{quiet_days(a, now)} days{due}</td><td>{e(str(job.get("overall", "")))}</td></tr>')
    options = "".join(f'<option value="{k}">{e(v)}</option>' for k, v in LABELS.items())
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Application pipeline</title><style>
:root{{--ink:#20392f;--muted:#6c7870;--line:#e1e6de;--bg:#f5f5ef;--paper:#fffefa}}
body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}}
main{{max-width:1040px;margin:auto;padding:32px 16px}}h1{{font:400 32px Georgia,serif;margin:0 0 4px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin:24px 0}}
.card{{background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:14px}}.card b{{display:block;font-size:24px}}.card span{{color:var(--muted);font-size:12px}}
.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}}section{{background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:16px;margin-bottom:12px}}
h2{{font-size:14px;margin:0 0 8px}}table{{width:100%;border-collapse:collapse;font-size:13px}}td,th{{text-align:left;padding:8px;border-bottom:1px solid var(--line)}}
.table{{overflow-x:auto}}select{{font:inherit;padding:6px;margin-bottom:10px}}small{{color:var(--muted)}}
</style></head><body><main><h1>Application pipeline</h1><small>{e(title)} · generated {now.isoformat()} · offline report</small>
<div class="cards">{cards}</div><div class="charts"><section><h2>Funnel</h2>{funnel}</section><section><h2>By status</h2>{status_chart}</section></div>
<section><h2>Applications</h2><select id="filter" aria-label="Filter by status"><option value="">All statuses</option>{options}</select>
<div class="table"><table><thead><tr><th>Company</th><th>Role</th><th>Status</th><th>Created</th><th>Last activity</th><th>Fit</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan="6">No tracked applications yet.</td></tr>'}</tbody></table></div></section></main>
<script>document.getElementById('filter').addEventListener('change',function(){{var v=this.value;document.querySelectorAll('tbody tr[data-status]').forEach(function(r){{r.hidden=v&&r.dataset.status!==v;}});}});</script>
</body></html>"""

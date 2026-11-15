"""Pyxida web server. Run with `python -m pyxida`, then open http://127.0.0.1:8765."""
import argparse
import json
import logging
import os
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .access import Access, env_flag, login_page
from .codex_auth import DeviceLogin, logout as codex_logout

from . import tracker
from .ai import CAREER_FIELDS, DOC_TYPES, Agent, WorkflowError, canonical_url, codex_status, evidence_text
from .checks import quality_checks
from .documents import application_bundle, extract_cv, extract_supporting, validate_cv
from .formatting import DOCUMENT_CSS, cv_identity, html_document, pdf_document, word_document
from .document_fonts import FONT_DIR, FONT_FILES
from .guard import inspect_posting
from .photos import document_photo, document_settings, normalize_photo, photo_bytes
from .regions import DRAFT_LANGUAGES, REGIONS, get_region, preset_portals, summary as region_summary
from .storage import Store

ROOT = Path(__file__).resolve().parent  # the package: static files
BASE = ROOT.parent  # the project: .env and the default .data directory
DEFAULT_FOCUS = "AML, KYC, financial crime, finance and banking; include relevant adjacent roles"
LANGUAGES = DRAFT_LANGUAGES
MAX_POSTING = 30000
MAX_DOCUMENTS = 12
ENV_KEYS = ("CODEX_BIN", "CODEX_MODEL", "CODEX_SANDBOX", "CODEX_HOME", "APP_PASSWORD", "ALLOWED_HOSTS",
            "HOST", "PORT", "DATA_DIR", "ALLOW_UNAUTHENTICATED")
PROFILE_LINK = re.compile(r"(?:https?://)?(?:www\.)?(?:github\.com|gitlab\.com|kaggle\.com|orcid\.org|"
                          r"scholar\.google\.[a-z.]+|huggingface\.co|behance\.net|dribbble\.com)/[^\s,;)<>\]]+|https?://[^\s,;)<>\]]+", re.I)


def load_env():
    path = BASE / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                if key.strip() in ENV_KEYS:
                    os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def preference_values(data, region="CH"):
    fields = ("cities", "work_style", "salary", "constraints", "focus", "permit", "depth")
    values = {}
    for key in fields:
        value = data.get(key, DEFAULT_FOCUS if key == "focus" else "")
        if not isinstance(value, str) or len(value) > 3000:
            raise WorkflowError(f"Please keep {key.replace('_', ' ')} under 3,000 characters.")
        values[key] = value.strip()
    if values["depth"] not in ("", "standard", "deep"):
        raise WorkflowError("Choose a standard or deep search.")
    values["depth"] = values["depth"] or "standard"
    values["country"] = get_region(region)["name"]
    values["search_languages"] = get_region(region)["languages"]
    return values


def profile_links(text):
    """Public profile links for enrichment. LinkedIn is login-walled and excluded."""
    links = []
    for match in PROFILE_LINK.findall(text):
        url = canonical_url(match if match.lower().startswith("http") else "https://" + match)
        host = urlsplit(url).hostname or ""
        if url and "linkedin." not in host and "@" not in match:
            links.append(url)
    return list(dict.fromkeys(links))


DOMAIN = re.compile(r"^(?=.{4,200}$)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")
MAX_PORTALS = 20


def portal_domain(value):
    if not isinstance(value, str):
        raise WorkflowError("Enter the portal's website, for example jobs.example.ch.")
    value = value.strip().lower()
    host = urlsplit(value if "://" in value else "https://" + value).hostname or ""
    host = host.removeprefix("www.")
    if not DOMAIN.match(host):
        raise WorkflowError("Enter the portal's website, for example jobs.example.ch.")
    return host


def on_domain(url, domain):
    host = (urlsplit(url).hostname or "").removeprefix("www.")
    return host == domain or host.endswith("." + domain)


def portal_coverage(portals, shortlist):
    """Per-portal health: how many research sources and shortlisted jobs came from each."""
    return [{"name": p["name"], "domain": p["domain"],
             "sources": sum(on_domain(s["url"], p["domain"]) for s in shortlist.get("sources", [])),
             "jobs": sum(on_domain(j["url"], p["domain"]) for j in shortlist.get("jobs", []) if j.get("url"))}
            for p in portals if p["enabled"]]


def choice(data, key, options, default=None):
    value = data.get(key, default)
    if value not in options:
        raise WorkflowError(f"Choose a valid {key.replace('_', ' ')}.")
    return value


class Application:
    def __init__(self, store, agent=None):
        self.store = store
        self.agent = agent or Agent()
        self.locks = {}
        self.lock_guard = threading.Lock()

    def state(self, state):
        # Raw CV is kept local in the database; the UI displays it only on intake.
        result = {k: v for k, v in state.items() if k not in ("cv", "photo")}
        result["photo"] = {k: v for k, v in state["photo"].items() if k != "data"} if state.get("photo") else None
        now = tracker.today()
        result["pipeline"] = {"stats": tracker.stats(state["applications"], now),
                              "timing": {k: {"quiet": tracker.quiet_days(a, now), "due": tracker.followup_due(a, now),
                                             "followups": sum(1 for f in a["followups"] if f["kind"] == "followup")}
                                         for k, a in state["applications"].items()},
                              "statuses": tracker.LABELS, "stages": tracker.STAGES, "channels": tracker.CHANNELS}
        result["profile_links"] = profile_links(self.evidence(state))
        result["documents"] = [{k: v for k, v in d.items() if k != "text"} for d in state["documents"]]
        result["labels"] = {"doc_types": DOC_TYPES, "career_fields": CAREER_FIELDS,
                            "calibration_minimum": tracker.CALIBRATION_MINIMUM, "regions": region_summary(),
                            "draft_languages": DRAFT_LANGUAGES}
        result["portals"] = self.region_portals(state)
        return result

    @staticmethod
    def evidence(state):
        return evidence_text(state["cv"], state["discussion"], state["documents"], state["career"])

    @staticmethod
    def candidate(state):
        """Candidate-authored context every evidence-backed Codex task receives."""
        return {"documents": state["documents"], "career": state["career"]}

    @staticmethod
    def context(state):
        return {"career": state["career"], "calibration": state["calibration"]}

    @staticmethod
    def find_job(state, job_id):
        shortlist = state["shortlist"] or {}
        for job in shortlist.get("jobs", []) + shortlist.get("vetoed", []):
            if job["id"] == job_id:
                return job
        for source in (state["drafts"].get(job_id), state["applications"].get(job_id), state["history"].get(job_id)):
            if source:
                return source["job"]
        raise WorkflowError("This job is no longer available in this profile.")

    @staticmethod
    def remember(state, jobs, status=None):
        today = tracker.today().isoformat()
        for job in jobs:
            entry = state["history"].setdefault(job["id"], {"first_seen": today, "status": "seen"})
            entry.update(job=tracker.job_summary(job), last_seen=today)
            if status:
                entry["status"] = status

    def research(self, state, job):
        """Company research with a 30-day shared cache. Failure never blocks drafting."""
        company = job.get("company", "").strip()
        if not company or company.lower() == "not stated":
            return {"text": "Company research unavailable: employer not stated.", "sources": []}
        cached = self.store.get_research(company)
        if cached:
            return cached
        try:
            return self.store.save_research(company, self.agent.company_research(
                company, job.get("location", ""), country=get_region(state["region"])["name"]))
        except WorkflowError as e:
            if e.code in ("codex_usage", "codex_auth"):
                raise
            return {"text": f"Company research unavailable: {e}", "sources": []}

    def checks(self, state, draft):
        return quality_checks(draft["materials"], draft["job"].get("keywords", []),
                              cv_photo=document_photo(state, "cv"), letter_photo=document_photo(state, "cover_letter"))

    def rediscuss(self, state, message):
        history = state["discussion"] + [{"role": "user", "text": message}]
        profile = self.agent.profile(state["cv"], history, state["preferences"]["focus"], **self.candidate(state))
        history.append({"role": "assistant", "text": profile["reply"]})
        state.update(profile=profile, discussion=history, revision=state["revision"] + 1)

    def application(self, state, job_id):
        application = state["applications"].get(job_id)
        if not application:
            raise WorkflowError("Track this application in your pipeline first.")
        return application

    def post(self, route, data):
        if route == "/api/intake":
            filename = data.get("filename", "Pasted CV")
            if not isinstance(filename, str) or len(filename) > 255:
                raise WorkflowError("Please use a shorter filename.")
            cv = (extract_cv(filename, data["file"]) if data.get("file")
                  else validate_cv(data.get("text", "")))
            prefs = preference_values(data.get("preferences", {}))  # new profiles start in the default region
            profile = self.agent.profile(cv, [], prefs["focus"])
            return self.state(self.store.create(cv, filename, profile, prefs))
        profile_id = data.get("id")
        if not isinstance(profile_id, str) or len(profile_id) != 32:
            raise WorkflowError("Choose a profile first.")
        with self.lock_guard:
            lock = self.locks.setdefault(profile_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise WorkflowError("Another operation is running for this profile. Wait for it to finish.")
        try:
            state = self.store.get(profile_id)
            if route == "/api/delete":
                self.store.delete(profile_id)
                return {"deleted": True}
            handler = self.routes.get(route)
            if not handler:
                raise WorkflowError("Unknown action.")
            handler(self, state, data)
            self.store.save(state)
            return self.state(state)
        finally:
            lock.release()

    # Profile -----------------------------------------------------------------
    def discuss(self, state, data):
        message = data.get("message", "")
        if not isinstance(message, str) or not message.strip() or len(message) > 5000:
            raise WorkflowError("Write a message with up to 5,000 characters.")
        self.rediscuss(state, message.strip())

    def expand(self, state, data):
        extra = data.get("urls", [])
        if not isinstance(extra, list) or len(extra) > 8 or any(not isinstance(u, str) or len(u) > 500 for u in extra):
            raise WorkflowError("Add up to 8 profile links.")
        urls = profile_links(self.evidence(state) + "\n" + "\n".join(extra))[:8]
        if not urls:
            raise WorkflowError("No public profile links were found. Add a GitHub, portfolio or publication link.")
        result = self.agent.expand(state["profile"], urls)
        state["expand"] = {**result, "urls": urls, "created": tracker.today().isoformat()}

    def expand_confirm(self, state, data):
        findings = (state["expand"] or {}).get("findings", [])
        indices = data.get("indices", [])
        if (not isinstance(indices, list) or not indices
                or any(type(i) is not int or not 0 <= i < len(findings) for i in indices)):
            raise WorkflowError("Choose at least one competency to add.")
        lines = [f'- {findings[i]["competency"]}: {findings[i]["evidence"]} ({findings[i]["url"]})' for i in sorted(set(indices))]
        message = "I confirm these additional competencies from my public profiles are accurate:\n" + "\n".join(lines)
        self.rediscuss(state, message[:8000])
        state["expand"] = None

    # Jobs --------------------------------------------------------------------
    def search(self, state, data):
        prefs = preference_values(data.get("preferences", state["preferences"]), state["region"])
        exclude = [h["job"].get("url") for h in state["history"].values() if h["status"] == "dismissed"]
        exclude += [a["job"].get("url") for a in state["applications"].values()]
        portals = [{k: p[k] for k in ("name", "domain", "hint", "preferred")} for p in self.region_portals(state) if p["enabled"]]
        shortlist = self.agent.search(state["profile"], prefs, self.evidence(state), exclude=[u for u in exclude if u],
                                      history=tracker.history_for_ranking(state["applications"]), portals=portals,
                                      context=self.context(state), depth=prefs["depth"])
        shortlist.setdefault("vetoed", [])
        shortlist["portal_coverage"] = portal_coverage(self.region_portals(state), shortlist)
        for job in shortlist["jobs"] + shortlist["vetoed"]:
            job.update(revision=state["revision"], new=job["id"] not in state["history"])
        found = {j["id"] for j in shortlist["jobs"]}
        # Postings the candidate evaluated individually survive a new search.
        shortlist["jobs"] += [j for j in (state["shortlist"] or {}).get("jobs", [])
                              if j.get("source") == "manual" and j["id"] not in found]
        self.remember(state, shortlist["jobs"] + shortlist["vetoed"])
        state.update(preferences=prefs, shortlist=shortlist, search_revision=state["revision"])
        valid_ids = {j["id"] for j in shortlist["jobs"]}
        state["selected"] = [j for j in state["selected"] if j in valid_ids]
        # Existing application drafts remain available even if listings disappear.

    def evaluate(self, state, data):
        url, text = data.get("url", ""), data.get("text", "")
        if not isinstance(url, str) or len(url) > 2000 or not isinstance(text, str) or len(text) > MAX_POSTING:
            raise WorkflowError("Paste a listing URL or up to 30,000 characters of job description.")
        integrity = None
        if text.strip():
            integrity = inspect_posting(text)
            text = integrity.pop("text")
            if len(text) < 200:
                raise WorkflowError("Paste the full job description, including requirements.")
        elif not canonical_url(url):
            raise WorkflowError("Paste a valid https listing URL or the job description text.")
        prefs = preference_values(data.get("preferences", state["preferences"]), state["region"])
        job = self.agent.evaluate(state["profile"], prefs, self.evidence(state), url=url.strip(), text=text,
                                  history=tracker.history_for_ranking(state["applications"]), context=self.context(state))
        job.update(integrity=integrity, revision=state["revision"], new=job["id"] not in state["history"])
        shortlist = state["shortlist"] or {"summary": "Postings you evaluated individually.", "jobs": [], "vetoed": [], "sources": []}
        shortlist["jobs"] = [job] + [j for j in shortlist["jobs"] if j["id"] != job["id"]]
        shortlist["vetoed"] = [j for j in shortlist.get("vetoed", []) if j["id"] != job["id"]]
        state.update(shortlist=shortlist, preferences=prefs)
        self.remember(state, [job], "seen")

    def dismiss(self, state, data):
        job_id = data.get("job_id")
        if data.get("restore"):
            entry = state["history"].get(job_id)
            if not entry or entry["status"] != "dismissed":
                raise WorkflowError("This listing is not dismissed.")
            entry["status"] = "seen"
            shortlist = state["shortlist"] or {"summary": "", "jobs": [], "vetoed": [], "sources": []}
            if all(j["id"] != job_id for j in shortlist["jobs"]):
                shortlist["jobs"].append(entry.get("full_job") or entry["job"])
            state["shortlist"] = shortlist
            return
        job = next((j for j in (state["shortlist"] or {}).get("jobs", []) if j["id"] == job_id), None)
        if not job:
            raise WorkflowError("Only listings in your shortlist can be dismissed.")
        state["shortlist"]["jobs"] = [j for j in state["shortlist"]["jobs"] if j["id"] != job_id]
        state["selected"] = [j for j in state["selected"] if j != job_id]
        self.remember(state, [job], "dismissed")
        state["history"][job_id]["full_job"] = job

    @staticmethod
    def region_portals(state):
        return [p for p in state["portals"] if p["country"] == state["region"]]

    def region(self, state, data):
        code = choice(data, "region", tuple(REGIONS))
        state["region"] = code
        if not self.region_portals(state):
            state["portals"] += preset_portals(code)
        if state["preferences"].get("permit") not in get_region(code)["permits"]:
            state["preferences"]["permit"] = ""
        state["preferences"].update(country=get_region(code)["name"], search_languages=get_region(code)["languages"])

    def portals(self, state, data):
        action = choice(data, "action", ("add", "toggle", "edit", "remove", "reset"))
        portals = self.region_portals(state)
        if action == "reset":
            # Restore this country's presets; portals the user added are kept.
            custom = [p for p in portals if not p["builtin"]]
            others = [p for p in state["portals"] if p["country"] != state["region"]]
            state["portals"] = others + preset_portals(state["region"]) + custom
            return
        domain = portal_domain(data.get("domain"))
        existing = next((p for p in portals if p["domain"] == domain), None)
        if action == "add":
            name = tracker.text_field(data.get("name"), "portal name", 60) or domain
            if existing:
                raise WorkflowError("This portal is already in your list for this country.")
            if len(portals) >= MAX_PORTALS:
                raise WorkflowError(f"Keep the list to {MAX_PORTALS} portals per country; remove one first.")
            state["portals"].append({"name": name, "domain": domain, "enabled": True, "builtin": False,
                                     "country": state["region"], "preferred": bool(data.get("preferred")),
                                     "hint": tracker.text_field(data.get("hint"), "search hint", 300)})
            return
        if not existing:
            raise WorkflowError("This portal is not in your list.")
        if action == "toggle":
            if type(data.get("enabled")) is not bool:
                raise WorkflowError("Choose whether the portal is enabled.")
            existing["enabled"] = data["enabled"]
        elif action == "edit":
            if type(data.get("preferred", existing["preferred"])) is not bool:
                raise WorkflowError("Choose whether the portal is preferred.")
            existing["preferred"] = data.get("preferred", existing["preferred"])
            existing["hint"] = tracker.text_field(data.get("hint", existing["hint"]), "search hint", 300)
            if not existing["builtin"]:
                existing["name"] = tracker.text_field(data.get("name", existing["name"]), "portal name", 60) or existing["name"]
                if data.get("new_domain"):
                    new_domain = portal_domain(data["new_domain"])
                    if new_domain != domain and any(p["domain"] == new_domain for p in portals):
                        raise WorkflowError("Another portal already uses that website.")
                    existing["domain"] = new_domain
        elif existing["builtin"]:
            raise WorkflowError("Built-in portals can be switched off but not removed.")
        else:
            state["portals"].remove(existing)

    def selection(self, state, data):
        ids = data.get("selected", [])
        valid_ids = {j["id"] for j in (state["shortlist"] or {}).get("jobs", [])}
        if not isinstance(ids, list) or any(not isinstance(j, str) or j not in valid_ids for j in ids):
            raise WorkflowError("Only jobs from this profile's shortlist can be selected.")
        state["selected"] = list(dict.fromkeys(ids))

    # Applications --------------------------------------------------------------
    def materials(self, state, data):
        job_id = data.get("job_id")
        if job_id not in state["selected"]:
            raise WorkflowError("Select this job in the shortlist before preparing an application.")
        job = next((j for j in (state["shortlist"] or {}).get("jobs", []) if j["id"] == job_id), None)
        if not job:
            raise WorkflowError("This listing is no longer in the shortlist. Search again.")
        if job.get("revision", state["search_revision"]) != state["revision"]:
            raise WorkflowError("Your profile changed. Refresh the shortlist or evaluate this posting again before preparing drafts.")
        language = choice(data, "language", LANGUAGES, "English")
        review = data.get("review", True)
        if type(review) is not bool:
            raise WorkflowError("Choose whether to run the independent review.")
        existing = state["drafts"].get(job_id)
        if (existing and existing["revision"] == state["revision"]
                and existing["language"] == language and not data.get("regenerate", False)):
            return
        draft = {"job": job, "language": language, "revision": state["revision"], "reviewed": review}
        draft["materials"] = self.agent.materials(
            state["cv"], state["discussion"], state["profile"], job, language,
            review_context=self.research(state, job) if review else None,
            checker=lambda materials: self.checks(state, {"job": job, "materials": materials}),
            style=state["style"], country=get_region(state["region"])["name"], **self.candidate(state))
        draft["checks"] = self.checks(state, draft)
        state["drafts"][job_id] = draft
        if job_id not in state["applications"]:
            state["applications"][job_id] = tracker.record(None, job, "drafted")
        self.remember(state, [job])

    def recheck(self, state, data):
        draft = state["drafts"].get(data.get("job_id"))
        if not draft:
            raise WorkflowError("Prepare drafts for this job first.")
        draft["checks"] = self.checks(state, draft)

    def form_answers(self, state, data):
        job_id = data.get("job_id")
        draft = state["drafts"].get(job_id)
        if not draft:
            raise WorkflowError("Prepare drafts for this job before answering form questions.")
        fields = data.get("fields")
        if not isinstance(fields, list) or not 1 <= len(fields) <= 15:
            raise WorkflowError("Add between 1 and 15 form questions.")
        clean = []
        for field in fields:
            label = field.get("label") if isinstance(field, dict) else None
            limit = field.get("limit", 0) if isinstance(field, dict) else None
            if not isinstance(label, str) or not label.strip() or len(label) > 500 or type(limit) is not int or not 0 <= limit <= 10000:
                raise WorkflowError("Each question needs text (up to 500 characters) and a limit between 0 and 10,000.")
            clean.append({"label": label.strip(), "limit": limit})
        language = choice(data, "language", LANGUAGES, draft["language"])
        draft["form_answers"] = self.agent.form_answers(state["cv"], state["discussion"], state["profile"],
                                                        draft["job"], clean, language, **self.candidate(state))

    def document_settings(self, state, data):
        settings = document_settings(data.get("settings", state["document_settings"]))
        if type(data.get("remove_photo", False)) is not bool:
            raise WorkflowError("Choose whether to remove the picture.")
        if data.get("remove_photo") and data.get("photo"):
            raise WorkflowError("Choose either a new picture or removal, not both.")
        photo = state.get("photo")
        if data.get("remove_photo"):
            photo = None
        elif data.get("photo"):
            photo = normalize_photo(data["photo"])
        state.update(document_settings=settings, photo=photo)

    # Pipeline ----------------------------------------------------------------
    def track(self, state, data):
        job_id = data.get("job_id")
        job = self.find_job(state, job_id)
        note = tracker.text_field(data.get("note"), "note", 2000)
        contact = data.get("contact")
        contact = tracker.text_field(contact, "contact", 200) if contact is not None else None
        application = tracker.record(state["applications"].get(job_id), job, data.get("status"), data.get("date"),
                                     note, data.get("channel"), contact, materials=state["drafts"].get(job_id))
        state["applications"][job_id] = application
        if application["status"] in tracker.SENT:
            self.remember(state, [job], "applied")

    def note(self, state, data):
        note = tracker.text_field(data.get("note"), "note", 2000)
        if not note:
            raise WorkflowError("Write a note first.")
        tracker.add_note(self.application(state, data.get("job_id")), note, data.get("date"))

    def untrack(self, state, data):
        self.application(state, data.get("job_id"))
        del state["applications"][data["job_id"]]

    def followup(self, state, data):
        application = self.application(state, data.get("job_id"))
        kind = choice(data, "kind", ("followup", "thank_you"))
        if kind == "followup":
            if application["status"] in tracker.FINAL or application["status"] == "drafted":
                raise WorkflowError("Follow-ups are for sent applications that are still open.")
            if sum(1 for f in application["followups"] if f["kind"] == "followup") >= tracker.MAX_FOLLOWUPS:
                raise WorkflowError("You have already followed up twice. Consider recording no response instead.")
        elif not tracker.reached(application, tracker.REACHED_INTERVIEW):
            raise WorkflowError("Record an interview stage before drafting a thank-you note.")
        language = choice(data, "language", LANGUAGES, (application["submitted"] or {}).get("language", "English"))
        details = tracker.text_field(data.get("details"), "interview details", 3000)
        draft = self.agent.follow_up(kind, application["job"], application, language, details, style=state["style"])
        application["draft_followup"] = {**draft, "kind": kind, "language": language,
                                         "created": tracker.today().isoformat()}

    def followup_sent(self, state, data):
        application = self.application(state, data.get("job_id"))
        draft = application.get("draft_followup")
        if not draft:
            raise WorkflowError("There is no follow-up draft to log.")
        if data.get("discard"):
            application["draft_followup"] = None
            return
        day = tracker.parse_day(data.get("date")).isoformat()
        application["followups"].append({**draft, "date": day})
        label = "Follow-up sent" if draft["kind"] == "followup" else "Thank-you note sent"
        application["events"].append({"date": day, "kind": draft["kind"], "status": application["status"], "note": label})
        application["draft_followup"] = None

    def interview_prep(self, state, data):
        application = self.application(state, data.get("job_id"))
        stage = choice(data, "stage", tracker.STAGES)
        interviewers = tracker.text_field(data.get("interviewers"), "interviewer names", 1000)
        language = choice(data, "language", LANGUAGES, (application["submitted"] or {}).get("language", "English"))
        research = self.research(state, application["job"])
        notes = None
        if interviewers:
            try:
                notes = self.agent.interviewer_research(application["job"].get("company", ""), interviewers)
            except WorkflowError as e:
                if e.code in ("codex_usage", "codex_auth"):
                    raise
                notes = {"text": f"Interviewer research unavailable: {e}", "sources": []}
        prep = self.agent.interview_prep(state["cv"], state["discussion"], application["job"], application,
                                         stage, research, notes, language, **self.candidate(state))
        application["interview"] = {"stage": stage, "interviewers": interviewers, "language": language,
                                    "prep": prep, "mock": [], "finished": False, "debrief": "",
                                    "created": tracker.today().isoformat()}

    def mock(self, state, data):
        application = self.application(state, data.get("job_id"))
        interview = application.get("interview")
        if not interview:
            raise WorkflowError("Build an interview prep pack first.")
        message = tracker.text_field(data.get("message", ""), "answer", 4000)
        if data.get("restart"):
            interview.update(mock=[], finished=False, debrief="")
            message = ""
        elif interview["finished"]:
            raise WorkflowError("This mock interview has finished. Restart it to practise again.")
        elif interview["mock"] and not message:
            raise WorkflowError("Write your answer first.")
        transcript = interview["mock"]
        if message:
            transcript.append({"role": "candidate", "text": message, "feedback": ""})
        result = self.agent.mock_interview(application["job"], interview["stage"], interview["prep"],
                                           transcript, message, interview["language"])
        if result["finished"]:
            interview.update(finished=True, debrief=result["feedback"])
        elif message:
            transcript[-1]["feedback"] = result["feedback"]
        transcript.append({"role": "interviewer", "text": result["reply"]})

    def upskill(self, state, data):
        jobs = {}
        for job in (state["shortlist"] or {}).get("jobs", []) + [a["job"] for a in state["applications"].values()]:
            jobs[job["id"]] = {k: job.get(k) for k in ("title", "company", "requirements", "gaps", "keywords", "overall")}
        if len(jobs) < 2:
            raise WorkflowError("Find or evaluate at least two jobs before analysing skill gaps.")
        result = self.agent.upskill(state["profile"], list(jobs.values())[:30], state["preferences"])
        previous = {g["skill"].casefold(): g["skill"] for g in (state["upskill"] or {}).get("gaps", [])}
        current = {g["skill"].casefold() for g in result["gaps"]}
        for gap in result["gaps"]:
            gap["new"] = bool(previous) and gap["skill"].casefold() not in previous
        result.update(closed=[v for k, v in previous.items() if k not in current], jobs_analysed=len(jobs),
                      created=tracker.today().isoformat())
        state["upskill"] = result

    # Supporting documents, career profile, style and calibration -----------------
    def documents(self, state, data):
        action = choice(data, "action", ("add", "remove"))
        if action == "remove":
            doc_id = data.get("doc_id")
            remaining = [d for d in state["documents"] if d["id"] != doc_id]
            if len(remaining) == len(state["documents"]):
                raise WorkflowError("This document is not in your profile.")
            removed = next(d for d in state["documents"] if d["id"] == doc_id)
            state["documents"] = remaining
            if state["reconcile"]:
                label = f'{DOC_TYPES[removed["type"]]}: {removed["filename"]}'
                for key in ("conflicts", "additions"):
                    state["reconcile"][key] = [c for c in state["reconcile"][key] if c["document"] != label]
            self.reprofile(state, f'I removed the supporting document "{removed["filename"]}".')
            return
        kind = choice(data, "type", tuple(DOC_TYPES))
        filename = data.get("filename", "")
        if not isinstance(filename, str) or not filename.strip() or len(filename) > 255 or not data.get("file"):
            raise WorkflowError("Choose a document to upload.")
        if len(state["documents"]) >= MAX_DOCUMENTS:
            raise WorkflowError(f"Keep up to {MAX_DOCUMENTS} supporting documents; remove one first.")
        text = extract_supporting(filename, data["file"])
        if any(d["text"] == text for d in state["documents"]):
            raise WorkflowError("This document is already in your profile.")
        state["documents"].append({"id": os.urandom(8).hex(), "type": kind, "filename": filename.strip(), "text": text,
                                   "added": tracker.today().isoformat(), "characters": len(text)})
        state["reconcile"] = {**self.agent.reconcile(state["cv"], state["documents"], state["discussion"]),
                              "created": tracker.today().isoformat(), "resolved": []}
        self.reprofile(state, f'I added a supporting document: {DOC_TYPES[kind]} "{filename.strip()}".')

    def reprofile(self, state, note):
        """Evidence changed outside the discussion: refresh roles and invalidate stale matches."""
        profile = self.agent.profile(state["cv"], state["discussion"], state["preferences"]["focus"], **self.candidate(state))
        state["discussion"] = state["discussion"] + [{"role": "assistant", "text": f"{note}\n{profile['reply']}"}]
        state.update(profile=profile, revision=state["revision"] + 1)

    def resolve_conflict(self, state, data):
        conflicts = (state["reconcile"] or {}).get("conflicts", [])
        index = data.get("index")
        if type(index) is not int or not 0 <= index < len(conflicts) or index in state["reconcile"]["resolved"]:
            raise WorkflowError("Choose an open conflict to resolve.")
        conflict = conflicts[index]
        side = choice(data, "correct", ("cv", "document", "custom"))
        value = {"cv": conflict["cv_says"], "document": conflict["document_says"]}.get(side)
        if side == "custom":
            value = tracker.text_field(data.get("value"), "correction", 1000)
            if not value:
                raise WorkflowError("Write the correct information.")
        state["reconcile"]["resolved"].append(index)
        self.rediscuss(state, f'Correction about {conflict["topic"]}: the accurate information is "{value}".')

    def career(self, state, data):
        values = data.get("career")
        if not isinstance(values, dict) or set(values) - set(CAREER_FIELDS):
            raise WorkflowError("Fill in the career profile fields.")
        state["career"] = {key: tracker.text_field(values.get(key, ""), CAREER_FIELDS[key].lower(), 2000)
                           for key in CAREER_FIELDS}

    def style(self, state, data):
        if choice(data, "action", ("learn", "clear")) == "clear":
            state["style"] = None
            return
        letters = [d for d in state["documents"] if d["type"] == "application"]
        if not letters:
            raise WorkflowError("Add at least one past application or cover letter as a supporting document first.")
        state["style"] = {**self.agent.learn_style(letters), "sources": [d["filename"] for d in letters],
                          "created": tracker.today().isoformat()}

    def calibrate(self, state, data):
        if data.get("clear"):
            state["calibration"] = None
            return
        outcomes = []
        for application in state["applications"].values():
            stage = tracker.furthest(application)
            if stage in tracker.SENT and (tracker.reached(application, tracker.REACHED_INTERVIEW) or application["status"] in tracker.FINAL):
                job = application["job"]
                outcomes.append({"title": job.get("title"), "company": job.get("company"), "fit_score": job.get("overall"),
                                 "keywords": job.get("keywords", []), "requirements": job.get("requirements", []),
                                 "gaps": job.get("gaps", []), "furthest_stage": stage})
        if len(outcomes) < tracker.CALIBRATION_MINIMUM:
            raise WorkflowError(f"Calibration needs at least {tracker.CALIBRATION_MINIMUM} resolved applications "
                                f"(reached an interview, or closed). You have {len(outcomes)}.")
        state["calibration"] = {**self.agent.calibrate(outcomes), "created": tracker.today().isoformat()}

    routes = {"/api/discuss": discuss, "/api/documents": documents, "/api/resolve-conflict": resolve_conflict,
              "/api/career": career, "/api/style": style, "/api/calibrate": calibrate, "/api/expand": expand, "/api/expand-confirm": expand_confirm,
              "/api/search": search, "/api/evaluate": evaluate, "/api/dismiss": dismiss, "/api/portals": portals, "/api/region": region,
              "/api/selection": selection, "/api/materials": materials, "/api/checks": recheck,
              "/api/form-answers": form_answers, "/api/document-settings": document_settings,
              "/api/track": track, "/api/note": note, "/api/untrack": untrack, "/api/followup": followup,
              "/api/followup-sent": followup_sent, "/api/interview-prep": interview_prep, "/api/mock": mock,
              "/api/upskill": upskill}


LOOPBACK = ("127.0.0.1", "localhost", "::1")
PUBLIC_PATHS = {"/login", "/styles.css", "/theme.js", "/healthz", "/favicon.svg"}
STATIC = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
          "/photo-crop.js": ("photo-crop.js", "text/javascript"), "/theme.js": ("theme.js", "text/javascript"),
          "/styles.css": ("styles.css", "text/css"),
          "/favicon.svg": ("favicon.svg", "image/svg+xml")}


class Tasks:
    """Long Codex operations run in the background so reverse proxies never time out a request.
    Quick operations still answer directly; slow ones return a task id the browser polls."""

    def __init__(self, wait=1.5):
        self.wait = wait
        self.items = {}
        self.lock = threading.Lock()

    def run(self, profile_id, work):
        task = {"id": secrets.token_hex(12), "profile": profile_id if isinstance(profile_id, str) else "",
                "status": "running", "started": time.time()}

        def worker():
            try:
                task.update(result=work(), status="done")
            except WorkflowError as e:
                logging.warning("Workflow action failed (%s)", e.code)
                task.update(status="error", http=400, error=str(e), code=e.code, retryable=e.retryable)
            except Exception:
                logging.exception("Workflow request failed")
                task.update(status="error", http=500, error="The operation failed. Your saved work is still available.")
            task["finished"] = time.time()

        with self.lock:
            now = time.time()
            self.items = {k: t for k, t in self.items.items() if now - t.get("finished", now) < 900}
            self.items[task["id"]] = task
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(self.wait)
        return task

    def get(self, task_id):
        with self.lock:
            return self.items.get(task_id)

    def running(self, profile_id):
        with self.lock:
            return next((t["id"] for t in self.items.values() if t["profile"] == profile_id and t["status"] == "running"), None)


def task_view(task):
    view = {"status": task["status"], "elapsed": round(time.time() - task["started"])}
    if task["status"] == "done":
        view["result"] = task["result"]
    elif task["status"] == "error":
        view.update({k: task[k] for k in ("error", "code", "retryable") if k in task})
    return view


def make_server(application, port=8765, host="127.0.0.1", access=None, allowed_hosts=(), login=None, tasks=None):
    access = access or Access("", None)
    login = login or DeviceLogin()
    tasks = tasks or Tasks()
    allowed_hosts = {h.lower() for h in allowed_hosts}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def host_allowed(self, allow_null_origin=False):
            host = self.headers.get("Host", "").lower()
            if allowed_hosts:
                ok = host in allowed_hosts or host.rsplit(":", 1)[0] in allowed_hosts
            elif host_is_loopback:
                ok = host in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            else:
                ok = bool(host)
            if not ok:
                self.send_json({"error": "This address is not allowed. Add it to ALLOWED_HOSTS."}, 403)
                return False
            origin = self.headers.get("Origin")
            forwarded = self.headers.get("X-Forwarded-Host", "").lower()
            # With Referrer-Policy no-referrer, browsers send "Origin: null" on HTML form posts and
            # the navigations they redirect to. Reads and the password form accept it; JSON writes do not.
            if origin and not (allow_null_origin and origin == "null") and urlsplit(origin).netloc.lower() not in {host, forwarded} - {""}:
                self.send_json({"error": "Only requests from this app are allowed."}, 403)
                return False
            return True

        def signed_in(self, path):
            if path in PUBLIC_PATHS or access.valid(self.headers.get("Cookie")):
                return True
            if path.startswith("/api/"):
                self.send_json({"error": "Sign in to continue.", "code": "signed_out"}, 401)
            else:
                self.redirect("/login")
            return False

        def secure(self):
            return self.headers.get("X-Forwarded-Proto", "").lower() == "https"

        def redirect(self, location, cookie=None):
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()

        def send_bytes(self, body, mime, status=200, extra=None, allow_framing=False):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            ancestors = "'self'" if allow_framing else "'none'"
            self.send_header("Content-Security-Policy", f"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors {ancestors}; form-action 'self'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def send_json(self, data, status=200):
            self.send_bytes(json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == "/healthz":
                # Container health checks call 127.0.0.1 directly, whatever ALLOWED_HOSTS says.
                self.send_json({"ok": True})
                return
            if not self.host_allowed(allow_null_origin=True) or not self.signed_in(url.path):
                return
            query = parse_qs(url.query)
            try:
                if url.path in STATIC:
                    name, mime = STATIC[url.path]
                    self.send_bytes((ROOT / "static" / name).read_bytes(), mime + "; charset=utf-8")
                elif url.path == "/login":
                    if not access.enabled:
                        self.redirect("/")
                    else:
                        self.send_bytes(login_page(), "text/html; charset=utf-8")
                elif url.path == "/document.css":
                    self.send_bytes(DOCUMENT_CSS.encode(), "text/css; charset=utf-8")
                elif url.path.startswith("/fonts/") and url.path[len("/fonts/"):] in FONT_FILES:
                    self.send_bytes((FONT_DIR / url.path[len("/fonts/"):]).read_bytes(), "font/ttf")
                elif url.path == "/api/task":
                    task = tasks.get(query.get("id", [""])[0])
                    if not task:
                        self.send_json({"error": "This operation is no longer tracked. Reload the page.", "code": "task_missing"}, 404)
                    else:
                        self.send_json(task_view(task))
                elif url.path == "/api/codex/login":
                    self.send_json(login.snapshot())
                elif url.path == "/api/photo":
                    state = application.store.get(query.get("id", [""])[0])
                    photo = state.get("photo")
                    if not photo:
                        raise WorkflowError("This profile has no saved applicant picture.")
                    self.send_bytes(photo_bytes(photo), "image/jpeg")
                elif url.path == "/api/status":
                    self.send_json({**codex_status(), "model": os.environ.get("CODEX_MODEL", "").strip() or "Codex default",
                                    "profiles": application.store.list(), "auth": access.enabled, "regions": region_summary(),
                                    "codex_login": login.snapshot()["status"]})
                elif url.path == "/api/profile":
                    profile_id = query.get("id", [""])[0]
                    self.send_json({**application.state(application.store.get(profile_id)),
                                    "running_task": tasks.running(profile_id)})
                elif url.path == "/api/export":
                    state = application.store.get(query.get("id", [""])[0])
                    draft = state["drafts"].get(query.get("job", [""])[0])
                    if not draft:
                        raise WorkflowError("Prepare drafts for this job before downloading.")
                    bundle = application_bundle(draft["job"], draft["materials"], photo=state.get("photo"),
                                                settings=state["document_settings"], checks=draft.get("checks"))
                    self.send_bytes(bundle, "application/zip", extra={
                        "Content-Disposition": 'attachment; filename="application-drafts.zip"'})
                elif url.path in ("/api/tracker.csv", "/api/report"):
                    state = application.store.get(query.get("id", [""])[0])
                    if url.path == "/api/tracker.csv":
                        body = ("\ufeff" + tracker.csv_text(state["applications"])).encode()
                        self.send_bytes(body, "text/csv; charset=utf-8", extra={
                            "Content-Disposition": 'attachment; filename="pyxida-applications.csv"'})
                    else:
                        body = tracker.report_html(state["applications"], state["filename"]).encode()
                        self.send_bytes(body, "text/html; charset=utf-8", extra={
                            "Content-Disposition": 'attachment; filename="pyxida-pipeline.html"'})
                elif url.path == "/api/document":
                    state = application.store.get(query.get("id", [""])[0])
                    draft = state["drafts"].get(query.get("job", [""])[0])
                    kind = query.get("kind", ["cv"])[0]
                    file_format = query.get("format", ["html"])[0]
                    if not draft or kind not in ("cv", "cover_letter") or file_format not in ("html", "pdf", "docx"):
                        raise WorkflowError("Choose a saved CV or cover letter to preview or download.")
                    identity = cv_identity(draft["materials"]["cv"])
                    text = draft["materials"][kind]
                    photo = document_photo(state, kind)
                    if file_format == "html":
                        photo_url = f'/api/photo?id={state["id"]}' if photo else None
                        body = html_document(text, kind, identity, stylesheet="/document.css", photo=photo_url).encode()
                        self.send_bytes(body, "text/html; charset=utf-8", allow_framing=True)
                    else:
                        body = pdf_document(text, kind, identity, photo=photo) if file_format == "pdf" else word_document(text, kind, identity, photo=photo)
                        mime = "application/pdf" if file_format == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        filename = "tailored-cv" if kind == "cv" else "cover-letter"
                        self.send_bytes(body, mime, extra={
                            "Content-Disposition": f'attachment; filename="{filename}.{file_format}"'})
                else:
                    self.send_json({"error": "Not found."}, 404)
            except WorkflowError as e:
                self.send_json({"error": str(e), "code": e.code, "retryable": e.retryable}, 400)
            except Exception:
                logging.exception("Request failed")
                self.send_json({"error": "The request failed. Please retry."}, 500)

        def login_post(self):
            length = int(self.headers.get("Content-Length", "0") or 0)
            if not access.enabled:
                self.redirect("/")
                return
            if not 0 < length <= 4096:
                self.send_bytes(login_page("Enter the password."), "text/html; charset=utf-8", 400)
                return
            password = parse_qs(self.rfile.read(length).decode("utf-8", "replace")).get("password", [""])[0]
            client = self.client_address[0]
            if access.locked(client):
                self.send_bytes(login_page("Too many attempts. Wait five minutes and try again."), "text/html; charset=utf-8", 429)
            elif access.attempt(client, password):
                self.redirect("/", access.cookie(access.issue(), self.secure()))
            else:
                self.send_bytes(login_page("That password is not correct."), "text/html; charset=utf-8", 401)

        def do_POST(self):
            path = urlsplit(self.path).path
            if not self.host_allowed(allow_null_origin=path == "/login"):
                return
            if path == "/login":
                self.login_post()
                return
            if not self.signed_in(path):
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                self.send_json({"error": "Expected a JSON request."}, 415)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 12 * 1024 * 1024:
                    self.send_json({"error": "Request too large or empty."}, 413)
                    return
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise WorkflowError("Expected a request object.")
                if path == "/api/logout":
                    self.send_bytes(b'{"signed_out": true}', "application/json; charset=utf-8",
                                    extra={"Set-Cookie": access.cookie("", self.secure(), 0)})
                    return
                if path == "/api/codex/login":
                    self.send_json(login.start())
                    return
                if path == "/api/codex/cancel":
                    self.send_json(login.cancel())
                    return
                if path == "/api/codex/logout":
                    login.cancel()
                    self.send_json(codex_logout())
                    return
                task = tasks.run(data.get("id"), lambda: application.post(path, data))
                if task["status"] == "running":
                    self.send_json({"task": task["id"]}, 202)
                elif task["status"] == "done":
                    self.send_json(task["result"])
                else:
                    self.send_json({k: task[k] for k in ("error", "code", "retryable") if k in task}, task["http"])
            except (json.JSONDecodeError, ValueError, TypeError):
                self.send_json({"error": "The request could not be read."}, 400)
            except WorkflowError as e:
                self.send_json({"error": str(e), "code": e.code, "retryable": e.retryable}, 400)
            except Exception:
                logging.exception("Request failed")
                self.send_json({"error": "The operation failed. Your saved work is still available."}, 500)

    host_is_loopback = host in LOOPBACK
    return ThreadingHTTPServer((host, port), Handler)


def main():
    load_env()
    parser = argparse.ArgumentParser(description="Pyxida: CV-driven job search, Switzerland first")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT") or 8765))
    parser.add_argument("--host", default=os.environ.get("HOST") or "127.0.0.1")
    args = parser.parse_args()
    data_dir = Path(os.environ.get("DATA_DIR") or BASE / ".data")
    store = Store(data_dir)
    access = Access(os.environ.get("APP_PASSWORD", ""), data_dir)
    if args.host not in LOOPBACK and not access.enabled and not env_flag("ALLOW_UNAUTHENTICATED"):
        print("Refusing to listen on a network address without APP_PASSWORD. Set APP_PASSWORD, "
              "or ALLOW_UNAUTHENTICATED=1 if another layer (VPN, authenticating proxy) protects Pyxida.", file=sys.stderr)
        sys.exit(2)
    allowed = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]
    server = make_server(Application(store), args.port, host=args.host, access=access, allowed_hosts=allowed)
    shown = "127.0.0.1" if args.host in LOOPBACK else args.host
    print(f"Pyxida: http://{shown}:{server.server_port}" + (" (password protected)" if access.enabled else ""), flush=True)
    print(codex_status()["message"], flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

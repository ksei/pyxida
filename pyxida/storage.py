"""Local, per-profile persistence. API keys are never stored here."""
import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .ai import WorkflowError
from .regions import DEFAULT_REGION, preset_portals


RESEARCH_TTL_DAYS = 30


def apply_defaults(state):
    """Profiles saved by earlier versions gain the newer keys without migration."""
    state.setdefault("photo", None)
    state.setdefault("document_settings", {"photo_cv": True, "photo_letter": False})
    state.setdefault("history", {})
    state.setdefault("applications", {})
    state.setdefault("upskill", None)
    state.setdefault("expand", None)
    state.setdefault("documents", [])
    state.setdefault("career", {})
    state.setdefault("style", None)
    state.setdefault("calibration", None)
    state.setdefault("reconcile", None)
    state.setdefault("region", DEFAULT_REGION)
    state.setdefault("portals", preset_portals(state["region"]))
    for portal in state["portals"]:
        # Portals saved before regions existed were all Swiss presets or custom Swiss boards.
        portal.setdefault("country", DEFAULT_REGION)
        portal.setdefault("hint", "")
        portal.setdefault("preferred", False)
    if state.get("shortlist"):
        state["shortlist"].setdefault("vetoed", [])
    return state


def company_key(company):
    return "-".join(company.casefold().split())


class Store:
    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / "profiles.sqlite3"
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS profiles (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            # Employer research is not personal data and is shared across profiles.
            connection.execute("CREATE TABLE IF NOT EXISTS company_research (key TEXT PRIMARY KEY, data TEXT NOT NULL, fetched_at TEXT NOT NULL)")
            connection.commit()
        self.path.chmod(0o600)

    def create(self, cv, filename, profile, preferences):
        state = {"id": uuid.uuid4().hex, "cv": cv, "filename": filename,
                 "profile": profile, "preferences": preferences,
                 "discussion": [{"role": "assistant", "text": profile["reply"]}],
                 "shortlist": None, "selected": [], "drafts": {}, "revision": 1,
                 "photo": None, "document_settings": {"photo_cv": True, "photo_letter": False},
                 "search_revision": 0, "updated_at": ""}
        apply_defaults(state)
        self.save(state)
        return state

    def save(self, state):
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("INSERT OR REPLACE INTO profiles VALUES (?, ?)",
                               (state["id"], json.dumps(state, ensure_ascii=False)))
            connection.commit()

    def get(self, profile_id):
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute("SELECT data FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        if not row:
            raise WorkflowError("Profile not found. Upload a CV to start a new profile.")
        return apply_defaults(json.loads(row[0]))

    def list(self):
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute("SELECT data FROM profiles").fetchall()
        states = [json.loads(row[0]) for row in rows]
        return [{"id": s["id"], "filename": s["filename"], "updated_at": s["updated_at"]}
                for s in sorted(states, key=lambda s: s["updated_at"], reverse=True)]

    def delete(self, profile_id):
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
            connection.commit()

    def get_research(self, company, ttl_days=RESEARCH_TTL_DAYS):
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute("SELECT data, fetched_at FROM company_research WHERE key = ?",
                                     (company_key(company),)).fetchone()
        if not row or datetime.now(timezone.utc) - datetime.fromisoformat(row[1]) > timedelta(days=ttl_days):
            return None
        return {**json.loads(row[0]), "fetched_at": row[1]}

    def save_research(self, company, data):
        fetched_at = datetime.now(timezone.utc).isoformat()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("INSERT OR REPLACE INTO company_research VALUES (?, ?, ?)",
                               (company_key(company), json.dumps(data, ensure_ascii=False), fetched_at))
            connection.commit()
        return {**data, "fetched_at": fetched_at}

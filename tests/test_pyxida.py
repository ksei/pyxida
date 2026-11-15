import base64
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from pyxida import tracker
from pyxida.access import Access
from pyxida.ai import Agent, WorkflowError, candidate_sources, evidence_text, merge_reposts, score_job
from pyxida.app import Application, Tasks, make_server
from pyxida.codex_auth import DeviceLogin
from pyxida.documents import extract_supporting
from pyxida.storage import Store
from test_features import FeatureAgent
from test_workflow import CV, DRAFTS, JOB, PROFILE, QUOTE

ROOT = Path(__file__).resolve().parent.parent


def b64(data):
    return base64.b64encode(data).decode()


def linkedin_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("Profile.csv", "First Name,Last Name,Headline\nAlex,Example,KYC Analyst at Example Bank\n")
        z.writestr("Positions.csv", "Company Name,Title,Started On,Finished On\nExample Bank,Senior KYC Analyst,Jan 2021,Mar 2025\n")
        z.writestr("Skills.csv", "Name\nTransaction monitoring\nSanctions screening\n")
        z.writestr("messages.csv", "FROM,CONTENT\nRecruiter,Private message text\n")
    return buffer.getvalue()


RECONCILE = {"summary": "One title differs.", "conflicts": [
    {"topic": "Job title at Example Bank", "cv_says": "KYC Analyst", "document_says": "Senior KYC Analyst",
     "document": "LinkedIn export: export.zip", "severity": "high"}],
    "additions": [{"fact": "Sanctions screening skill", "document": "LinkedIn export: export.zip"}]}
STYLE = {"summary": "Direct and concrete.", "tone": "Warm", "structure": "Three paragraphs", "habits": ["Leads with results"],
         "avoid": ["Long openings"], "openings": ["Role + strongest proof"]}
CALIBRATION = {"summary": "Analyst roles convert.", "worked": ["KYC analyst roles"], "did_not": ["Head roles"],
               "adjustments": ["Score head-of roles lower on experience"], "confidence": "low"}


class PyxidaAgent(FeatureAgent):
    def profile(self, cv, discussion, focus, **kwargs):
        self.calls.append(("profile", len(kwargs.get("documents", ())), kwargs.get("career")))
        return copy.deepcopy(PROFILE)

    def reconcile(self, cv, documents, discussion):
        self.calls.append(("reconcile", [d["filename"] for d in documents]))
        return copy.deepcopy(RECONCILE)

    def learn_style(self, documents):
        self.calls.append(("style", [d["filename"] for d in documents]))
        return copy.deepcopy(STYLE)

    def calibrate(self, outcomes):
        self.calls.append(("calibrate", outcomes))
        return {**copy.deepcopy(CALIBRATION), "sample_size": len(outcomes)}

    def materials(self, cv, discussion, profile, job, language, review_context=None, checker=None, **kwargs):
        self.calls.append(("materials", kwargs))
        return copy.deepcopy(DRAFTS)


class DocumentExtractionTests(unittest.TestCase):
    def test_linkedin_export_keeps_profile_data_and_skips_messages(self):
        text = extract_supporting("export.zip", b64(linkedin_zip()))
        self.assertIn("Senior KYC Analyst", text)
        self.assertIn("Sanctions screening", text)
        self.assertNotIn("Private message text", text)

    def test_unrelated_zip_and_tiny_documents_are_rejected(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as z:
            z.writestr("notes.txt", "hello")
        for name, data in (("other.zip", buffer.getvalue()), ("tiny.txt", b"Too short.")):
            with self.assertRaises(WorkflowError):
                extract_supporting(name, b64(data))

    def test_documents_and_career_become_citable_sources(self):
        documents = [{"type": "reference", "filename": "ref.pdf", "text": "Alex led the remediation of 400 KYC files."}]
        career = {"goals": "Move into a financial crime advisory role."}
        sources = candidate_sources(CV, [], documents, career)
        origins = {s["origin"] for s in sources}
        self.assertIn("Reference letter: ref.pdf", origins)
        self.assertIn("Career preferences", origins)
        self.assertTrue(any(s["id"].startswith("DOC1-") for s in sources))
        self.assertIn("400 KYC files", evidence_text(CV, [], documents, career))


class RichProfileWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.agent = PyxidaAgent()
        self.app = Application(self.store, self.agent)
        self.id = self.app.post("/api/intake", {"text": CV})["id"]

    def tearDown(self):
        self.temp.cleanup()

    def post(self, route, **data):
        return self.app.post(route, {"id": self.id, **data})

    def add_linkedin(self):
        return self.post("/api/documents", action="add", type="linkedin", filename="export.zip", file=b64(linkedin_zip()))

    def test_adding_a_document_reconciles_refreshes_profile_and_hides_text(self):
        revision = self.store.get(self.id)["revision"]
        state = self.add_linkedin()
        self.assertEqual(state["revision"], revision + 1)
        self.assertEqual(state["documents"][0]["filename"], "export.zip")
        self.assertNotIn("text", state["documents"][0])
        self.assertEqual(state["reconcile"]["conflicts"][0]["topic"], "Job title at Example Bank")
        self.assertEqual(self.agent.calls[-1][:2], ("profile", 1))
        self.assertIn("Senior KYC Analyst", self.app.evidence(self.store.get(self.id)))
        with self.assertRaises(WorkflowError):
            self.add_linkedin()

    def test_conflicts_are_resolved_through_the_discussion(self):
        self.add_linkedin()
        state = self.post("/api/resolve-conflict", index=0, correct="document")
        self.assertIn('"Senior KYC Analyst"', state["discussion"][-2]["text"])
        self.assertEqual(state["reconcile"]["resolved"], [0])
        with self.assertRaises(WorkflowError):
            self.post("/api/resolve-conflict", index=0, correct="cv")
        with self.assertRaises(WorkflowError):
            self.post("/api/resolve-conflict", index=5, correct="cv")

    def test_removing_a_document_drops_its_conflicts_and_evidence(self):
        state = self.add_linkedin()
        state = self.post("/api/documents", action="remove", doc_id=state["documents"][0]["id"])
        self.assertEqual(state["documents"], [])
        self.assertEqual(state["reconcile"]["conflicts"], [])
        self.assertNotIn("Senior KYC Analyst", self.app.evidence(self.store.get(self.id)))

    def test_career_profile_is_saved_without_codex_and_reaches_search_and_drafts(self):
        before = len(self.agent.calls)
        revision = self.store.get(self.id)["revision"]
        state = self.post("/api/career", career={"goals": "Lead AML investigations", "energizing": "Complex cases"})
        self.assertEqual(len(self.agent.calls), before)
        self.assertEqual(state["revision"], revision)
        self.assertEqual(state["career"]["goals"], "Lead AML investigations")
        self.assertEqual(state["career"]["draining"], "")
        with self.assertRaises(WorkflowError):
            self.post("/api/career", career={"unknown": "x"})
        self.post("/api/search", preferences={"depth": "deep"})
        search = self.agent.calls[-1][4]
        self.assertEqual(search["context"]["career"]["goals"], "Lead AML investigations")
        self.assertEqual(search["depth"], "deep")
        with self.assertRaises(WorkflowError):
            self.post("/api/search", preferences={"depth": "extreme"})
        self.post("/api/selection", selected=["job1"])
        self.post("/api/materials", job_id="job1", review=False)
        drafted = self.agent.calls[-1][1]
        self.assertEqual(drafted["career"]["energizing"], "Complex cases")
        self.assertIsNone(drafted["style"])

    def test_writing_style_needs_past_applications_and_feeds_drafts(self):
        with self.assertRaises(WorkflowError):
            self.post("/api/style", action="learn")
        letter = b"Dear hiring team,\n\nI am applying for the KYC analyst role because I enjoy complex reviews.\n\nKind regards"
        self.post("/api/documents", action="add", type="application", filename="letter.txt", file=b64(letter))
        state = self.post("/api/style", action="learn")
        self.assertEqual(state["style"]["sources"], ["letter.txt"])
        self.post("/api/search")
        self.post("/api/selection", selected=["job1"])
        self.post("/api/materials", job_id="job1", review=False)
        self.assertEqual(self.agent.calls[-1][1]["style"]["tone"], "Warm")
        self.assertIsNone(self.post("/api/style", action="clear")["style"])

    def test_calibration_needs_resolved_outcomes_and_reaches_scoring(self):
        with self.assertRaises(WorkflowError):
            self.post("/api/calibrate")
        state = self.store.get(self.id)
        for index, final in enumerate(("interview", "rejected", "no_response")):
            job = {**JOB, "id": f"job{index}", "title": f"Role {index}"}
            application = tracker.record(None, job, "applied", "2026-10-01")
            state["applications"][job["id"]] = tracker.record(application, job, final, "2026-10-10")
        state["applications"]["open"] = tracker.record(None, {**JOB, "id": "open"}, "applied", "2026-10-01")
        self.store.save(state)
        state = self.post("/api/calibrate")
        self.assertEqual(state["calibration"]["sample_size"], 3)
        self.assertEqual({o["furthest_stage"] for o in self.agent.calls[-1][1]}, {"interview", "rejected", "no_response"})
        self.post("/api/search")
        self.assertEqual(self.agent.calls[-1][4]["context"]["calibration"]["confidence"], "low")
        self.assertIsNone(self.post("/api/calibrate", clear=True)["calibration"])


class AgentPyxidaTests(unittest.TestCase):
    def draft(self, cv="# Alex\nKYC"):
        sources = candidate_sources(CV, [])
        return {"cv": cv, "cover_letter": "Dear team", "email": "Subject", "notes": "",
                "evidence": [{"claim": "KYC", "source_ids": [sources[0]["id"]]}]}

    def test_overlong_drafts_are_condensed_until_they_fit(self):
        pages = iter([{"cv_pages": 3, "letter_pages": 1}, {"cv_pages": 2, "letter_pages": 1}])
        listing = {"text": "Open", "sources": DRAFTS["sources"]}
        with patch.object(Agent, "call", side_effect=[listing, self.draft(), self.draft("# Alex\nShorter")]) as call:
            result = Agent().materials(CV, [], PROFILE, JOB, "English", checker=lambda m: next(pages))
        self.assertEqual(result["cv"], "# Alex\nShorter")
        self.assertEqual(result["fit_passes"], 1)
        self.assertIn("length_problems", call.call_args.args[1])
        self.assertIn("Relevance-weighted cutting", call.call_args.args[0])

    def test_a_failed_condense_keeps_the_grounded_draft(self):
        broken = {**self.draft("# Alex\nInvented"), "evidence": []}
        listing = {"text": "Open", "sources": DRAFTS["sources"]}
        with patch.object(Agent, "call", side_effect=[listing, self.draft(), broken]):
            result = Agent().materials(CV, [], PROFILE, JOB, "English", checker=lambda m: {"cv_pages": 3, "letter_pages": 1})
        self.assertEqual(result["cv"], "# Alex\nKYC")
        self.assertNotIn("fit_passes", result)

    def test_deep_search_researches_portal_groups_and_tolerates_one_failure(self):
        portals = [{"name": f"P{i}", "domain": f"p{i}.ch"} for i in range(4)]
        base = {k: v for k, v in JOB.items() if k not in ("id", "checked_at", "overall", "vetoes", "fit")}
        research = {"text": "Found", "sources": [{"url": JOB["url"], "title": "KYC"}]}
        responses = [research, WorkflowError("interrupted", "codex_connection"), research,
                     {"summary": "Ranked.", "jobs": [copy.deepcopy(base)]}]
        with patch.object(Agent, "call", side_effect=responses) as call:
            result = Agent().search(PROFILE, {}, CV, portals=portals, depth="deep")
        self.assertEqual(call.call_count, 4)
        self.assertEqual([len(c.args[1]["portals"]) for c in call.call_args_list[:3]], [3, 1, 0])
        self.assertIn("employer career pages", call.call_args_list[2].args[0])
        self.assertIn("P3", result["summary"])
        self.assertEqual(len(result["jobs"]), 1)

    def test_search_prompts_use_the_selected_country_and_preferred_portals_first(self):
        base = {k: v for k, v in JOB.items() if k not in ("id", "checked_at", "overall", "vetoes", "fit")}
        research = {"text": "Found", "sources": [{"url": JOB["url"], "title": "KYC"}]}
        portals = [{"name": "A", "domain": "a.de", "hint": "", "preferred": False},
                   {"name": "B", "domain": "b.de", "hint": "Finance", "preferred": True}]
        prefs = {"country": "Germany", "search_languages": ["German", "English"]}
        with patch.object(Agent, "call", side_effect=[research, {"summary": "Ranked.", "jobs": [copy.deepcopy(base)]}]) as call:
            Agent().search(PROFILE, prefs, CV, portals=portals)
        prompt, data = call.call_args_list[0].args[:2]
        self.assertIn("vacancies in\nGermany", prompt)
        self.assertIn("(German, English)", prompt)
        self.assertNotIn("Switzerland", prompt)
        self.assertEqual([p["domain"] for p in data["portals"]], ["b.de", "a.de"])
        self.assertIn("Include only vacancies\nin Germany", call.call_args_list[1].args[0])

    def test_reposted_roles_merge_into_the_best_scored_listing(self):
        first = score_job({**copy.deepcopy(JOB), "url": "https://a.ch/1", "also_listed": []})
        repost = score_job({**copy.deepcopy(JOB), "url": "https://b.ch/2", "company": "sample bank ", "also_listed": [],
                            "scores": dict.fromkeys(JOB["scores"], 40)})
        merged = merge_reposts([repost, first])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["url"], "https://a.ch/1")
        self.assertEqual(merged[0]["also_listed"], ["https://b.ch/2"])

    def test_reconcile_keeps_document_names_constrained(self):
        documents = [{"type": "linkedin", "filename": "export.zip", "text": "Senior KYC Analyst"}]
        with patch.object(Agent, "call", return_value=copy.deepcopy(RECONCILE)) as call:
            result = Agent().reconcile(CV, documents, [])
        schema = call.call_args.args[2]
        self.assertEqual(schema["properties"]["conflicts"]["items"]["properties"]["document"]["enum"], ["LinkedIn export: export.zip"])
        self.assertEqual(len(result["conflicts"]), 1)


class FakeLoginProcess:
    def __init__(self, lines, code=0):
        self.stdout = iter(lines)
        self.code = code
        self.pid = 4242

    def poll(self):
        return None

    def wait(self):
        return self.code


class DeviceLoginTests(unittest.TestCase):
    LINES = ["Welcome to Codex\n", "1. Open this link\n", "   \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n",
             "2. Enter this one-time code \x1b[90m(expires in 15 minutes)\x1b[0m\n", "   \x1b[94mABCD-EFGH2\x1b[0m\n"]

    def test_link_and_code_are_parsed_from_cli_output(self):
        process = FakeLoginProcess(self.LINES)
        login = DeviceLogin(popen=lambda *a, **k: process, status=lambda: {"configured": True})
        with patch("pyxida.codex_auth.codex_binary", return_value="codex"):
            state = login.start(wait=2)
        self.assertIn(state["status"], ("waiting", "success"))
        for _ in range(50):
            if login.snapshot()["status"] == "success":
                break
            time.sleep(0.02)
        self.assertEqual(login.snapshot()["status"], "success")

    def test_waiting_state_exposes_code_until_completion(self):
        gate = threading.Event()

        def lines():
            yield from self.LINES
            gate.wait(2)

        process = FakeLoginProcess(lines(), code=1)
        login = DeviceLogin(popen=lambda *a, **k: process, status=lambda: {"configured": False})
        with patch("pyxida.codex_auth.codex_binary", return_value="codex"):
            state = login.start(wait=2)
        self.assertEqual((state["status"], state["url"], state["code"]),
                         ("waiting", "https://auth.openai.com/codex/device", "ABCD-EFGH2"))
        gate.set()
        for _ in range(50):
            if login.snapshot()["status"] == "failed":
                break
            time.sleep(0.02)
        self.assertEqual(login.snapshot()["status"], "failed")
        self.assertEqual(login.snapshot()["code"], "")


class SlowAgent(PyxidaAgent):
    def search(self, *args, **kwargs):
        time.sleep(0.4)
        return super().search(*args, **kwargs)


class ServerTests(unittest.TestCase):
    def start(self, password="", allowed=(), agent=None):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.app = Application(self.store, agent or PyxidaAgent())
        self.profile = self.app.post("/api/intake", {"text": CV})["id"]
        login = DeviceLogin(popen=lambda *a, **k: FakeLoginProcess(DeviceLoginTests.LINES), status=lambda: {"configured": True})
        self.server = make_server(self.app, 0, host="127.0.0.1", access=Access(password, self.temp.name),
                                  allowed_hosts=allowed, login=login, tasks=Tasks(wait=0.05))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.temp.cleanup()

    def request(self, path, data=None, opener=None, headers=None, form=None):
        body = json.dumps(data).encode() if data is not None else form
        request = Request(self.base + path, data=body, headers=headers or {})
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with (opener.open(request) if opener else urlopen(request)) as response:
                return response.status, response.read(), response.headers
        except HTTPError as e:
            return e.code, e.read(), e.headers

    def test_password_protects_pages_and_api_until_sign_in(self):
        self.start(password="correct horse")
        status, _, _ = self.request("/api/status")
        self.assertEqual(status, 401)
        status, body, _ = self.request("/login")
        self.assertIn(b"Sign in", body)
        status, body, _ = self.request("/login", form=b"password=wrong")
        self.assertEqual(status, 401)
        self.assertEqual(self.request("/healthz")[0], 200)
        opener = build_opener(HTTPCookieProcessor())
        status, body, _ = self.request("/login", form=b"password=correct+horse", opener=opener, headers={"Origin": "null"})
        self.assertEqual(status, 200)
        self.assertIn(b"pyxida", body.lower())
        status, body, _ = self.request("/api/status", opener=opener)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["auth"])
        self.request("/api/logout", {}, opener=opener)
        self.assertEqual(self.request("/api/status", opener=opener)[0], 401)

    def test_allowed_hosts_are_enforced_except_for_health_checks(self):
        self.start(allowed=("pyxida.home.lan",))
        self.assertEqual(self.request("/api/status")[0], 403)
        self.assertEqual(self.request("/api/status", headers={"Host": "pyxida.home.lan"})[0], 200)
        self.assertEqual(self.request("/api/status", headers={"Host": "pyxida.home.lan:8765"})[0], 200)
        self.assertEqual(self.request("/healthz")[0], 200)
        cross = {"Host": "pyxida.home.lan", "Origin": "https://evil.example"}
        self.assertEqual(self.request("/api/selection", {"id": self.profile, "selected": []}, headers=cross)[0], 403)
        null_origin = {"Host": "pyxida.home.lan", "Origin": "null"}
        self.assertEqual(self.request("/api/selection", {"id": self.profile, "selected": []}, headers=null_origin)[0], 403)

    def test_slow_operations_run_as_background_tasks(self):
        self.start(agent=SlowAgent())
        status, body, _ = self.request("/api/search", {"id": self.profile})
        self.assertEqual(status, 202)
        task = json.loads(body)["task"]
        status, body, _ = self.request(f"/api/profile?id={self.profile}")
        self.assertEqual(json.loads(body)["running_task"], task)
        for _ in range(50):
            view = json.loads(self.request(f"/api/task?id={task}")[1])
            if view["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(view["status"], "done")
        self.assertEqual(view["result"]["shortlist"]["jobs"][0]["id"], "job1")
        status, body, _ = self.request("/api/selection", {"id": self.profile, "selected": ["missing"]})
        self.assertEqual(status, 400)
        self.assertEqual(self.request("/api/task?id=unknown")[0], 404)

    def test_codex_device_login_endpoints(self):
        self.start()
        with patch("pyxida.codex_auth.codex_binary", return_value="codex"):
            status, body, _ = self.request("/api/codex/login", {})
        self.assertEqual(status, 200)
        self.assertIn(json.loads(body)["status"], ("waiting", "success"))
        self.assertIn(json.loads(self.request("/api/codex/login")[1])["status"], ("waiting", "success"))


class StartupTests(unittest.TestCase):
    def test_network_binding_requires_a_password(self):
        with tempfile.TemporaryDirectory() as data:
            env = {**os.environ, "DATA_DIR": data, "APP_PASSWORD": "", "ALLOW_UNAUTHENTICATED": ""}
            result = subprocess.run([sys.executable, "-m", "pyxida", "--host", "0.0.0.0", "--port", "0"],
                                    capture_output=True, text=True, timeout=20, env=env, cwd=ROOT)
        self.assertEqual(result.returncode, 2)
        self.assertIn("APP_PASSWORD", result.stderr)


if __name__ == "__main__":
    unittest.main()

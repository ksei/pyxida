import base64
import copy
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from pyxida.ai import (Agent, WorkflowError, candidate_sources, canonical_url, check_quotes,
                material_schema, obj, resolve_material_evidence)
from pyxida.app import Application
from pyxida.documents import application_bundle, extract_cv, word_document
from pyxida.storage import Store

CV = "Alex Example\nWorked for Example Bank from 2021 to 2025. Reviewed KYC files and investigated transaction monitoring alerts. English fluent. German B2."
QUOTE = "Reviewed KYC files and investigated transaction monitoring alerts."
PROFILE = {"summary": "Banking operations experience with KYC review and alert investigations.",
           "strengths": ["KYC review"], "languages": ["English fluent", "German B2"],
           "roles": [{"title": "KYC analyst", "seniority": "Experienced individual contributor",
                      "reason": "Direct KYC review experience", "evidence": [QUOTE]}],
           "questions": ["Which Swiss locations work for you?"], "reply": "Your KYC experience supports analyst roles."}
JOB = {"title": "KYC analyst", "company": "Sample Bank", "location": "Zurich, Switzerland",
       "url": "https://example.com/jobs/kyc?id=42", "fit": "Strong", "why": "Relevant KYC review experience",
       "evidence": [QUOTE], "gaps": ["Work authorization not established"],
       "requirements": ["KYC review experience"], "salary": "Not stated", "work_style": "Hybrid",
       "language": "English", "deadline": "Not stated", "listing_status": "Open", "in_country": True,
       "keywords": ["KYC", "transaction monitoring"], "deadline_date": "",
       "scores": {"skills": 80, "experience": 80, "culture": 60, "career": 80},
       "location_check": "PASS", "language_gate": "PASS", "language_note": "English required; English fluent.",
       "eligibility": "UNVERIFIED", "eligibility_note": "The listing is silent.", "questions_for_employer": [],
       "id": "job1", "checked_at": "2026-10-09T21:00:00+02:00", "overall": 77, "vetoes": []}
DRAFTS = {"cv": "# Alex Example\nReviewed KYC files.", "cover_letter": "Dear hiring team,\nI reviewed KYC files.",
          "email": "Subject: Application\nPlease find my application attached.", "notes": "Review before using.",
          "evidence": [{"claim": "Reviewed KYC files", "source": QUOTE}],
          "sources": [{"url": JOB["url"], "title": "KYC analyst"}]}


class FakeAgent:
    def profile(self, cv, discussion, focus, **_):
        assert cv and focus is not None
        return copy.deepcopy(PROFILE)

    def search(self, profile, preferences, evidence, exclude=(), history=(), portals=(), **_):
        return {"summary": "One sourced match.", "jobs": [copy.deepcopy(JOB)], "vetoed": [], "sources": DRAFTS["sources"]}

    def materials(self, cv, discussion, profile, job, language, review_context=None, checker=None, **_):
        return copy.deepcopy(DRAFTS)

    def company_research(self, company, location="", **_):
        return {"text": f"{company} research", "sources": [{"url": "https://example.com/about", "title": "About"}]}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.app = Application(self.store, FakeAgent())
        self.state = self.app.post("/api/intake", {"text": CV})

    def tearDown(self):
        self.temp.cleanup()

    def test_cv_only_intake_resume_and_isolation(self):
        self.assertNotIn("cv", self.state)
        self.assertEqual(self.state["profile"]["roles"][0]["title"], "KYC analyst")
        second = self.app.post("/api/intake", {"text": CV + "\nAnother candidate."})
        self.assertNotEqual(second["id"], self.state["id"])
        reopened = Store(self.temp.name).get(self.state["id"])
        self.assertEqual(reopened["cv"], CV)
        self.assertEqual(len(self.store.list()), 2)

    def test_selected_jobs_only_and_profile_changes_invalidate_matches(self):
        state = self.app.post("/api/search", {"id": self.state["id"]})
        with self.assertRaises(WorkflowError):
            self.app.post("/api/materials", {"id": state["id"], "job_id": "job1"})
        self.app.post("/api/selection", {"id": state["id"], "selected": ["job1"]})
        state = self.app.post("/api/materials", {"id": state["id"], "job_id": "job1"})
        self.assertIn("job1", state["drafts"])
        state = self.app.post("/api/discuss", {"id": state["id"], "message": "I prefer Zurich."})
        self.assertIn("job1", state["drafts"])
        self.assertNotEqual(state["revision"], state["search_revision"])
        with self.assertRaises(WorkflowError):
            self.app.post("/api/materials", {"id": state["id"], "job_id": "job1"})

    def test_invalid_selection_and_failed_search_preserve_previous_work(self):
        state = self.app.post("/api/search", {"id": self.state["id"]})
        with self.assertRaises(WorkflowError):
            self.app.post("/api/selection", {"id": state["id"], "selected": ["invented"]})
        with patch.object(self.app.agent, "search", side_effect=WorkflowError("network failure")):
            with self.assertRaises(WorkflowError):
                self.app.post("/api/search", {"id": state["id"]})
        self.assertEqual(self.store.get(state["id"])["shortlist"], state["shortlist"])

    def test_retry_keeps_completed_packages_and_drafts_a_new_language(self):
        state = self.app.post("/api/search", {"id": self.state["id"]})
        self.app.post("/api/selection", {"id": state["id"], "selected": ["job1"]})
        state = self.app.post("/api/materials", {"id": state["id"], "job_id": "job1"})
        saved = copy.deepcopy(state["drafts"]["job1"])
        with patch.object(self.app.agent, "materials", wraps=self.app.agent.materials) as generate:
            self.app.post("/api/materials", {"id": state["id"], "job_id": "job1"})
            generate.assert_not_called()
            self.assertEqual(self.store.get(state["id"])["drafts"]["job1"], saved)
            self.app.post("/api/materials", {"id": state["id"], "job_id": "job1", "language": "German"})
            generate.assert_called_once()

    def test_delete_removes_only_chosen_profile(self):
        second = self.app.post("/api/intake", {"text": CV})
        self.app.post("/api/delete", {"id": self.state["id"]})
        with self.assertRaises(WorkflowError):
            self.store.get(self.state["id"])
        self.assertEqual(self.store.get(second["id"])["id"], second["id"])


class GroundingTests(unittest.TestCase):
    def test_url_identity_and_safe_protocols(self):
        self.assertEqual(canonical_url("https://example.com/jobs/kyc/?utm_source=board&id=42#detail"), JOB["url"])
        self.assertNotEqual(canonical_url("https://example.com/jobs/kyc?id=43"), JOB["url"])
        for url in ("javascript:alert(1)", "file:///tmp/file", "https://user:password@example.com/job"):
            self.assertEqual(canonical_url(url), "")

    def test_evidence_requires_real_candidate_quotes(self):
        self.assertTrue(check_quotes(["Reviewed KYC files\n and investigated transaction monitoring alerts."], CV))
        self.assertFalse(check_quotes(["Led a team of 20 investigators"], CV))
        self.assertFalse(check_quotes([""], CV))

    def test_inferred_roles_without_evidence_are_dropped(self):
        result = copy.deepcopy(PROFILE)
        role = copy.deepcopy(result["roles"][0]); role["evidence"] = ["Managed global compliance"]
        result["roles"].append(role)
        with patch.object(Agent, "call", return_value=result):
            self.assertEqual(len(Agent().profile(CV, [], "" )["roles"]), 1)

    def test_unsourced_closed_foreign_duplicate_or_ungrounded_jobs_dropped(self):
        valid = {k: v for k, v in JOB.items() if k not in ("id", "checked_at")}
        jobs = [valid, copy.deepcopy(valid)]
        for field, value in (("url", "https://invented.example/job"), ("listing_status", "Closed"),
                             ("in_country", False), ("evidence", ["Unheld credential"])):
            job = copy.deepcopy(valid); job[field] = value; jobs.append(job)
        research = {"text": "Listing research", "sources": DRAFTS["sources"]}
        with patch.object(Agent, "call", side_effect=[research, {"summary": "Matches", "jobs": jobs}]):
            shortlist = Agent().search(PROFILE, {}, CV)
        self.assertEqual(len(shortlist["jobs"]), 1)
        self.assertEqual(shortlist["jobs"][0]["url"], JOB["url"])

    def test_drafts_require_candidate_evidence(self):
        drafts = {k: copy.deepcopy(v) for k, v in DRAFTS.items() if k != "sources"}
        drafts["evidence"] = [{"claim": "Unheld credential", "source_ids": ["invented"]}]
        with patch.object(Agent, "call", side_effect=[{"text": "Still open", "sources": DRAFTS["sources"]}, drafts, drafts]):
            with self.assertRaises(WorkflowError):
                Agent().materials(CV, [], PROFILE, JOB, "English")

    def test_typographic_quotes_and_ligatures_do_not_change_facts(self):
        text = 'Reviewed the client’s KYC ﬁles; amount CHF 1,000.'
        self.assertTrue(check_quotes(["client's KYC files"], text))
        self.assertFalse(check_quotes(["CHF 10,000"], text))
        self.assertFalse(check_quotes(["client's AML files"], text))

    def test_catalog_uses_only_candidate_text_and_chunks_long_pdf_blocks(self):
        cv = "▶ Reviewed KYC ﬁles – 2021–2025. " + ("Investigated transaction alerts. " * 40)
        discussion = [{"role": "assistant", "text": "Invented certification"},
                      {"role": "user", "text": "I speak French at B2."}]
        catalog = candidate_sources(cv, discussion)
        self.assertGreater(len(catalog), 2)
        self.assertEqual(len({s["id"] for s in catalog}), len(catalog))
        self.assertTrue(all(s["text"] in cv or s["text"] in discussion[1]["text"] for s in catalog))
        self.assertTrue(all(len(s["text"]) <= 500 for s in catalog))
        self.assertFalse(any("Invented certification" in s["text"] for s in catalog))

    def test_translated_drafts_resolve_ids_to_the_original_candidate_text(self):
        sources = candidate_sources("Contrôle des dossiers KYC et surveillance des transactions.", [])
        drafts = {k: v for k, v in DRAFTS.items() if k not in ("sources", "evidence")}
        drafts["evidence"] = [{"claim": "Reviewed KYC files", "source_ids": [sources[0]["id"]]}]
        result = resolve_material_evidence(drafts, sources)
        self.assertEqual(result["evidence"][0]["source"], sources[0]["text"])
        self.assertNotIn("source", drafts["evidence"][0])

    def test_multiple_source_refs_are_resolved_without_requiring_adjacent_quotes(self):
        sources = candidate_sources(CV, [])
        drafts = {k: v for k, v in DRAFTS.items() if k not in ("sources", "evidence")}
        drafts["evidence"] = [{"claim": "Candidate background", "source_ids": [sources[0]["id"], sources[-1]["id"]]}]
        result = resolve_material_evidence(drafts, sources)
        self.assertEqual(result["evidence"][0]["source"], sources[0]["text"] + "\n\n" + sources[-1]["text"])

    def test_missing_references_are_repaired_without_repeating_listing_research(self):
        sources = candidate_sources(CV, [])
        drafts = {k: copy.deepcopy(v) for k, v in DRAFTS.items() if k not in ("sources", "evidence")}
        broken = {**drafts, "evidence": []}
        repaired = {**drafts, "evidence": [{"claim": "KYC review", "source_ids": [sources[0]["id"]]}]}
        with patch.object(Agent, "call", side_effect=[{"text": "Still open", "sources": DRAFTS["sources"]}, broken, repaired]) as call:
            result = Agent().materials(CV, [], PROFILE, JOB, "English")
        self.assertEqual(call.call_count, 3)
        self.assertTrue(call.call_args_list[0].kwargs["search"])
        self.assertNotIn("search", call.call_args_list[2].kwargs)
        self.assertEqual(result["evidence"][0]["source"], sources[0]["text"])


class DocumentTests(unittest.TestCase):
    def test_word_round_trip_and_bundle_contents(self):
        text = "# Candidate\nEnglish, Français & Deutsch\nReviewed KYC files and banking clients. Investigated transaction monitoring alerts."
        data = word_document(text)
        extracted = extract_cv("CV.docx", base64.b64encode(data).decode())
        self.assertIn("Français & Deutsch", extracted)
        self.assertIn("Reviewed KYC files", extracted)
        with zipfile.ZipFile(io.BytesIO(application_bundle(JOB, DRAFTS))) as z:
            self.assertEqual(set(z.namelist()), {"tailored-cv.md", "tailored-cv.docx", "cover-letter.md",
                                               "cover-letter.docx", "tailored-cv.pdf", "cover-letter.pdf",
                                               "email.txt", "review-notes.md"})
            self.assertIn(QUOTE, z.read("review-notes.md").decode())

    def test_bad_or_empty_uploads_fail_clearly(self):
        for name, value in (("cv.pdf", "!!!"), ("cv.txt", base64.b64encode(b"").decode()),
                            ("cv.exe", base64.b64encode(b"binary").decode())):
            with self.assertRaises(WorkflowError):
                extract_cv(name, value)


class FakeProcess:
    returncode = 0
    pid = 1234
    events = []
    result = {"ready": True}

    def __init__(self, command, **kwargs):
        self.command = command
        self.kwargs = kwargs
        self.output = Path(command[command.index("--output-last-message") + 1])

    def communicate(self, prompt, timeout):
        self.prompt = prompt
        self.output.write_text(json.dumps(self.result))
        return "\n".join(json.dumps(e) for e in self.events), ""


class CodexContractTests(unittest.TestCase):
    def test_recovered_error_event_does_not_reject_a_successful_result(self):
        class RecoveredProcess(FakeProcess):
            events = [{"type": "error", "message": "Stream disconnected; reconnecting"},
                      {"type": "turn.completed"}]
        with patch("pyxida.ai.codex_status", return_value={"configured": True}), patch("pyxida.ai.codex_binary", return_value="codex"), \
             patch("pyxida.ai.subprocess.Popen", side_effect=RecoveredProcess):
            self.assertEqual(Agent().call("Return ready", {}, obj(ready={"type": "boolean"})), {"ready": True})

    def test_transient_failure_is_retried_once(self):
        class InterruptedProcess(FakeProcess):
            returncode = 1
            events = [{"type": "turn.failed", "error": {"message": "Stream disconnected"}}]
        processes = [InterruptedProcess, FakeProcess]
        def spawn(command, **kwargs):
            return processes.pop(0)(command, **kwargs)
        with patch("pyxida.ai.codex_status", return_value={"configured": True}), patch("pyxida.ai.codex_binary", return_value="codex"), \
             patch("pyxida.ai.subprocess.Popen", side_effect=spawn) as start, patch("pyxida.ai.time.sleep"):
            self.assertEqual(Agent().call("Return ready", {}, obj(ready={"type": "boolean"})), {"ready": True})
        self.assertEqual(start.call_count, 2)

    def test_persistent_connection_failure_stops_after_one_retry(self):
        class InterruptedProcess(FakeProcess):
            returncode = 1
            events = [{"type": "turn.failed", "error": {"message": "Stream disconnected"}}]
        with patch("pyxida.ai.codex_status", return_value={"configured": True}), patch("pyxida.ai.codex_binary", return_value="codex"), \
             patch("pyxida.ai.subprocess.Popen", side_effect=InterruptedProcess) as start, patch("pyxida.ai.time.sleep"):
            with self.assertRaises(WorkflowError) as caught:
                Agent().call("Return ready", {}, obj(ready={"type": "boolean"}))
        self.assertEqual(start.call_count, 2)
        self.assertEqual(caught.exception.code, "codex_connection")

    def test_exhausted_usage_is_reported_without_automatic_retry(self):
        class ExhaustedProcess(FakeProcess):
            returncode = 1
            events = [{"type": "turn.failed", "error": {"message": "usage_limit_reached"}}]
        with patch("pyxida.ai.codex_status", return_value={"configured": True}), patch("pyxida.ai.codex_binary", return_value="codex"), \
             patch("pyxida.ai.subprocess.Popen", side_effect=ExhaustedProcess) as start:
            with self.assertRaises(WorkflowError) as caught:
                Agent().call("Return ready", {}, obj(ready={"type": "boolean"}))
        self.assertEqual(start.call_count, 1)
        self.assertEqual(caught.exception.code, "codex_usage")

    def test_uses_local_chatgpt_codex_stdin_and_schema_without_api_credentials(self):
        processes = []
        def spawn(command, **kwargs):
            process = FakeProcess(command, **kwargs); processes.append(process); return process
        with patch("pyxida.ai.codex_status", return_value={"configured": True}), patch("pyxida.ai.codex_binary", return_value="codex"), \
             patch("pyxida.ai.subprocess.Popen", side_effect=spawn), patch.dict(os.environ, {"OPENAI_API_KEY": "unused", "CODEX_API_KEY": "unused"}):
            result = Agent().call("Return ready", {"private": "candidate details"}, obj(ready={"type": "boolean"}))
        self.assertEqual(result, {"ready": True})
        process = processes[0]
        self.assertEqual(process.command[-1], "-")
        self.assertNotIn("candidate details", " ".join(process.command))
        self.assertIn("candidate details", process.prompt)
        self.assertNotIn("OPENAI_API_KEY", process.kwargs["env"])
        self.assertNotIn("CODEX_API_KEY", process.kwargs["env"])
        self.assertIn("read-only", process.command)
        self.assertIn('web_search="disabled"', process.command)

    def test_research_rejects_a_run_without_native_search_events(self):
        with patch("pyxida.ai.codex_status", return_value={"configured": True}), patch("pyxida.ai.codex_binary", return_value="codex"), \
             patch("pyxida.ai.subprocess.Popen", side_effect=FakeProcess):
            with self.assertRaisesRegex(WorkflowError, "did not perform a web search"):
                Agent().call("Search", {}, search=True)

    def test_valid_search_events_accept_structured_source_output(self):
        class SearchProcess(FakeProcess):
            result = {"text": "Research", "sources": DRAFTS["sources"]}
            events = [{"type": "item.completed", "item": {"type": "web_search", "query": "Swiss jobs"}}]
        with patch("pyxida.ai.codex_status", return_value={"configured": True}), patch("pyxida.ai.codex_binary", return_value="codex"), \
             patch("pyxida.ai.subprocess.Popen", side_effect=SearchProcess):
            research = Agent().call("Search", {}, search=True)
        self.assertEqual(research["sources"][0]["url"], JOB["url"])

    def test_malformed_structured_output_is_not_saved(self):
        class MalformedProcess(FakeProcess):
            result = {"wrong": True}
        with patch("pyxida.ai.codex_status", return_value={"configured": True}), patch("pyxida.ai.codex_binary", return_value="codex"), \
             patch("pyxida.ai.subprocess.Popen", side_effect=MalformedProcess):
            with self.assertRaises(WorkflowError):
                Agent().call("Return ready", {}, obj(ready={"type": "boolean"}))


if __name__ == "__main__":
    unittest.main()

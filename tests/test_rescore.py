"""Re-scoring saved matches and refreshing drafts after the profile changes."""
import base64
import copy
import tempfile
import unittest
from unittest.mock import patch

from pyxida.ai import Agent, WorkflowError, score_job
from pyxida.app import Application
from pyxida.storage import Store
from test_features import POSTING
from test_pyxida import PyxidaAgent
from test_workflow import CV, JOB, PROFILE, QUOTE

CERT = "Certified Anti-Money Laundering Specialist (CAMS), 2025. Certificate number 12345 issued by ACAMS."


class RescoringAgent(PyxidaAgent):
    def __init__(self):
        super().__init__()
        self.new_scores = {"skills": 95, "experience": 85, "culture": 60, "career": 85}
        self.language_gate = "PASS"
        self.skip = set()

    def rescore(self, profile, preferences, evidence, jobs, history=(), context=None):
        self.calls.append(("rescore", [j["id"] for j in jobs], CERT[:20] in evidence))
        result = {}
        for job in jobs:
            if job["id"] in self.skip:
                continue
            updated = {**copy.deepcopy(job), "scores": dict(self.new_scores), "language_gate": self.language_gate,
                       "rescore_note": "The CAMS certificate covers the AML qualification.",
                       "previous": {"overall": job.get("overall"), "fit": job.get("fit"), "vetoed": bool(job.get("vetoes"))}}
            result[job["id"]] = score_job(updated)
        return result


class RescoreWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.agent = RescoringAgent()
        self.app = Application(self.store, self.agent)
        self.id = self.app.post("/api/intake", {"text": CV})["id"]
        self.post("/api/search")
        self.post("/api/selection", selected=["job1"])
        self.post("/api/materials", job_id="job1")

    def tearDown(self):
        self.temp.cleanup()

    def post(self, route, **data):
        return self.app.post(route, {"id": self.id, **data})

    def add_certificate(self):
        return self.post("/api/documents", action="add", type="diploma", filename="cams.txt",
                         file=base64.b64encode(CERT.encode()).decode())

    def calls(self, kind):
        return [c for c in self.agent.calls if c[0] == kind]

    def test_profile_change_then_rescore_updates_every_stale_match(self):
        state = self.add_certificate()
        job = state["shortlist"]["jobs"][0]
        self.assertNotEqual(job["revision"], state["revision"])
        with self.assertRaises(WorkflowError):
            self.post("/api/materials", job_id="job1", regenerate=True)

        state = self.post("/api/rescore")
        job = state["shortlist"]["jobs"][0]
        self.assertEqual(job["revision"], state["revision"])
        self.assertEqual(job["previous"]["overall"], 77)
        self.assertEqual(job["overall"], 84)
        self.assertIn("CAMS", job["rescore_note"])
        self.assertEqual(state["shortlist"]["rescored"]["count"], 1)
        self.assertEqual(state["selected"], ["job1"])
        self.assertTrue(self.calls("rescore")[0][2], "the new certificate must be in the evidence")
        # An unsent application follows the new score.
        self.assertEqual(state["applications"]["job1"]["job"]["overall"], 84)
        with self.assertRaisesRegex(WorkflowError, "already scored"):
            self.post("/api/rescore")

    def test_refresh_draft_redrafts_without_rescoring_a_fresh_match(self):
        self.post("/api/track", job_id="job1", status="applied", note="Sent by email")
        self.store.get(self.id)  # sanity: profile exists
        state = self.store.get(self.id)
        state["drafts"]["job1"]["form_answers"] = {"answers": [{"label": "Why us?", "answer": "Because."}]}
        self.store.save(state)
        self.add_certificate()
        self.post("/api/rescore")
        materials_before = len(self.calls("materials"))

        state = self.post("/api/refresh-draft", job_id="job1")
        self.assertEqual(len(self.calls("rescore")), 1, "the match was already fresh")
        self.assertEqual(len(self.calls("materials")), materials_before + 1)
        draft = state["drafts"]["job1"]
        self.assertEqual(draft["revision"], state["revision"])
        self.assertEqual(draft["job"]["overall"], 84)
        self.assertEqual(draft["form_answers"]["answers"][0]["label"], "Why us?")
        application = state["applications"]["job1"]
        self.assertEqual(application["status"], "applied")
        self.assertEqual(application["job"]["overall"], 77, "a sent application keeps the score it was sent with")
        with self.assertRaisesRegex(WorkflowError, "already use"):
            self.post("/api/refresh-draft", job_id="job1")

    def test_refresh_draft_rescores_a_job_no_longer_in_the_shortlist(self):
        self.add_certificate()
        state = self.store.get(self.id)
        state["shortlist"]["jobs"] = []
        state["selected"] = []
        self.store.save(state)

        state = self.post("/api/refresh-draft", job_id="job1")
        self.assertEqual(self.calls("rescore")[-1][1], ["job1"])
        self.assertEqual(state["drafts"]["job1"]["job"]["overall"], 84)
        self.assertEqual(state["shortlist"]["jobs"], [], "the shortlist is not repopulated")

    def test_a_gate_failure_moves_the_match_to_vetoed_and_unselects_it(self):
        self.add_certificate()
        self.agent.language_gate = "FAIL"
        state = self.post("/api/rescore")
        self.assertEqual(state["shortlist"]["jobs"], [])
        self.assertEqual(state["shortlist"]["vetoed"][0]["id"], "job1")
        self.assertEqual(state["selected"], [])

    def test_jobs_the_model_leaves_out_stay_stale(self):
        self.add_certificate()
        self.agent.skip = {"job1"}
        with self.assertRaisesRegex(WorkflowError, "did not return"):
            self.post("/api/rescore")
        with self.assertRaisesRegex(WorkflowError, "did not return"):
            self.post("/api/refresh-draft", job_id="job1")


class SkillAcquiredTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.agent = RescoringAgent()
        self.app = Application(Store(self.temp.name), self.agent)
        self.id = self.app.post("/api/intake", {"text": CV})["id"]
        self.post("/api/search")
        self.post("/api/evaluate", text=POSTING)
        self.state = self.post("/api/upskill")

    def tearDown(self):
        self.temp.cleanup()

    def post(self, route, **data):
        return self.app.post(route, {"id": self.id, **data})

    def test_marking_a_skill_acquired_updates_the_profile_and_flags_matches(self):
        before = self.state["revision"]
        state = self.post("/api/skill-acquired", skill="german c1", details="Passed the Goethe C1 exam in March 2026.")
        self.assertEqual(state["revision"], before + 1)
        statement = state["discussion"][-2]
        self.assertEqual(statement["role"], "user")
        self.assertIn('acquired "German C1"', statement["text"])
        self.assertIn("Goethe C1", statement["text"])
        self.assertEqual(state["upskill"]["gaps"][0]["acquired"]["details"], "Passed the Goethe C1 exam in March 2026.")
        self.assertTrue(all(j["revision"] != state["revision"] for j in state["shortlist"]["jobs"]),
                        "matches now need re-scoring")

    def test_unknown_skills_and_empty_details_are_rejected(self):
        with self.assertRaisesRegex(WorkflowError, "growth plan"):
            self.post("/api/skill-acquired", skill="Underwater basket weaving", details="Certified in 2026 by the guild.")
        with self.assertRaisesRegex(WorkflowError, "how you acquired"):
            self.post("/api/skill-acquired", skill="German C1", details="yes")


class AgentRescoreTests(unittest.TestCase):
    @staticmethod
    def saved(index):
        job = copy.deepcopy(JOB)
        job.update(id=f"job{index}", url=f"https://example.com/jobs/{index}")
        return score_job(job)

    @staticmethod
    def answer(job_id, quote=QUOTE):
        return {"id": job_id, "why": "Stronger now", "evidence": [quote, "Invented claim not in the CV"],
                "gaps": [], "change": "The certificate covers the AML requirement.",
                "scores": {"skills": 90, "experience": 90, "culture": 60, "career": 90},
                "location_check": "PASS", "language_gate": "PASS", "language_note": "English",
                "eligibility": "UNVERIFIED", "eligibility_note": "Silent"}

    def test_batches_without_web_search_filter_quotes_and_ignore_unknown_ids(self):
        jobs = [self.saved(i) for i in range(12)]
        first = {"jobs": [self.answer(f"job{i}") for i in range(10)] + [self.answer("job99")]}
        second = {"jobs": [self.answer("job10"), self.answer("job10")]}
        with patch.object(Agent, "call", side_effect=[first, second]) as call:
            result = Agent().rescore(PROFILE, {"country": "Switzerland"}, CV, jobs)
        self.assertEqual(call.call_count, 2)
        self.assertEqual([len(c.args[1]["listings"]) for c in call.call_args_list], [10, 2])
        self.assertTrue(all(not c.kwargs.get("search") for c in call.call_args_list))
        self.assertNotIn("posting_text", call.call_args_list[0].args[1]["listings"][0])
        self.assertEqual(sorted(result), sorted(f"job{i}" for i in range(11)), "job11 was left out, job99 is unknown")
        job = result["job0"]
        self.assertEqual(job["evidence"], [QUOTE])
        self.assertEqual(job["overall"], 86)
        self.assertEqual(job["fit"], "Strong")
        self.assertEqual(job["previous"], {"overall": 77, "fit": "Strong", "vetoed": False})
        self.assertEqual(job["rescore_note"], "The certificate covers the AML requirement.")
        self.assertNotIn("change", job)
        self.assertEqual(job["url"], "https://example.com/jobs/0", "listing details are kept")


if __name__ == "__main__":
    unittest.main()

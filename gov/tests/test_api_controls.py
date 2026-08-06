"""End-to-end tests for the console API — the landing page's controls, over real HTTP.

These drive the same endpoints a browser drives and assert the EFFECT on world state,
not merely a 200. A control that returns success without changing anything is the
failure mode that a status-code assertion cannot see.

They also cover what it means to be a service: several anonymous guests share one
settlement, so a power must be refused to a guest, attributed to whoever used it, and
must not let two people answer the same question twice.
"""

import json
import os
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import _env

os.environ["CONSOLE_TOKEN"] = "test-token"      # set BEFORE the console imports it
os.environ["PUBLIC_CHAT"] = "1"

import anchor as A                              # noqa: E402
import models as M                              # noqa: E402
import sim                                      # noqa: E402
import sim_console as C                         # noqa: E402

TOKEN = "test-token"


class ApiBase(_env.Base):
    """A live server on a real socket, one per test class."""

    ISOLATE_ANCHOR = False                      # the console holds its own anchor handle

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), C.Handler)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        super().setUp()
        os.environ["CONSOLE_TOKEN"] = TOKEN
        os.environ["PUBLIC_CHAT"] = "1"
        self.close_all_gates()

    def tearDown(self):
        self.close_all_gates()
        super().tearDown()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path, token="", agent="tester"):
        req = urllib.request.Request(self.url(path), headers={
            "X-Console-Token": token, "User-Agent": agent})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                status, body = r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            status, body = e.code, (e.read() or b"").decode()
        try:
            return status, json.loads(body)
        except json.JSONDecodeError:
            return status, body

    def post(self, path, body, token="", agent="tester"):
        req = urllib.request.Request(
            self.url(path), data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "X-Console-Token": token,
                     "User-Agent": agent})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raw = e.read() or b"{}"
            try:
                return e.code, json.loads(raw)
            except json.JSONDecodeError:
                return e.code, {}

    def close_all_gates(self):
        """Answer anything still parked, so one test's leftovers are never the next
        test's mystery. The world under test is shared, exactly like the real one."""
        with C._LOCK:
            for uid, u in C._by_uid().items():
                if u.pending and u.pending.get("reversible") is False:
                    sim.resume(C._GRAPH, uid, "dismiss")

    def wait_for_gate(self, uid, timeout=5.0):
        """Poll /api/home until it reports this gate.

        The snapshot is deliberately cached for a second so that N pollers cost one
        computation, which means the page is eventually consistent by design. A test
        that demands sub-second freshness would be testing the cache, not the gate."""
        import time as _t
        deadline = _t.time() + timeout
        while _t.time() < deadline:
            _, home = self.get("/api/home", token=TOKEN)
            gate = home.get("gate")
            if gate and gate["unit_id"] == uid:
                return gate
            _t.sleep(0.2)
        self.fail(f"/api/home never reported the gate for {uid}")

    def fingerprint(self, uid):
        with C._LOCK:
            unit = C._by_uid().get(uid)
            return C._gate_fingerprint(uid, unit.pending if unit else None)

    def open_gate(self):
        """Park a herald at the irreversible gate exactly the way the world does —
        including minting its id through the console's own counter, since only units
        the console knows about are read live (_live_ids)."""
        with C._LOCK:
            C._S["heralds"] += 1
            A.config_set("heralds", str(C._S["heralds"]))
            uid = f"herald-{C._S['heralds']:02d}"
            sim.spawn(C._GRAPH, uid, "herald")
            C._S["gate_since"] = C._S["turn"]
        unit = C._by_uid().get(uid)
        assert unit is not None and unit.pending, "the gate did not open"
        return uid


class TestRoutes(ApiBase):

    def test_landing_page_is_served_at_the_root(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("Can your model run it?", body)

    def test_the_operator_console_keeps_its_own_route(self):
        status, body = self.get("/console")
        self.assertEqual(status, 200)
        self.assertIn("Vision", body)

    def test_every_page_route_answers(self):
        for path in ("/providers", "/leaderboard", "/agents", "/chats", "/rules",
                     "/logs", "/skills", "/work"):
            status, _ = self.get(path)
            self.assertEqual(status, 200, path)

    def test_unknown_routes_are_404(self):
        status, _ = self.get("/nope")
        self.assertEqual(status, 404)


class TestHomePayload(ApiBase):

    def test_it_carries_what_the_page_needs(self):
        _, d = self.get("/api/home")
        for key in ("live", "world", "counts", "arena", "runs", "gate", "proposals",
                    "visions", "cap", "participants", "providers", "you"):
            self.assertIn(key, d)

    def test_it_never_serves_a_key_or_the_token(self):
        os.environ["OPENAI_API_KEY"] = "sk-secret-value"
        _, raw = self.get("/api/home")
        blob = json.dumps(raw)
        self.assertNotIn("sk-secret-value", blob)
        self.assertNotIn(TOKEN, blob)

    def test_a_guest_is_told_they_are_a_guest(self):
        _, d = self.get("/api/home")
        self.assertFalse(d["you"]["can_act"])
        self.assertTrue(d["you"]["guest"])

    def test_the_operator_is_told_their_powers_are_live(self):
        _, d = self.get("/api/home", token=TOKEN)
        self.assertTrue(d["you"]["can_act"])

    def test_guests_are_distinguished_from_one_another(self):
        _, a = self.get("/api/home", agent="visitor-a")
        _, b = self.get("/api/home", agent="visitor-b")
        self.assertNotEqual(a["you"]["guest"], b["you"]["guest"])

    def test_the_same_guest_keeps_one_identity(self):
        _, a = self.get("/api/home", agent="steady")
        _, b = self.get("/api/home", agent="steady")
        self.assertEqual(a["you"]["guest"], b["you"]["guest"])


class TestFleetBudgetVisibility(ApiBase):
    """A suspended lifecycle is unbounded, so the overrun has to be a number the page
    can show. Found in production: two agents at 10.6x their budget while the console
    reported 100% utilisation and no stall."""

    def test_the_payload_reports_how_far_past_budget_the_fleet_is(self):
        _, d = self.get("/api/home")
        self.assertIn("fleet", d)
        for key in ("over_budget", "worst_pct", "size"):
            self.assertIn(key, d["fleet"])

    def test_budget_use_is_reported_uncapped(self):
        _, snap = self.get("/api/state")
        for a in snap["agents"]:
            self.assertIn("budget_used_pct", a)
            self.assertIn("over_budget", a)
            # the clamped number is for the bar; the true one must not be clamped
            self.assertGreaterEqual(a["budget_used_pct"], a["utilisation_pct"])


class TestUnlockedDeployment(ApiBase):
    """With no CONSOLE_TOKEN configured the settlement is open to everyone. That is a
    legitimate way to run it — but the page must say so, not tell each visitor they
    personally hold a token nobody set."""

    def setUp(self):
        super().setUp()
        os.environ.pop("CONSOLE_TOKEN", None)

    def tearDown(self):
        os.environ["CONSOLE_TOKEN"] = TOKEN
        super().tearDown()

    def test_an_anonymous_visitor_can_act_but_the_page_is_told_it_is_unlocked(self):
        _, d = self.get("/api/home")
        self.assertTrue(d["you"]["can_act"])
        self.assertFalse(d["token_required"])

    def test_actions_still_record_which_visitor_took_them(self):
        # everyone is "operator" only in the sense that nothing stops them; the record
        # must still be able to answer who
        status, _ = self.post("/api/vision", {"vision": "consolidate"})
        self.assertEqual(status, 200)
        self.assertTrue(any("by " in e for e in A.event_log(20)))


class TestGuestsAreRefused(ApiBase):

    def test_a_guest_cannot_answer_the_gate(self):
        uid = self.open_gate()
        status, _ = self.post("/api/resume", {"unit_id": uid, "decision": "approve"})
        self.assertEqual(status, 401)
        with C._LOCK:
            self.assertTrue(C._by_uid()[uid].pending)       # untouched

    def test_a_guest_cannot_adopt_a_vision(self):
        before = C._S["vision_key"]
        status, _ = self.post("/api/vision", {"vision": "consolidate"})
        self.assertEqual(status, 401)
        self.assertEqual(C._S["vision_key"], before)

    def test_a_guest_cannot_move_the_cap(self):
        import governor as G
        before = G.TOKEN_CAP
        status, _ = self.post("/api/cap", {"token_cap": before + 50_000})
        self.assertEqual(status, 401)
        self.assertEqual(G.TOKEN_CAP, before)

    def test_a_guest_cannot_assign_a_model(self):
        status, _ = self.post("/api/models", {"role": "governor", "model": "openai"})
        self.assertEqual(status, 401)
        self.assertEqual(M.assignments()["governor"], "")

    def test_a_guest_may_talk_when_public_chat_is_on(self):
        status, _ = self.post("/api/chat", {"thread": "chief", "body": "hello"},
                              agent="chatty-guest")
        self.assertEqual(status, 200)

    def test_a_guest_is_rate_limited(self):
        self.post("/api/chat", {"thread": "chief", "body": "one"}, agent="flooder")
        status, body = self.post("/api/chat", {"thread": "chief", "body": "two"},
                                 agent="flooder")
        self.assertEqual(status, 429)
        self.assertIn("seconds", body.get("error", ""))


class TestGate(ApiBase):

    def test_approving_clears_the_gate_and_records_the_actor(self):
        uid = self.open_gate()
        status, _ = self.post("/api/resume", {"unit_id": uid, "decision": "approve"},
                              token=TOKEN)
        self.assertEqual(status, 200)
        with C._LOCK:
            self.assertFalse(C._by_uid()[uid].pending)
        self.assertTrue(any("by operator" in e for e in A.event_log(30)))

    def test_rejecting_also_clears_it(self):
        uid = self.open_gate()
        self.post("/api/resume", {"unit_id": uid, "decision": "reject"}, token=TOKEN)
        with C._LOCK:
            self.assertFalse(C._by_uid()[uid].pending)

    def test_an_invalid_decision_is_refused(self):
        uid = self.open_gate()
        status, _ = self.post("/api/resume", {"unit_id": uid, "decision": "maybe"},
                              token=TOKEN)
        self.assertEqual(status, 400)
        with C._LOCK:
            self.assertTrue(C._by_uid()[uid].pending)

    def test_answering_a_gate_nobody_is_waiting_at_is_a_conflict(self):
        uid = self.open_gate()
        self.post("/api/resume", {"unit_id": uid, "decision": "approve"}, token=TOKEN)
        status, body = self.post("/api/resume", {"unit_id": uid, "decision": "approve"},
                                 token=TOKEN)
        self.assertEqual(status, 409)
        self.assertTrue(body.get("stale"))

    def test_a_stale_fingerprint_is_refused(self):
        uid = self.open_gate()
        status, body = self.post(
            "/api/resume",
            {"unit_id": uid, "decision": "approve", "expect": "not-the-real-one"},
            token=TOKEN)
        self.assertEqual(status, 409)
        with C._LOCK:
            self.assertTrue(C._by_uid()[uid].pending)       # nothing was decided

    def test_the_fingerprint_from_the_page_is_accepted(self):
        uid = self.open_gate()
        gate = self.wait_for_gate(uid)
        self.assertEqual(gate["waiting"], 1)
        status, _ = self.post("/api/resume",
                              {"unit_id": gate["unit_id"], "decision": "approve",
                               "expect": gate["fingerprint"]}, token=TOKEN)
        self.assertEqual(status, 200)

    def test_two_guests_racing_the_same_gate_produce_one_decision(self):
        uid = self.open_gate()
        fp = self.fingerprint(uid)
        results, lock = [], threading.Lock()

        def answer(decision):
            st, _ = self.post("/api/resume",
                              {"unit_id": uid, "decision": decision, "expect": fp},
                              token=TOKEN)
            with lock:
                results.append(st)

        threads = [threading.Thread(target=answer, args=(d,))
                   for d in ("approve", "reject")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), [200, 409],
                         f"exactly one answer must win, got {results}")


class TestHumanPowers(ApiBase):

    def test_adopting_a_vision_changes_the_goal(self):
        target = "consolidate" if C._S["vision_key"] != "consolidate" else "feudal"
        status, _ = self.post("/api/vision", {"vision": target}, token=TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual(C._S["vision_key"], target)
        self.assertTrue(any("adopted the Vision" in e for e in A.event_log(20)))

    def test_an_unknown_vision_is_refused(self):
        status, _ = self.post("/api/vision", {"vision": "world-domination"}, token=TOKEN)
        self.assertEqual(status, 400)

    def test_setting_the_cap_moves_it_and_is_attributed(self):
        import governor as G
        want = G.TOKEN_CAP + 25_000
        status, _ = self.post("/api/cap", {"token_cap": want}, token=TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual(G.TOKEN_CAP, want)
        self.assertTrue(any("set the compute cap" in e for e in A.event_log(20)))

    def test_assigning_a_seat_takes_effect_and_is_recorded(self):
        os.environ["OPENAI_API_KEY"] = "sk-test"
        status, body = self.post("/api/models",
                                 {"role": "governor", "model": "openai:some-model"},
                                 token=TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual(body["roles"]["governor"], "openai:some-model")
        self.assertEqual(M.resolve("governor")["model"], "some-model")
        self.assertTrue(any("assigned governor" in e for e in A.event_log(20)))
        self.post("/api/models", {"role": "governor", "model": ""}, token=TOKEN)

    def test_assigning_a_provider_without_a_model_is_reported_as_a_fault(self):
        os.environ["OPENAI_API_KEY"] = "sk-test"
        _, body = self.post("/api/models", {"role": "Ledger", "model": "openai"},
                            token=TOKEN)
        self.assertIn("Ledger", body["faults"])
        self.assertEqual(body["tier"], "incomplete")
        self.post("/api/models", {"role": "Ledger", "model": ""}, token=TOKEN)

    def test_an_unregistered_provider_is_refused_even_for_the_operator(self):
        status, _ = self.post("/api/models", {"role": "governor", "model": "ghostlab"},
                              token=TOKEN)
        self.assertEqual(status, 400)

    def test_a_development_can_be_adopted(self):
        C._S["dev_proposal"] = {"name": "test_mill", "cost": {"food": 1, "wood": 1,
                                                              "gold": 1},
                                "kind": "yield_pct", "value": 10, "resource": "food",
                                "rank": 2, "source": "test", "board_approved": True}
        status, _ = self.post("/api/development", {"action": "adopt"}, token=TOKEN)
        self.assertEqual(status, 200)
        self.assertIsNone(C._S["dev_proposal"])
        self.assertIn("test_mill", [d["name"] for d in sim.dev_catalog()])

    def test_a_development_can_be_rejected_and_leaves_no_trace_in_the_catalog(self):
        C._S["dev_proposal"] = {"name": "rejected_mill", "cost": {"food": 1},
                                "kind": "yield_pct", "value": 5, "resource": "food",
                                "rank": 2, "source": "test"}
        status, _ = self.post("/api/development", {"action": "reject"}, token=TOKEN)
        self.assertEqual(status, 200)
        self.assertIsNone(C._S["dev_proposal"])
        self.assertNotIn("rejected_mill", [d["name"] for d in sim.dev_catalog()])

    def test_acting_on_no_proposal_is_refused(self):
        C._S["dev_proposal"] = None
        status, _ = self.post("/api/development", {"action": "adopt"}, token=TOKEN)
        self.assertEqual(status, 400)

    def test_chat_reaches_the_thread_and_is_answered(self):
        status, body = self.post("/api/chat", {"thread": "chief", "body": "status?"},
                                 token=TOKEN)
        self.assertEqual(status, 200)
        thread = body["threads"]["chief"]
        self.assertGreaterEqual(len(thread), 2)             # the ask and the answer
        self.assertEqual(thread[-2]["sender"], "operator")

    def test_an_empty_message_is_refused(self):
        status, _ = self.post("/api/chat", {"thread": "chief", "body": "  "},
                              token=TOKEN)
        self.assertEqual(status, 400)

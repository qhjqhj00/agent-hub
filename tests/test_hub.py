"""The v2 hub over HTTP: sessions, the inbox and its leases, routing to the liaison, tasks, the board."""
import threading
import time
import unittest

from util import Hub


class HubTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hub = Hub()
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.hub.stop()

    def reg(self, name=None, kind="claude", ok=True, **kw):
        """Register a fresh agent; returns (name, session_id, token)."""
        if name is None:
            HubTest.n += 1
            name = "a%d" % HubTest.n
        body = dict(name=name, kind=kind, cwd="/w/" + name, push="pull")
        body.update(kw)
        code, out = self.hub.call("POST", "/v1/sessions", body)
        if ok:
            self.assertEqual(code, 200, out)
            return name, out["session_id"], out["token"]
        return code, out

    def as_(self, tok, method, path, body=None):
        return self.hub.call(method, path, body, session=tok)

    def pull(self, sid, tok, wait=0):
        code, out = self.as_(tok, "GET", "/v1/sessions/%s/pull?wait=%d" % (sid, wait))
        self.assertEqual(code, 200, out)
        return out["items"]

    # ------------------------------------------------------------ auth and identity

    def test_public_and_private_paths(self):
        h = self.hub
        self.assertEqual(h.call("GET", "/healthz", token="")[0], 200)
        for path in ("/v1/agents", "/v1/tasks", "/v1/board"):
            self.assertEqual(h.call("GET", path, token="")[0], 401, path)
        self.assertEqual(h.call("GET", "/v1/agents", token="wrong")[0], 401)

    def test_kind_is_required(self):
        code, out = self.hub.call("POST", "/v1/sessions", {"name": "nokind", "cwd": "/x"})
        self.assertEqual(code, 400)
        self.assertIn("kind", out["error"])

    def test_name_in_body_is_ignored(self):
        """v1 let anyone act as anyone by putting a name in the body; v2 knows you by your session."""
        a, sa, ta = self.reg()
        b, sb, tb = self.reg()
        code, out = self.as_(ta, "POST", "/v1/posts", {"to": b, "body": "hi", "agent": b, "from": b})
        self.assertEqual(code, 200, out)
        items = self.pull(sb, tb)
        self.assertEqual(items[0]["from"], a)
        # no session, no agent: the plain node token cannot post as anyone
        _, tok = self.hub.call("POST", "/v1/admin/nodes", {"name": "box1"})
        code, _ = self.hub.call("POST", "/v1/posts", {"to": b, "body": "x"}, token=tok["token"])
        self.assertEqual(code, 401)

    def test_live_name_conflict_and_release_on_end(self):
        name, sid, tok = self.reg()
        code, out = self.reg(name, ok=False, conversation="other")
        self.assertEqual(code, 409)
        self.assertIn("in use", out["error"])
        self.assertEqual(self.as_(tok, "POST", "/v1/sessions/%s/end" % sid, {})[0], 200)
        # the name is free at once, not after the heartbeat window
        self.reg(name)
        self.assertEqual(self.as_(tok, "POST", "/v1/sessions/%s/beat" % sid, {})[0], 401)

    def test_same_conversation_takes_over(self):
        name, sid, tok = self.reg(conversation="conv-1")
        name2, sid2, tok2 = self.reg(name, conversation="conv-1")
        self.assertNotEqual(sid, sid2)
        self.assertEqual(self.as_(tok, "POST", "/v1/sessions/%s/beat" % sid, {})[0], 401)
        self.assertEqual(self.as_(tok2, "POST", "/v1/sessions/%s/beat" % sid2, {})[0], 200)

    def test_cannot_use_another_session_id(self):
        a, sa, ta = self.reg()
        b, sb, tb = self.reg()
        self.assertEqual(self.as_(ta, "GET", "/v1/sessions/%s/pull?wait=0" % sb)[0], 403)

    def test_state_meta_and_card(self):
        name, sid, tok = self.reg(kind="agy", push="bridge", meta={"t3_thread": "th-1"})
        self.assertEqual(self.as_(tok, "POST", "/v1/sessions/%s/state" % sid, {"state": "busy"})[0], 200)
        self.assertEqual(self.as_(tok, "POST", "/v1/sessions/%s/meta" % sid, {"conversation": "c-9"})[0], 200)
        code, w = self.as_(tok, "GET", "/v1/whoami")
        self.assertEqual(w["session"]["meta"]["t3_thread"], "th-1")
        self.assertEqual(w["session"]["conversation"], "c-9")
        code, card = self.hub.call("GET", "/v1/agents/" + name)
        self.assertEqual((card["kind"], card["state"], card["push"], card["presence"]), ("agy", "busy", "bridge", "live"))
        self.assertTrue(card["start"])

    # ------------------------------------------------------------ inbox

    def test_lease_and_redelivery(self):
        a, sa, ta = self.reg()
        b, sb, tb = self.reg()
        self.as_(ta, "POST", "/v1/posts", {"to": b, "body": "one"})
        first = self.pull(sb, tb)
        self.assertEqual(len(first), 1)
        # leased: a second taker (a hook beside a bridge) does not get it again
        self.assertEqual(self.pull(sb, tb), [])
        self.as_(tb, "POST", "/v1/sessions/%s/delivered" % sb, {"ids": [first[0]["id"]]})
        code, card = self.hub.call("GET", "/v1/agents/" + b)
        self.assertEqual(card["pending"], 0)

    def test_undelivered_lease_comes_back_to_the_next_session(self):
        a, sa, ta = self.reg()
        b, sb, tb = self.reg(conversation="cb")
        self.as_(ta, "POST", "/v1/posts", {"to": b, "body": "lost"})
        self.assertEqual(len(self.pull(sb, tb)), 1)  # taken, never reported delivered
        _, sb2, tb2 = self.reg(b, conversation="cb")  # its agent came back (resume)
        items = self.pull(sb2, tb2)
        self.assertEqual([i["text"].split("\n")[1] for i in items], ["lost"])

    def test_long_poll_wakes_on_new_item(self):
        a, sa, ta = self.reg()
        b, sb, tb = self.reg()
        got = []
        t = threading.Thread(target=lambda: got.extend(self.pull(sb, tb, wait=20)))
        t0 = time.time()
        t.start()
        time.sleep(0.5)
        self.as_(ta, "POST", "/v1/posts", {"to": b, "body": "wake"})
        t.join(10)
        self.assertEqual(len(got), 1)
        self.assertLess(time.time() - t0, 8)

    def test_inbox_marks_delivered(self):
        a, sa, ta = self.reg()
        b, sb, tb = self.reg()
        self.as_(ta, "POST", "/v1/posts", {"to": b, "body": "x"})
        self.assertEqual(len(self.as_(tb, "GET", "/v1/inbox")[1]["items"]), 1)
        self.assertEqual(self.as_(tb, "GET", "/v1/inbox")[1]["items"], [])

    def test_reply_threads(self):
        a, sa, ta = self.reg()
        b, sb, tb = self.reg()
        _, m = self.as_(ta, "POST", "/v1/posts", {"to": b, "body": "q?"})
        _, r = self.as_(tb, "POST", "/v1/posts", {"reply_to": m["post_id"], "body": "a!"})
        self.assertEqual(r["delivered_to"], [a])
        self.assertEqual(r["thread_id"], m["thread_id"])

    # ------------------------------------------------------------ the human, via the liaison

    def test_human_goes_to_the_liaison(self):
        li, sl, tl = self.reg("p1-agy", kind="agy", project="p1")
        dev, sd, td = self.reg(project="p1", role="dev")
        _, out = self.as_(td, "POST", "/v1/posts", {"to": "human", "body": "look at this"})
        self.assertEqual(out["delivered_to"], [], "no liaison set yet: saved, delivered nowhere")
        self.assertEqual(self.hub.call("POST", "/v1/admin/projects/p1", {"liaison": li})[0], 200)
        _, out = self.as_(td, "POST", "/v1/posts", {"to": "human", "body": "now?"})
        self.assertEqual(out["delivered_to"], [li])
        # a question to the human inside a task reaches the liaison too, with the liaison's instructions
        _, t = self.as_(td, "POST", "/v1/tasks", {"to": dev, "title": "self task"})
        tid = t["task"]["id"]
        self.as_(td, "POST", "/v1/tasks/%d" % tid, {"kind": "question", "body": "ship it?", "to": "human"})
        texts = [i["text"] for i in self.pull(sl, tl)]
        self.assertTrue(any("ship it?" in x and "liaison" in x for x in texts), texts)
        code, card = self.hub.call("GET", "/v1/agents/" + li)
        self.assertEqual(card["liaison_of"], ["p1"])

    def test_task_from_the_human_reports_to_the_liaison(self):
        li, sl, tl = self.reg("p2-agy", kind="agy", project="p2")
        self.hub.call("POST", "/v1/admin/projects/p2", {"liaison": li})
        dev, sd, td = self.reg(project="p2", role="dev")
        _, t = self.hub.call("POST", "/v1/tasks", {"to": dev, "title": "from the dashboard", "project": "p2"})
        tid = t["task"]["id"]
        self.assertEqual(t["task"]["created_by"], "human")
        self.as_(td, "POST", "/v1/tasks/%d" % tid, {"kind": "result", "body": "done it"})
        items = self.pull(sl, tl)
        self.assertTrue(any("done it" in i["text"] for i in items))
        # the liaison accepts on the human's behalf
        code, out = self.as_(tl, "POST", "/v1/tasks/%d" % tid, {"kind": "close", "state": "done", "body": "ok per human"})
        self.assertEqual(out["task"]["state"], "done")

    # ------------------------------------------------------------ tasks

    def test_task_lifecycle(self):
        a, sa, ta = self.reg(project="tp")
        b, sb, tb = self.reg(project="tp", role="be")
        _, t = self.as_(ta, "POST", "/v1/tasks", {"to": "be@tp", "title": "build it", "goal": "g", "context": "c"})
        tid = t["task"]["id"]
        self.assertEqual(t["task"]["state"], "open")
        self.assertIn("New task #%d" % tid, self.pull(sb, tb)[0]["text"])
        _, out = self.as_(tb, "POST", "/v1/tasks/%d" % tid, {"kind": "plan", "body": "1. do"})
        self.assertEqual((out["task"]["state"], out["task"]["owner"]), ("working", b))
        _, out = self.as_(tb, "POST", "/v1/tasks/%d" % tid, {"kind": "result", "body": "did"})
        self.assertEqual(out["task"]["state"], "review")
        _, out = self.as_(ta, "POST", "/v1/tasks/%d" % tid, {"kind": "comment", "body": "redo"})
        self.assertEqual(out["task"]["state"], "working")
        _, out = self.as_(ta, "POST", "/v1/tasks/%d" % tid, {"kind": "close", "state": "done"})
        self.assertEqual(out["task"]["state"], "done")
        _, full = self.hub.call("GET", "/v1/tasks/%d" % tid)
        self.assertEqual([p["kind"] for p in full["posts"]], ["task", "plan", "result", "comment", "closed"])
        kinds = [i["kind"] for i in self.pull(sa, ta)]
        self.assertEqual(kinds, ["plan", "result"])
        self.assertEqual([i["kind"] for i in self.pull(sb, tb)], ["comment", "closed"])

    def test_bad_kind(self):
        a, sa, ta = self.reg()
        _, t = self.as_(ta, "POST", "/v1/tasks", {"to": a, "title": "t"})
        code, out = self.as_(ta, "POST", "/v1/tasks/%d" % t["task"]["id"], {"kind": "needs_human", "body": "x"})
        self.assertEqual(code, 400)

    # ------------------------------------------------------------ board

    def test_board_secret_exports_and_note(self):
        a, sa, ta = self.reg()
        code, out = self.as_(ta, "POST", "/v1/board/secret/llm", {"value": '{"key": "s3cr3t"}', "env": {"LLM_KEY": "key"},
                                                                  "note": "an llm"})
        self.assertEqual(code, 200, out)
        _, board = self.as_(ta, "GET", "/v1/board")
        s = [x for x in board["secrets"] if x["name"] == "llm"][0]
        self.assertEqual(s["env_keys"], ["LLM_KEY"])
        self.assertNotIn("value", s)
        _, got = self.as_(ta, "GET", "/v1/board/secret/llm")
        self.assertEqual(got["exports"], ["export LLM_KEY=s3cr3t"])
        self.as_(ta, "POST", "/v1/board/note/llm-howto", {"title": "how", "body": "call it", "refs": ["llm"]})
        _, n = self.as_(ta, "GET", "/v1/board/note/llm-howto")
        self.assertEqual((n["body"], n["refs"], n["owner"]), ("call it", ["llm"], a))

    def test_archive(self):
        a, sa, ta = self.reg(project="arc")
        b, sb, tb = self.reg(project="arc")
        self.as_(ta, "POST", "/v1/sessions/%s/end" % sa, {})
        # bulk: everything without a running session (cutoff in the future) -> a, not b (live)
        code, out = self.hub.call("POST", "/v1/admin/archive", {"inactive_days": -1})
        self.assertEqual(code, 200, out)
        self.assertIn(a, out["archived"])
        self.assertNotIn(b, out["archived"])
        names = [x["name"] for x in self.hub.call("GET", "/v1/agents?project=arc")[1]["agents"]]
        self.assertEqual(names, [b])
        arch = {x["name"]: x for x in self.hub.call("GET", "/v1/agents?archived=1")[1]["agents"]}
        self.assertTrue(arch[a]["archived"])
        self.assertGreaterEqual(self.hub.call("GET", "/v1/overview")[1]["archived"], 1)
        # still addressable, sessions and history kept; coming back does not un-archive it
        _, sa2, ta2 = self.reg(a)
        _, m = self.as_(tb, "POST", "/v1/posts", {"to": a, "body": "still there?"})
        self.assertEqual(m["delivered_to"], [a])
        self.assertTrue(self.hub.call("GET", "/v1/agents/" + a)[1]["archived"])
        self.assertGreaterEqual(len(self.hub.call("GET", "/v1/agents/" + a)[1]["sessions"]), 2)
        # restore by hand, one or many
        self.hub.call("POST", "/v1/admin/agents/" + a, {"archived": False})
        self.assertFalse(self.hub.call("GET", "/v1/agents/" + a)[1]["archived"])
        self.hub.call("POST", "/v1/admin/archive", {"names": [a, b]})
        code, out = self.hub.call("POST", "/v1/admin/archive", {"names": [a, b], "restore": True})
        self.assertEqual(out["restored"], sorted([a, b]))
        # only the admin archives
        self.assertEqual(self.as_(tb, "POST", "/v1/admin/agents/" + a, {"archived": True})[0], 403)

    def test_liaison_hears_no_progress_noise(self):
        li, sl, tl = self.reg("p3-agy", kind="agy", project="p3")
        self.hub.call("POST", "/v1/admin/projects/p3", {"liaison": li})
        dev, sd, td = self.reg(project="p3", role="dev")
        _, t = self.hub.call("POST", "/v1/tasks", {"to": dev, "title": "for the human", "project": "p3"})
        tid = t["task"]["id"]
        for kind, body, to in (("plan", "1. x", ""), ("progress", "half way", ""), ("progress", "FYI human", "human"),
                               ("question", "A or B?", ""), ("result", "done", "")):
            self.as_(td, "POST", "/v1/tasks/%d" % tid, {"kind": kind, "body": body, "to": to})
        got = [(i["kind"], i["text"].split("\n")[1]) for i in self.pull(sl, tl)]
        self.assertEqual(got, [("plan", "1. x"), ("progress", "FYI human"), ("question", "A or B?"), ("result", "done")])
        _, full = self.hub.call("GET", "/v1/tasks/%d" % tid)
        self.assertIn("half way", [p["body"] for p in full["posts"]], "progress still in the thread")
        # the liaison as an ordinary party (it owns the task) still hears progress
        _, t2 = self.as_(td, "POST", "/v1/tasks", {"to": li, "title": "liaison's own task"})
        self.as_(td, "POST", "/v1/tasks/%d" % t2["task"]["id"], {"kind": "progress", "body": "note to owner"})
        self.assertIn("note to owner", "".join(i["text"] for i in self.pull(sl, tl)))

    def _db(self):
        import os, sqlite3
        return sqlite3.connect(os.path.join(self.hub.tmp, "hub", "hub.db"))

    def test_stale_queue_and_attention(self):
        a, sa, ta = self.reg(project="p4")
        b, sb, tb = self.reg(project="p4")
        self.as_(ta, "POST", "/v1/posts", {"to": b, "body": "stuck one"})
        self.as_(ta, "POST", "/v1/posts", {"to": b, "body": "fresh one"})
        card = self.hub.call("GET", "/v1/agents/" + b)[1]
        self.assertEqual((card["stale_queued"], card["oldest_queued_s"]), (0, 0))
        with self._db() as c:  # make the first one five minutes old
            c.execute("UPDATE inbox SET created_at=datetime('now','-300 seconds')||'+00:00' WHERE agent=? AND "
                      "text LIKE '%stuck one%'", (b,))
            c.execute("UPDATE inbox SET created_at=replace(created_at,' ','T') WHERE agent=?", (b,))
        card = self.hub.call("GET", "/v1/agents/" + b)[1]
        self.assertEqual(card["stale_queued"], 1)
        self.assertGreaterEqual(card["oldest_queued_s"], 290)
        att = self.hub.call("GET", "/v1/overview")[1]["attention"]
        self.assertIn(b, [x["name"] for x in att["stale"]])
        # delivered -> gone from the alarm
        items = self.pull(sb, tb)
        self.as_(tb, "POST", "/v1/sessions/%s/delivered" % sb, {"ids": [i["id"] for i in items]})
        att = self.hub.call("GET", "/v1/overview")[1]["attention"]
        self.assertNotIn(b, [x["name"] for x in att["stale"]])
        # a result waiting for review, and an unanswered question
        _, t = self.as_(ta, "POST", "/v1/tasks", {"to": b, "title": "review me"})
        self.as_(tb, "POST", "/v1/tasks/%d" % t["task"]["id"], {"kind": "result", "body": "r"})
        _, q = self.as_(ta, "POST", "/v1/tasks", {"to": b, "title": "ask me"})
        self.as_(tb, "POST", "/v1/tasks/%d" % q["task"]["id"], {"kind": "question", "body": "which?"})
        att = self.hub.call("GET", "/v1/overview")[1]["attention"]
        self.assertIn(t["task"]["id"], [x["id"] for x in att["review"]])
        qs = {x["id"]: x for x in att["questions"]}
        self.assertEqual((qs[q["task"]["id"]]["asked_by"], qs[q["task"]["id"]]["body"]), (b, "which?"))
        self.as_(ta, "POST", "/v1/tasks/%d" % q["task"]["id"], {"kind": "answer", "body": "this"})
        att = self.hub.call("GET", "/v1/overview")[1]["attention"]
        self.assertNotIn(q["task"]["id"], [x["id"] for x in att["questions"]])

    def test_overview(self):
        self.reg(project="ov")
        code, out = self.hub.call("GET", "/v1/overview")
        self.assertEqual(code, 200)
        self.assertIn("ov", [p["name"] for p in out["projects"]])


if __name__ == "__main__":
    unittest.main()

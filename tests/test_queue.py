"""The review queue: drafting, editing, validating, and the send-side refusals.

Every test here is written so that deleting the behaviour it covers makes it
fail. That is the bar ARCHITECTURE.md sets, and this module is where it matters
most: these are the guarantees between "the machine wrote something" and "a
person received it".

The send path is exercised without a network by faking `mailer.send` or by
stubbing `mailer.config`. config.json is never touched: the tests must not be
able to make a real send just by being run.
"""
from .harness import bootstrap  # noqa: E402

import db
import json
import latex_cv
import llm
import mailer
import unittest

from api import queue


def setUpModule():
    bootstrap()


def _stub_mail_config(**overrides):
    """Replace mailer.config() for one test. Returns the value to restore."""
    original = mailer.config
    base = original()

    def fake():
        merged = dict(base)
        merged.update(overrides)
        return merged

    mailer.config = fake
    return original


def _fake_transport(message_id="<t@cold-approach.local>"):
    """Swap mailer.send for one that reports acceptance. Returns the original."""
    original = mailer.send
    captured = {}

    def fake(to_addr, subject, body, **kw):
        captured.update(to=to_addr, subject=subject, body=body)
        return {"ok": True, "sent": True, "dry": False, "message_id": message_id,
                "error": None}

    mailer.send = fake
    return original, captured


class QueueFixture(unittest.TestCase):
    """A contact with a real-looking address, and a clean queue."""

    def setUp(self):
        self._clean()
        self.co = db.resolve_company("Jane Street")
        db.execute("INSERT INTO contacts (first_name,last_name,job_title,company_id,"
                   "company_name,email,email_source,source) VALUES "
                   "('Robin','Tester','Quantitative Researcher',?,'Jane Street',"
                   "'robin.tester@janestreet.com','hunter','test')", [self.co["id"]])
        self.cid = db.query("SELECT id FROM contacts WHERE source='test'")[0]["id"]

    def _clean(self):
        db.execute("DELETE FROM contact_touchpoints")
        db.execute("DELETE FROM mail_queue")
        # The suite shares ONE database, so a follow-up test that inserts an
        # `outreach` row and does not remove it leaves a mail in the log that
        # later modules then count - which showed up as "a refusal created an
        # outreach row" in a test about reconstructed addresses. Scoped to this
        # fixture's own contacts (and to contact-less rows, which only these
        # tests create) so it cannot eat another module's data.
        db.execute("DELETE FROM outreach WHERE contact_id IN "
                   "(SELECT id FROM contacts WHERE source='test') OR contact_id IS NULL")
        db.execute("DELETE FROM contacts WHERE source='test'")

    def tearDown(self):
        self._clean()

    def draft(self, **kw):
        """Queue a row without spending a token.

        The model is the only real drafter, so the LLM is stubbed here. The
        template path was removed on purpose - it produced identical generic text
        for everyone and made a working queue look broken.
        """
        tpl = db.query("SELECT id FROM templates WHERE active=1 ORDER BY id LIMIT 1")[0]
        r = self._draft_with({"contact_id": self.cid, "template_id": tpl["id"], **kw})
        self.assertNotIn("error", r, r.get("error"))
        return r["queue"]

    def _draft_with(self, payload):
        cfg, gen = llm.is_configured, llm.generate_mail
        llm.is_configured = lambda: True
        llm.generate_mail = lambda *a, **k: {
            "ok": True, "subject": "Is the desk hiring this year?",
            "body": "Hi Robin,\n\nYour work on the systematic book is why I'm writing.\n\n"
                    "Is the team hiring or planning to hire this year?\n\nBest,\nMe",
            "model": "test-model", "cached": False, "tokens": 7,
            "specifics": ["their systematic book", "the desk's hiring signal"]}
        try:
            r = queue.api_draft(payload)
        finally:
            llm.is_configured, llm.generate_mail = cfg, gen
        return r


class TestDrafting(QueueFixture):
    def test_a_draft_is_queued_and_sends_nothing(self):
        row = self.draft()
        self.assertEqual(row["status"], "pending")
        self.assertEqual(db.query("SELECT COUNT(*) n FROM mail_queue")[0]["n"], 1)
        self.assertEqual(db.query("SELECT COUNT(*) n FROM outreach")[0]["n"], 0)
        self.assertEqual(db.query("SELECT COUNT(*) n FROM contact_touchpoints")[0]["n"], 0)

    def test_the_draft_records_which_model_wrote_it(self):
        """`generation` is what lets a reply rate be split by author later
        instead of guessed at from timestamps."""
        self.assertEqual(self.draft()["generation"], "llm:test-model")

    def test_the_draft_saves_the_facts_it_was_built_on(self):
        """So 'why did it mention that' is answerable months later, and a claim
        in the mail can be checked against the row it claims to come from."""
        row = self.draft()
        saved = json.loads(db.query("SELECT specifics FROM mail_queue WHERE id=?",
                                    [row["id"]])[0]["specifics"])
        self.assertIn("their systematic book", saved)

    def test_a_draft_naming_no_facts_is_flagged(self):
        """The whole point of the exercise. A mail that could go to anyone gets
        deleted, and it should be caught while it is still only a draft."""
        cfg, gen = llm.is_configured, llm.generate_mail
        llm.is_configured = lambda: True
        llm.generate_mail = lambda *a, **k: {
            "ok": True, "subject": "Quick question", "body": "Hi Robin,\n\nQuick question?\n\nBest,\nMe",
            "model": "m", "cached": False, "tokens": 1, "specifics": []}
        try:
            r = queue.api_draft({"contact_id": self.cid})
        finally:
            llm.is_configured, llm.generate_mail = cfg, gen
        self.assertTrue(any("no specific facts" in f for f in r["flags"]), r["flags"])

    def test_drafting_is_refused_loudly_without_a_key(self):
        """The bug this replaced: with no key every draft came back an error, the
        UI swallowed it, and the button looked dead."""
        original = llm.is_configured
        llm.is_configured = lambda: False
        try:
            r = queue.api_draft({"contact_id": self.cid})
        finally:
            llm.is_configured = original
        self.assertTrue(r.get("needs_key"), r)
        self.assertIn("config.json", r["error"])
        self.assertEqual(db.query("SELECT COUNT(*) n FROM mail_queue")[0]["n"], 0)

    def test_the_refusal_is_useful_even_when_the_config_looks_fine(self):
        """With a key present but the endpoint unusable, `status()` has no
        `reason`, and the message used to end in a dangling space."""
        original_cfg, original_status = llm.is_configured, llm.status
        llm.is_configured = lambda: False
        llm.status = lambda: {"configured": False, "model": "m", "base_url": "http://x"}
        try:
            r = queue.api_draft({"contact_id": self.cid})
        finally:
            llm.is_configured, llm.status = original_cfg, original_status
        self.assertTrue(r["error"].rstrip().endswith(")"), r["error"])
        self.assertIn("--models", r["error"])

    def test_a_verified_address_is_marked_verified(self):
        self.assertEqual(self.draft()["addr_kind"], "verified")

    def test_a_reconstructed_address_is_marked_as_a_reconstruction(self):
        """Invariant 1. This word is what arms the preflight refusal."""
        db.execute("UPDATE contacts SET email_source='pattern' WHERE id=?", [self.cid])
        self.assertEqual(self.draft()["addr_kind"], "pattern")

    def test_no_address_means_no_mail(self):
        """Invariant 4: refuse rather than invent."""
        db.execute("UPDATE contacts SET email='', email_masked='' WHERE id=?", [self.cid])
        db.execute("UPDATE companies SET email_pattern=NULL WHERE id=?", [self.co["id"]])
        r = queue.api_draft({"contact_id": self.cid})
        self.assertIn("no address", r["error"])
        self.assertEqual(db.query("SELECT COUNT(*) n FROM mail_queue")[0]["n"], 0)

    def test_a_failed_llm_draft_queues_nothing(self):
        """An empty row would look like real work and get approved by accident."""
        original_cfg, original_gen = llm.is_configured, llm.generate_mail
        llm.is_configured = lambda: True
        llm.generate_mail = lambda *a, **k: {"ok": False, "error": "the model is down"}
        try:
            r = queue.api_draft({"contact_id": self.cid})
        finally:
            llm.is_configured, llm.generate_mail = original_cfg, original_gen
        self.assertIn("the model is down", r["error"])
class TestEditing(QueueFixture):
    def test_edits_are_kept_and_flagged(self):
        row = self.draft()
        r = queue.api_edit({"id": row["id"], "body": "My own words, entirely."})
        self.assertEqual(r["body"], "My own words, entirely.")
        self.assertEqual(r["edited"], 1)

    def test_a_sent_mail_can_no_longer_be_edited(self):
        """Otherwise editing notes on a thread would rewrite what was received."""
        row = self.draft()
        db.execute("UPDATE mail_queue SET status='sent' WHERE id=?", [row["id"]])
        self.assertIn("no longer be edited",
                      queue.api_edit({"id": row["id"], "body": "x"})["error"])

    def test_replacing_a_reconstructed_address_upgrades_its_trust(self):
        """Half the reason to edit the recipient is swapping a guess for a fact."""
        db.execute("UPDATE contacts SET email_source='pattern' WHERE id=?", [self.cid])
        row = self.draft()
        self.assertEqual(row["addr_kind"], "pattern")
        r = queue.api_edit({"id": row["id"], "to_addr": "robin.tester@js.com"})
        self.assertEqual(r["addr_kind"], "verified")


class TestCancellation(QueueFixture):
    def test_cancelling_deletes_the_row(self):
        """Cancelling discards the draft. Keeping it was justified by "why did I not
        write to this person", which the LLM cache answers for free: an identical
        prompt is never paid for twice, so a rejected draft is worth nothing."""
        row = self.draft()
        r = queue.api_cancel({"id": row["id"]})
        self.assertEqual(r["status"], "deleted")
        self.assertEqual(db.query("SELECT * FROM mail_queue WHERE id=?",
                                  [row["id"]]), [])

    def test_cancelling_twice_is_harmless(self):
        """The UI advances immediately, so a double click must not 500."""
        row = self.draft()
        queue.api_cancel({"id": row["id"]})
        self.assertIn("error", queue.api_cancel({"id": row["id"]}))

    def test_a_cancelled_draft_still_counts_as_not_contacted(self):
        """The ledger, not the queue, decides who has been reached."""
        row = self.draft()
        queue.api_cancel({"id": row["id"]})
        self.assertIn(self.cid, [r["id"] for r in queue.contacts_to_contact()])

    def test_a_sent_mail_is_never_deleted(self):
        """Cancel deletes the row. That has to stop at `sent`: it is the only
        record that this text actually left the machine."""
        row = self.draft()
        db.execute("UPDATE mail_queue SET status='sent' WHERE id=?", [row["id"]])
        self.assertIn("already sent", queue.api_cancel({"id": row["id"]})["error"])
        self.assertEqual(len(db.query("SELECT * FROM mail_queue WHERE id=?",
                                      [row["id"]])), 1)


class TestSendGate(QueueFixture):
    """The refusals. Each must stop the mail before any socket opens."""

    def setUp(self):
        super().setUp()
        self.restore = _stub_mail_config(send_mode="live", smtp_user="",
                                         smtp_password="", daily_cap=40)

    def tearDown(self):
        mailer.config = self.restore
        super().tearDown()

    def test_a_reconstructed_address_is_refused_until_confirmed(self):
        db.execute("UPDATE contacts SET email_source='pattern' WHERE id=?", [self.cid])
        row = self.draft()
        r = queue.api_validate({"id": row["id"]})
        self.assertFalse(r["ok"])
        self.assertTrue(any("reconstructed" in b for b in r["blocking"]), r["blocking"])
        self.assertEqual(db.query("SELECT COUNT(*) n FROM outreach")[0]["n"], 0)

    def test_confirming_the_reconstruction_clears_that_refusal(self):
        """The arming has to actually remove the block, not add a warning.

        The send still stops here - this fixture is live mode with no credentials,
        which is its own refusal, and is covered separately - so what is asserted
        is that 'reconstructed' is no longer among the blockers.
        """
        db.execute("UPDATE contacts SET email_source='pattern' WHERE id=?", [self.cid])
        row = self.draft()
        blocked = queue.api_validate({"id": row["id"]}).get("blocking", [])
        self.assertTrue(any("reconstructed" in b for b in blocked), blocked)
        again = queue.api_validate({"id": row["id"], "confirm_pattern": True})
        self.assertFalse(any("reconstructed" in b for b in again.get("blocking", [])),
                         again.get("blocking"))
        self.assertIn("credentials", again.get("error", ""))

    def test_live_mode_without_credentials_sends_nothing(self):
        """A configured switch is not a configured account."""
        row = self.draft()
        r = queue.api_validate({"id": row["id"], "confirm_pattern": True})
        self.assertFalse(r["ok"])
        self.assertIn("credentials", r["error"])
        self.assertEqual(db.query("SELECT COUNT(*) n FROM contact_touchpoints")[0]["n"], 0)

    def test_a_refusal_creates_no_outreach_row(self):
        """The mail log must never contain a mail that was not sent."""
        db.execute("UPDATE contacts SET email_source='pattern' WHERE id=?", [self.cid])
        queue.api_validate({"id": self.draft()["id"]})
        self.assertEqual(db.query("SELECT COUNT(*) n FROM outreach")[0]["n"], 0)

    def test_an_unresolved_placeholder_blocks_the_send(self):
        row = self.draft()
        queue.api_edit({"id": row["id"], "body": "Hi {{first_name}}, is now hiring?"})
        r = queue.api_validate({"id": row["id"], "confirm_pattern": True})
        self.assertFalse(r["ok"])
        self.assertTrue(any("placeholder" in b for b in r["blocking"]), r["blocking"])

    def test_the_daily_cap_stops_the_run(self):
        for _ in range(40):
            db.execute("INSERT INTO contact_touchpoints (contact_id,sent_at) "
                       "VALUES (?, date('now'))", [self.cid])
        r = queue.api_validate({"id": self.draft()["id"], "confirm_pattern": True})
        self.assertFalse(r["ok"])
        self.assertTrue(any("daily cap" in b for b in r["blocking"]), r["blocking"])

    def test_a_failed_send_keeps_the_draft(self):
        """So the work is not lost to a transient SMTP error."""
        row = self.draft()
        original = mailer.send
        mailer.send = lambda *a, **k: {"ok": False, "sent": False, "error": "SMTP down"}
        try:
            r = queue.api_validate({"id": row["id"], "confirm_pattern": True})
        finally:
            mailer.send = original
        self.assertFalse(r["ok"])
        self.assertEqual(db.query("SELECT status FROM mail_queue WHERE id=?",
                                  [row["id"]])[0]["status"], "failed")
        self.assertEqual(db.query("SELECT COUNT(*) n FROM contact_touchpoints")[0]["n"], 0)
class TestTheLedger(QueueFixture):
    """`contact_touchpoints` is the memory. These tests are why it exists."""

    def send_it(self, addr_kind="verified"):
        """Validate a draft with the transport faked as accepting."""
        row = self.draft()
        if addr_kind != "verified":
            db.execute("UPDATE mail_queue SET addr_kind=? WHERE id=?",
                       [addr_kind, row["id"]])
        original, captured = _fake_transport()
        try:
            r = queue.api_validate({"id": row["id"], "confirm_pattern": True})
        finally:
            mailer.send = original
        return row, r, captured

    def test_a_real_send_appends_one_touchpoint(self):
        _row, r, _cap = self.send_it()
        self.assertTrue(r["sent"], r)
        self.assertEqual(db.query("SELECT COUNT(*) n FROM contact_touchpoints")[0]["n"], 1)

    def test_the_touchpoint_records_when_and_to_whom(self):
        row, _r, _cap = self.send_it()
        t = db.query("SELECT * FROM contact_touchpoints")[0]
        self.assertTrue(t["sent_at"])
        self.assertEqual(t["to_addr"], "robin.tester@janestreet.com")
        self.assertEqual(t["contact_id"], self.cid)
        self.assertEqual(t["queue_id"], row["id"])
        self.assertEqual(t["direction"], "outbound")

    def test_the_touchpoint_remembers_how_trustworthy_the_address_was(self):
        """Six months later, 'pattern' is the only warning left."""
        self.send_it(addr_kind="pattern")
        self.assertEqual(
            db.query("SELECT addr_kind FROM contact_touchpoints")[0]["addr_kind"],
            "pattern")

    def test_a_dry_run_records_nothing(self):
        """The honesty test. If this fails, 'who is left' starts lying."""
        row = self.draft()
        restore = _stub_mail_config(send_mode="dry")
        try:
            r = queue.api_validate({"id": row["id"], "confirm_pattern": True})
        finally:
            mailer.config = restore
        self.assertTrue(r["ok"])
        self.assertFalse(r["sent"])
        self.assertEqual(db.query("SELECT COUNT(*) n FROM contact_touchpoints")[0]["n"], 0)

    def test_a_contacted_person_leaves_the_remaining_list(self):
        self.send_it()
        self.assertNotIn(self.cid, [r["id"] for r in queue.contacts_to_contact()])

    def test_editing_the_mail_log_cannot_erase_the_contact(self):
        """The exact failure the ledger exists to prevent: notes being edited on
        the outreach row must not bring a contacted person back into the list."""
        self.send_it()
        db.execute("UPDATE outreach SET status='draft', notes='cleared by mistake'")
        self.assertNotIn(self.cid, [r["id"] for r in queue.contacts_to_contact()])

    def test_the_same_person_is_never_offered_twice(self):
        self.send_it()
        self.draft()
        todo = queue.api_todo({})
        self.assertNotIn(self.cid, [r["id"] for r in todo["remaining"]])
        self.assertEqual(todo["n_contacted"], 1)

    def test_the_history_view_shows_the_contact(self):
        self.send_it()
        h = queue.api_history({})
        self.assertEqual(len(h), 1)
        self.assertEqual(h[0]["last_name"], "Tester")


class TestSendSideEffects(QueueFixture):
    def test_sending_marks_the_queue_and_the_mail_log(self):
        row = self.draft()
        original, _captured = _fake_transport()
        try:
            r = queue.api_validate({"id": row["id"], "confirm_pattern": True})
        finally:
            mailer.send = original
        self.assertTrue(r["sent"])
        self.assertEqual(db.query("SELECT status FROM mail_queue WHERE id=?",
                                  [row["id"]])[0]["status"], "sent")
        o = db.query("SELECT * FROM outreach WHERE id=?", [r["outreach_id"]])[0]
        self.assertEqual(o["status"], "sent")
        self.assertTrue(o["sent_at"])
        self.assertTrue(o["next_followup_at"])

    def test_sending_the_same_row_twice_is_refused(self):
        row = self.draft()
        original, _captured = _fake_transport()
        try:
            queue.api_validate({"id": row["id"], "confirm_pattern": True})
            again = queue.api_validate({"id": row["id"], "confirm_pattern": True})
        finally:
            mailer.send = original
        self.assertIn("already been sent", again["error"])
        self.assertEqual(db.query("SELECT COUNT(*) n FROM contact_touchpoints")[0]["n"], 1)

    def test_the_edit_sent_with_the_approval_is_what_goes_out(self):
        """You fixed a sentence and pressed validate; that sentence must be sent."""
        row = self.draft()
        original, captured = _fake_transport()
        try:
            queue.api_validate({"id": row["id"], "confirm_pattern": True,
                                "body": "Corrected opening line."})
        finally:
            mailer.send = original
        self.assertEqual(captured["body"], "Corrected opening line.")


class TestNoAutomaticSending(QueueFixture):
    def test_the_send_is_reachable_from_exactly_one_function(self):
        """Structural: `mailer.send` is called from one place, and that place is
        the explicit per-row validate. If someone later adds a batch route that
        calls it directly, this is the test that was supposed to stop them."""
        import inspect
        callers = sorted(name for name, fn in inspect.getmembers(queue, inspect.isfunction)
                         if "mailer.send(" in inspect.getsource(fn))
        self.assertEqual(callers, ["api_validate"],
                         f"mailer.send is reachable from {callers}, not only api_validate")

    def test_no_route_sends_a_batch(self):
        """A queue that can be emptied in one click is a queue that will be."""
        import api
        for _method, pattern, _handler in api.ROUTES:
            for word in ("send-all", "sendall", "validate-all", "blast"):
                self.assertNotIn(word, pattern.lower(),
                                 f"{pattern} would defeat the review step")


class TestThePromptCarriesThePerson(unittest.TestCase):
    """The prompt is the product. These tests pin what it is allowed to forget.

    The first version of this prompt was a polite generic instruction and the facts
    block omitted two things the database had been holding all along: the person's
    career history and the firm's live hiring signal. A draft written from that is
    generic by construction, so each of these is a regression guard on a specific
    omission rather than a general "the prompt is not empty" check.
    """

    def setUp(self):
        bootstrap()
        db.execute("DELETE FROM mail_queue")
        db.execute("DELETE FROM contacts WHERE source='prompttest'")
        self.co = db.resolve_company("Jane Street")
        db.execute("INSERT INTO contacts (first_name,last_name,job_title,position_raw,"
                   "headline,department,company_id,company_name,email,email_source,source)"
                   " VALUES ('Robin','Tester','Quantitative Researcher',"
                   "'Quantitative Researcher','Quant at Jane Street','Research',?,"
                   "'Jane Street','robin.tester@janestreet.com','hunter','prompttest')",
                   [self.co["id"]])
        self.cid = db.query("SELECT id FROM contacts WHERE source='prompttest'")[0]["id"]
        db.execute("INSERT INTO person_job_history (contact_id,title,company_name,"
                   "start_year,end_year,is_current) VALUES (?,'Portfolio Manager',"
                   "'Dana Petroleum',2019,2021,0)", [self.cid])
        self.contact = dict(db.query("SELECT * FROM contacts WHERE id=?", [self.cid])[0])
        self.company = dict(db.query("SELECT * FROM companies WHERE id=?",
                                     [self.co["id"]])[0])

    def tearDown(self):
        db.execute("DELETE FROM person_job_history WHERE contact_id=?", [self.cid])
        db.execute("DELETE FROM contacts WHERE source='prompttest'")

    def test_the_prompt_carries_the_career_history(self):
        """'You moved from energy to systematic equities' is the one thing that
        makes a mail about ONE person instead of a firm."""
        prompt = self._real_prompt()
        self.assertIn("Dana Petroleum", prompt)
        self.assertIn("Portfolio Manager", prompt)

    def test_the_prompt_carries_the_department_and_headline(self):
        prompt = self._real_prompt()
        self.assertIn("Research", prompt)
        self.assertIn("Quant at Jane Street", prompt)

    def test_the_prompt_carries_the_live_hiring_signal(self):
        """A firm with N open roles is actively hiring, and saying so plainly is
        the strongest non-flattery opening there is.

        Re-read from the database rather than trusting the dict the caller passed:
        a stale `company` argument must not be able to hide a hiring signal that
        was harvested minutes ago.
        """
        db.execute("UPDATE companies SET job_postings_count=42 WHERE id=?", [self.co["id"]])
        fresh = dict(db.query("SELECT * FROM companies WHERE id=?", [self.co["id"]])[0])
        prompt = self._real_prompt(company=fresh)
        self.assertIn("42 open roles", prompt)

    def test_the_hiring_signal_is_labelled_firm_wide(self):
        """"1414 open roles" said to a Goldman PM counts the whole bank, not their
        desk, and reads as if you scrolled their careers page. The count is worth
        keeping; the misdirection is not."""
        db.execute("UPDATE companies SET job_postings_count=1414 WHERE id=?",
                   [self.co["id"]])
        fresh = dict(db.query("SELECT * FROM companies WHERE id=?", [self.co["id"]])[0])
        prompt = self._real_prompt(company=fresh)
        self.assertIn("NOT their desk", prompt)

    def test_the_prompt_never_claims_a_firms_tech_stack(self):
        """`technologies` is CDN and ad-vendor noise scraped from a careers page:
        'Akamai Bot Manager, Algolia, Google Ads'. Telling a Goldman PM they run
        Redux is absurd and instantly discreditsing, so it must never reach the
        prompt even when it is on the row."""
        db.execute("UPDATE companies SET technologies='Redux,Algolia,Google Ads' "
                   "WHERE id=?", [self.co["id"]])
        fresh = dict(db.query("SELECT * FROM companies WHERE id=?", [self.co["id"]])[0])
        prompt = self._real_prompt(company=fresh)
        self.assertNotIn("Redux", prompt)
        self.assertNotIn("Algolia", prompt)

    def test_the_prompt_demands_the_hiring_question(self):
        """The user's own wording: 'do you know if your team ... is looking for
        someone with this background' - a referral-shaped ask, not 'are you
        hiring', which is the harder yes to get."""
        prompt = self._real_prompt()
        self.assertIn("currently looking for someone with this background", prompt)
        self.assertIn("10-15 minutes", prompt)
        self.assertIn("work around their schedule", prompt)

    def test_the_prompt_forbids_markdown(self):
        """The source template was written with **bold**; a mail is plain text and
        literal asterisks read as broken formatting."""
        self.assertIn("no markdown", llm.SYSTEM_PROMPT.lower())

    def test_the_prompt_forbids_inventing(self):
        prompt = self._real_prompt()
        self.assertIn("Never invent", llm.SYSTEM_PROMPT)
        self.assertIn("Do not invent", prompt)

    def test_an_unknown_fact_says_so_rather_than_disappearing(self):
        """Silently omitting a missing field is what invites the model to fill it."""
        prompt = self._real_prompt()
        self.assertIn("(unknown)", prompt)

    def _real_prompt(self, company=None):
        """Run the real generator with only the network call stubbed out."""
        saved = llm.chat
        captured = {}
        llm.chat = lambda prompt, **k: captured.update(prompt=prompt) or {
            "ok": False, "error": "not called for real"}
        try:
            llm.generate_mail(self.contact, company or self.company,
                              {"full_name": "Me Tester", "headline": "Quant dev"})
        finally:
            llm.chat = saved
        return captured["prompt"]


class TestPickingWhoToWriteTo(QueueFixture):
    """The picker's filters. These are the whole of 'who to write to'.

    Each answers a question the user actually asks - who is in Paris, who is a
    hedge fund, who have I not written to yet - and each is wrong in an obvious way
    if it silently ignores its argument, which is how a filter that "works" but
    returns 467 rows happens.
    """

    def _p(self, **kw):
        return queue.api_todo({k: [v] for k, v in kw.items()})

    def test_the_city_filter_is_exact_not_substring(self):
        """'London' must not drag in 'London Bridge', and 'london' must not be a
        different city."""
        db.execute("INSERT INTO contacts (first_name,last_name,city,email,email_source,"
                   "source) VALUES ('Sue','Test','London','sue@londonbridge.co',"
                   "'hunter','test')")
        db.execute("INSERT INTO contacts (first_name,last_name,city,email,email_source,"
                   "source) VALUES ('Ann','Other','London Bridge','ann@x.co',"
                   "'hunter','test')")
        cities = {r["city"] for r in self._p(city="London", limit="500")["remaining"]}
        self.assertIn("London", cities)
        self.assertNotIn("London Bridge", cities)

    def test_the_city_filter_ignores_case(self):
        for row in self._p(city="london", limit="500")["remaining"]:
            self.assertEqual(row["city"].lower(), "london")

    def test_the_firm_type_filter_works(self):
        for row in self._p(type="prop_hft", limit="500")["remaining"]:
            self.assertEqual(row["firm_type"], "prop_hft")

    def test_the_search_covers_name_firm_and_address(self):
        self.assertTrue(self._p(search="tester", limit="500")["remaining"])
        self.assertTrue(self._p(search="robin.tester", limit="500")["remaining"])

    def test_hiding_pending_drops_people_who_already_have_a_draft(self):
        """Otherwise the same person gets drafted twice, and the second draft is
        just a rewrite of the first."""
        before = {r["id"] for r in self._p()["remaining"]}
        self.draft()
        after = {r["id"] for r in self._p(no_pending="1")["remaining"]}
        self.assertIn(self.cid, before)
        self.assertNotIn(self.cid, after)

    def test_the_facets_are_offered(self):
        """The city/type/desk lists are built from who is still reachable, so a
        filter never offers a city that only holds people already written to."""
        r = self._p()
        for key in ("cities", "types", "desks"):
            self.assertIn(key, r)

    def test_n_remaining_is_the_total_not_the_page(self):
        """It is shown as "to go", so it must ignore the page limit. Reporting
        `len(rows)` made a city with 45 people left read as "0 to go"."""
        everything = self._p()["n_remaining"]
        page = self._p(limit="1")
        self.assertEqual(page["n_remaining"], everything)
        self.assertEqual(page["n_shown"], min(1, everything))

    def test_a_bare_string_filter_is_not_read_as_its_first_character(self):
        """`(params.get(k) or [""])[0]` turned {'city': 'London'} into 'L' - a
        one-letter filter that matched nothing and looked like an empty queue."""
        # The fixture's contact has no city, so give it one: otherwise both forms
        # agree on zero and the equality below proves nothing.
        db.execute("UPDATE contacts SET city='London' WHERE id=?", [self.cid])
        as_list = queue.api_todo({"city": ["London"]})["n_remaining"]
        as_string = queue.api_todo({"city": "London"})["n_remaining"]
        self.assertEqual(as_list, as_string)
        self.assertGreaterEqual(as_list, 1)
        self.assertIn(self.cid,
                      [r["id"] for r in queue.api_todo({"city": ["London"]})["remaining"]])


class TestFollowUpsGoThroughTheQueue(QueueFixture):
    """A follow-up used to bypass review entirely, which is the one mail that must
    never be sent unread."""

    def _sent(self, subject="Quick question about the Quant team"):
        return db.execute(
            "INSERT INTO outreach (contact_id,subject,body,status,sent_at) "
            "VALUES (?,?,?,'sent','2026-01-01T09:00:00')",
            [self.cid, subject, "Hi Robin, ...\n\nBest"])

    def test_a_followup_arrives_as_a_pending_draft(self):
        r = queue.api_queue_followup({"outreach_id": self._sent()})
        self.assertIn("queue", r, r.get("error"))
        self.assertEqual(r["queue"]["status"], "pending")
        self.assertEqual(r["queue"]["contact_id"], self.cid)

    def test_a_followup_threads_its_subject(self):
        """Keeping the subject is most of why a bump gets read at all."""
        r = queue.api_queue_followup({"outreach_id": self._sent()})
        self.assertEqual(r["queue"]["subject"], "Re: Quick question about the Quant team")

    def test_a_followup_does_not_double_prefix(self):
        r = queue.api_queue_followup(
            {"outreach_id": self._sent("Re: already threaded")})
        self.assertEqual(r["queue"]["subject"], "Re: already threaded")

    def test_a_followup_is_recorded_as_template_not_llm(self):
        """Otherwise `generation` implies the model wrote it, and the reply-rate
        comparison between model and template quietly breaks."""
        r = queue.api_queue_followup({"outreach_id": self._sent()})
        self.assertTrue(r["queue"]["generation"].startswith("template:"))

    def test_a_followup_to_a_mail_with_no_contact_refuses(self):
        """The row can exist with a NULL contact (it is allowed to), and the bump
        then has nobody to address."""
        oid = db.execute("INSERT INTO outreach (contact_id,subject,body,status) "
                         "VALUES (NULL,'s','b','sent')")
        self.assertIn("no contact", queue.api_queue_followup({"outreach_id": oid})["error"])

    def test_a_followup_to_a_missing_row_refuses(self):
        self.assertIn("error", queue.api_queue_followup({"outreach_id": 999999}))


class TestTheQueueWalksItself(QueueFixture):
    """After a decision the next draft must be the next thing on screen."""

    def _draft_for(self, first, last):
        db.execute("INSERT INTO contacts (first_name,last_name,company_id,company_name,"
                   "email,email_source,source) VALUES (?,?,?,?,?,'hunter','test')",
                   [first, last, self.co["id"], "Jane Street",
                    f"{first.lower()}@janestreet.com"])
        cid = db.query("SELECT id FROM contacts WHERE source='test' AND first_name=?",
                       [first])[0]["id"]
        return self._draft_with({"contact_id": cid})["queue"]

    def _pending(self):
        return len(queue.api_queue({"status": ["pending"]}))

    def test_cancelling_shortens_the_pending_list(self):
        a = self._draft_for("Ann", "One")
        self._draft_for("Bob", "Two")
        self.assertEqual(self._pending(), 2)
        queue.api_cancel({"id": a["id"]})
        self.assertEqual(self._pending(), 1)

    def test_sending_shortens_the_pending_list(self):
        a = self._draft_for("Cid", "Three")
        self._draft_for("Dee", "Four")
        original = mailer.send
        mailer.send = lambda *x, **k: {"ok": True, "sent": True, "dry": False,
                                       "message_id": "<t@x>", "error": None}
        try:
            queue.api_validate({"id": a["id"], "confirm_pattern": True})
        finally:
            mailer.send = original
        self.assertEqual(self._pending(), 1)

    def test_skipping_decides_nothing(self):
        """Skip moves the cursor. A skip that cancelled would quietly drop people
        out of the queue by accident."""
        a = self._draft_for("Eve", "Five")
        queue.api_cancel({"id": a["id"]})
        b = self._draft_for("Fay", "Six")
        self.assertEqual(
            db.query("SELECT status FROM mail_queue WHERE id=?", [b["id"]])[0]["status"],
            "pending")


class TestTheSignature(QueueFixture):
    """The sign-off is written by the code, never the model.

    A model asked for a signature invents a phone number or a profile URL, and a
    wrong one in an outbound mail is a real problem for a real person. The
    ordering follows the user's own template: name, LinkedIn, phone.
    """

    PROFILE = {"full_name": "Badre Mhiouah",
               "linkedin": "https://linkedin.com/in/badre",
               "phone": "+33 6 11 68 23 67"}

    def test_the_signature_is_name_then_linkedin_then_phone(self):
        sig = llm.signature(self.PROFILE)
        self.assertEqual(sig.split("\n"),
                         ["Badre Mhiouah",
                          "https://linkedin.com/in/badre",
                          "+33 6 11 68 23 67"])

    def test_a_missing_field_is_simply_absent(self):
        """No blank lines, no 'None', no invented placeholder."""
        sig = llm.signature({"full_name": "Badre Mhiouah", "phone": "+33 6"})
        self.assertEqual(sig.split("\n"), ["Badre Mhiouah", "+33 6"])

    def test_a_model_that_signs_itself_does_not_get_a_second_signature(self):
        """The double-signature bug: models write the name, then the phone, so a
        `endswith(name)` test failed and the name appeared twice."""
        saved = llm.chat
        llm.chat = lambda *a, **k: {
            "ok": True, "subject": "s",
            "body": "Hi Robin,\n\nThank you.\n\nBest,\nBadre Mhiouah\n"
                    "https://linkedin.com/in/badre\n+33 6 11 68 23 67",
            "model": "m", "cached": False, "tokens": 1, "specifics": ["x"]}
        try:
            out = llm.generate_mail({}, {}, self.PROFILE)
        finally:
            llm.chat = saved
        self.assertEqual(out["body"].lower().count("badre mhiouah"), 1)
        self.assertEqual(out["body"].count("+33 6 11 68 23 67"), 1)

    def test_only_the_missing_lines_are_appended(self):
        saved = llm.chat
        llm.chat = lambda *a, **k: {
            "ok": True, "subject": "s",
            "body": "Hi Robin,\n\nThank you.\n\nBest,\nBadre Mhiouah",
            "model": "m", "cached": False, "tokens": 1, "specifics": ["x"]}
        try:
            out = llm.generate_mail({}, {}, self.PROFILE)
        finally:
            llm.chat = saved
        self.assertEqual(out["body"].lower().count("badre mhiouah"), 1)
        self.assertIn("linkedin.com/in/badre", out["body"])
        self.assertIn("+33 6 11 68 23 67", out["body"])


class TestChoosingTheModel(QueueFixture):
    """The model is chosen per batch, and recorded, so two models can be compared.

    Inherits QueueFixture because `test_draft_records_which_model_wrote_it` needs
    a real contact: `api_draft` refuses a missing one, which is correct and would
    otherwise have made this test assert nothing.
    """

    def test_a_per_call_model_overrides_the_configured_one(self):
        saved_cfg, saved_net = llm.config, llm.net.post_json
        llm.config = lambda: {**saved_cfg(), "api_key": "k", "base_url": "http://x/v1",
                              "model": "default-model", "temperature": 0.7,
                              "max_tokens": 500}
        sent = {}

        def fake_post(url, body, **kw):
            sent.update(body)
            return {"ok": True, "status": 200, "body": b"{}", "text": "{}", "headers": {},
                    "json": {"usage": {"prompt_tokens": 5},
                             "choices": [{"finish_reason": "stop",
                                          "message": {"content":
                                                      '{"subject":"a","body":"b"}'}}]},
                    "error": None}
        llm.net.post_json = fake_post
        try:
            r = llm.chat("p", model="other-model")
        finally:
            llm.config, llm.net.post_json = saved_cfg, saved_net
        self.assertEqual(sent["model"], "other-model")
        self.assertEqual(r["model"], "other-model")

    def test_the_model_is_part_of_the_cache_key(self):
        """Otherwise asking the same person with a second model would silently
        replay the first model's draft, which is the opposite of what picking a
        model is for."""
        a = llm._request_key("model-a", "sys", "prompt", 0.7)
        b = llm._request_key("model-b", "sys", "prompt", 0.7)
        self.assertNotEqual(a, b)

    def test_draft_records_which_model_wrote_it(self):
        saved = llm.is_configured, llm.generate_mail
        llm.is_configured = lambda: True
        llm.generate_mail = lambda *a, **k: {
            "ok": True, "subject": "s", "body": "b", "model": "chosen-model",
            "cached": False, "tokens": 1, "specifics": ["x"]}
        try:
            r = queue.api_draft({"contact_id": self.cid, "model": "chosen-model"})
        finally:
            llm.is_configured, llm.generate_mail = saved
        self.assertIn("queue", r, r.get("error"))
        self.assertEqual(r["queue"]["generation"], "llm:chosen-model")
        self.assertEqual(r["queue"]["model"], "chosen-model")


class TestFailuresExplainThemselves(unittest.TestCase):
    """A failing model call must say why. These are the messages that replaced a
    dead button: the Queue simply stopped working and read as a broken app."""

    def test_an_expired_aws_signature_is_named_as_such(self):
        """The real failure mode on a Bedrock key: every model 401s at once and
        the body blames entitlement, which sends you down the wrong path."""
        msg = llm._explain_http(
            "HTTP 401: Signature expired: 20261002T035705Z is now earlier than "
            "20261004T141148Z (20261004T141648Z - 5 min.)", "openai.gpt-oss-120b")
        self.assertIn("expired on 2026-10-02", msg.replace("20261002", "2026-10-02"))
        self.assertIn("time-limited", msg)
        self.assertIn("config.json", msg)

    def test_the_date_is_read_from_a_lowercased_string(self):
        """`low` has already turned T and Z lowercase, so a `\\d+T\\d+Z` pattern
        can never fire. This is what printed 'expired on some time ago'."""
        msg = llm._explain_http(
            "HTTP 401: signature expired: 20261002t035705z is now earlier", "m")
        self.assertIn("20261002", msg)
        self.assertNotIn("some time ago", msg)

    def test_an_unsupported_api_is_named(self):
        self.assertIn("does not speak /chat/completions",
                      llm._explain_http(
                          "HTTP 400: The model 'anthropic.claude-sonnet-5' does not "
                          "support the '/v1/chat/completions' API", "anthropic.claude-sonnet-5"))

    def test_an_unentitled_model_is_distinguished_from_a_bad_key(self):
        self.assertIn("not entitled", llm._explain_http("HTTP 401", "openai.gpt-6-sol"))

    def test_an_unknown_error_is_not_invented(self):
        self.assertEqual(llm._explain_http("HTTP 418 teapot", "m"), "HTTP 418 teapot")
        self.assertEqual(llm._explain_http(None, "m"), "the request failed")

    def test_status_flags_a_recent_failure(self):
        """So the Queue can say 'drafting is broken' instead of doing nothing."""
        saved_cfg = llm.config
        llm.config = lambda: {**saved_cfg(), "api_key": "k"}
        db.execute("DELETE FROM fetch_log WHERE provider='llm'")
        try:
            self.assertFalse(llm.status().get("failing"))
            db.execute("INSERT INTO fetch_log (provider,endpoint,request_key,ok,error,"
                       "fetched_at) VALUES ('llm','chat','old',0,'boom',"
                       "'2020-01-01 00:00:00')")
            db.execute("INSERT INTO fetch_log (provider,endpoint,request_key,ok,error,"
                       "fetched_at) VALUES ('llm','chat','good',1,'','2021-01-01 00:00:00')")
            self.assertFalse(llm.status().get("failing"), "a later success clears it")
            db.execute("INSERT INTO fetch_log (provider,endpoint,request_key,ok,error,"
                       "fetched_at) VALUES ('llm','chat','new',0,'Signature expired: "
                       "20261002T035705Z','2026-01-01 00:00:00')")
            st = llm.status()
            self.assertTrue(st.get("failing"))
            self.assertIn("expired", st["last_error"])
        finally:
            db.execute("DELETE FROM fetch_log WHERE provider='llm'")
            llm.config = saved_cfg


class TestModelShortlist(unittest.TestCase):
    """The dropdown is five models, chosen for writing rather than benchmarks."""

    SERVED = [
        "anthropic.claude-sonnet-5", "xai.grok-4.3", "google.gemma-4-31b",
        "writer.palmyra-vision-7b", "openai.gpt-6-sol", "openai.gpt-5.6-terra",
        "openai.gpt-oss-120b", "moonshotai.kimi-k2.5",
        "mistral.mistral-large-3-675b-instruct", "deepseek.v3.2", "zai.glm-5",
        "qwen.qwen3-235b-a22b-2507", "google.gemma-3-4b-it",
        "mistral.ministral-3-3b-instruct"]

    def _models(self, current="openai.gpt-oss-120b"):
        saved = llm.available_models, llm.config
        llm.available_models = lambda: list(self.SERVED)
        llm.config = lambda: {**saved[1](), "model": current}
        try:
            return queue.api_models()
        finally:
            llm.available_models, llm.config = saved

    def test_no_more_than_five_are_offered(self):
        self.assertLessEqual(len(self._models()["models"]), 5)

    def test_the_configured_model_is_always_reachable(self):
        """Dropping the model the user last chose would look like the app retired it."""
        for m in ("deepseek.v3.2", "moonshotai.kimi-k2.5", "openai.gpt-oss-120b"):
            self.assertIn(m, self._models(m)["models"])

    def test_models_the_endpoint_cannot_serve_are_never_offered(self):
        for m in self._models()["models"]:
            self.assertFalse(m.startswith(("anthropic.", "xai.", "openai.gpt-5",
                                           "openai.gpt-6", "google.gemma-4", "writer.")), m)

    def test_everything_offered_is_something_the_endpoint_serves(self):
        """A name that is not on /models would fail the moment it was used."""
        served = set(self.SERVED)
        for m in self._models()["models"]:
            self.assertIn(m, served, f"{m} is offered but the endpoint does not serve it")

    def test_the_offered_list_is_the_shortlist_plus_the_configured_model(self):
        """The exact contract: five slots, filled from the prose shortlist, and
        the model the user last chose is never dropped in favour of it."""
        for current in ("openai.gpt-oss-120b", "deepseek.v3.2", "moonshotai.kimi-k2.5"):
            r = self._models(current)
            self.assertLessEqual(len(r["models"]), 5)
            self.assertIn(current, r["models"])
            for m in r["models"]:
                self.assertTrue(
                    m in queue.TOP_MODELS_FOR_PROSE or m == current,
                    f"{m} is neither shortlisted nor the configured model")

    def test_a_shortlisted_model_the_endpoint_lacks_is_simply_not_offered(self):
        """The list is a preference, not a promise: if a model is renamed or
        withdrawn it must disappear rather than fail on selection."""
        saved = llm.available_models, llm.config
        without = [m for m in self.SERVED if m != "zai.glm-5"]
        llm.available_models = lambda: list(without)
        llm.config = lambda: {**saved[1](), "model": "openai.gpt-oss-120b"}
        try:
            self.assertNotIn("zai.glm-5", queue.api_models()["models"])
        finally:
            llm.available_models, llm.config = saved


class TestTheLlmClient(unittest.TestCase):
    """The two ways this endpoint fails in ways a generic client does not.

    Both were real: `openai.gpt-oss-120b` spent an entire 700-token budget
    reasoning and returned `content: None`, which read as "the model is broken"
    rather than "give it more room". And `fetch_log` has no `prompt_tokens`
    column, so asking for one took the whole Queue status endpoint down.
    """

    def test_an_empty_reply_says_the_budget_was_too_small(self):
        saved_cfg = llm.config
        saved_net = llm.net.post_json
        llm.config = lambda: {**saved_cfg(), "api_key": "k", "base_url": "http://x/v1",
                              "model": "m", "temperature": 0.7, "max_tokens": 700}

        def fake_post(url, body, **kw):
            return {"ok": True, "status": 200, "body": b"{}", "text": "{}", "headers": {},
                    "json": {"usage": {"prompt_tokens": 10},
                             "choices": [{"finish_reason": "length",
                                          "message": {"content": None,
                                                      "reasoning": "thinking..."}}]},
                    "error": None}
        llm.net.post_json = fake_post
        try:
            r = llm.chat("anything")
        finally:
            llm.config, llm.net.post_json = saved_cfg, saved_net
        self.assertFalse(r["ok"])
        self.assertIn("max_tokens", r["error"])

    def test_status_does_not_ask_for_a_column_that_does_not_exist(self):
        """The ledger stores token cost in `credits`; `prompt_tokens` is not a column."""
        saved = llm.config
        llm.config = lambda: {**saved(), "api_key": "k"}
        try:
            st = llm.status()
        finally:
            llm.config = saved
        self.assertTrue(st["configured"])
        self.assertIsInstance(st["prompt_tokens"], int)
        self.assertIn("max_tokens", st)

    def test_unconfigured_is_a_result_not_an_exception(self):
        saved = llm.config
        llm.config = lambda: {**saved(), "api_key": ""}
        try:
            r = llm.chat("anything")
        finally:
            llm.config = saved
        self.assertFalse(r["ok"])
        self.assertIn("API key", r["error"])


class TestLatexConversion(unittest.TestCase):
    """The CV reaches a recipient as written, so the converter may not eat words.

    Every case below is a real bug this file had: nested braces silently deleted
    the opening of a bullet, a multi-argument macro glued a role onto its dates,
    and the preamble leaked into the text the model is asked to read.
    """

    def test_nested_braces_do_not_eat_the_words_before_them(self):
        """`\\resumeItem{Partnered with \\textbf{stakeholders} to identify...}`.
        A non-greedy `\\{(.*?)\\}` stops at the first `}` and loses the opening."""
        src = (r"\begin{document}\section{Experience}"
               r"\resumeItem{Partnered with \textbf{stakeholders} to find risk.}"
               r"\end{document}")
        out = latex_cv.latex_to_text(src)
        self.assertIn("Partnered with stakeholders to find risk.", out)

    def test_a_role_is_not_glued_to_its_dates(self):
        """`\\resumeSubheading` takes four braced arguments."""
        src = (r"\begin{document}\section{Experience}\resumeSubheading"
               r"{Barclays : Trader intern}{Aug.2023 -- Jan. 2024}"
               r"{Cross-asset derivatives}{Paris \& London}\end{document}")
        out = latex_cv.latex_to_text(src)
        self.assertIn("Barclays : Trader intern", out)
        self.assertNotIn("Trader internAug", out)
        self.assertIn("Aug.2023 -- Jan. 2024", out)

    def test_the_preamble_never_reaches_the_output(self):
        src = (r"\usepackage{tabularx}\newcommand{\skillgroup}[2]{\textbf{#1}: #2}"
               r"\begin{document}\section{Skills}\end{document}")
        out = latex_cv.latex_to_text(src)
        self.assertIn("Skills", out)
        for junk in ("usepackage", "newcommand", "tabularx", "#1"):
            self.assertNotIn(junk, out)

    def test_a_commented_out_example_is_ignored(self):
        """The resume template ships one, and it is a third of the file."""
        src = ("% \\textbf{Sourabh Bajaj} & Email : sourabh@sourabhbajaj.com\n"
               "\\begin{document}\n\\section{Experience}\n"
               "\\resumeItem{Real work.}\n\\end{document}")
        out = latex_cv.latex_to_text(src)
        self.assertIn("Real work.", out)
        self.assertNotIn("Sourabh", out)

    def test_links_keep_their_visible_text(self):
        src = (r"\begin{document}\href{https://github.com/bmhiouah}"
               r"{\underline{https://github.com/bmhiouah}}\end{document}")
        out = latex_cv.latex_to_text(src)
        self.assertIn("github.com/bmhiouah", out)
        self.assertNotIn("href", out)

    def test_empty_input_is_not_an_error(self):
        self.assertEqual(latex_cv.latex_to_text(""), "")
        self.assertEqual(latex_cv.latex_to_text(None), "")


if __name__ == "__main__":
    unittest.main()
"""E2E test: opening an existing mail rule for edit pre-selects the stored
condition field/operator in the Simple-mode form.

The bug this guards against (frontend-only, invisible to backend tests):
the condition ``field`` and ``op`` ``<select>``s are populated by Alpine
``<template x-for>`` loops, while the action-type ``<select>`` uses static
``<option>`` markup. When the edit form opens, Alpine binds ``x-model`` on
the field/op selects *before* it has rendered their ``<option>`` children,
so ``el.value = <stored value>`` silently no-ops and the select falls back
to its first option ("From" / "contains"). The underlying model is correct,
but the user sees the wrong field, so edits appear not to "take".

Only a real browser can prove the rendered ``<select>.value`` matches the
stored rule: the binding race is a DOM/Alpine timing effect that a pure-JS
or Django view test cannot observe (the component data is right either way).
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.mail.models import MailAccount, MailRule


class RulesEditFormPreselectTests(PlaywrightTestCase):
    """Editing a rule must show its stored field/operator, not the defaults."""

    def _make_account(self, user):
        return MailAccount.objects.create(
            owner=user,
            email="alice@example.com",
            display_name="Alice",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="alice@example.com",
        )

    def test_edit_form_preselects_stored_field_and_op(self):
        user = self.create_user(username="alice")
        account = self._make_account(user)
        # A non-default leaf: field != "from" (first option) and
        # op != "contains" (first compatible op), so a fallback to the
        # first option is unambiguously distinguishable from the real value.
        rule = MailRule.objects.create(
            account=account,
            name="Subject rule",
            position=0,
            conditions={"field": "subject", "op": "equals", "value": "urgent"},
            actions=[{"type": "mark_read"}],
        )

        self.login_as(user)
        self.page.goto(f"{self.live_server_url}/mail")

        # Open the per-account rules dialog through the same entry point the
        # account context menu uses (``showRules``). Driving the Alpine
        # method directly avoids the fixed-position context-menu hit-testing
        # without changing the code path under test, which is the edit-form
        # render triggered by the click below.
        self.page.wait_for_function(
            "() => window.Alpine && document.querySelector('[x-data=\"mailApp()\"]')"
        )
        self.page.evaluate(
            """async (uuid) => {
              const root = document.querySelector('[x-data="mailApp()"]');
              const app = Alpine.$data(root);
              const acc = app.accounts.find(a => a.uuid === uuid);
              await app.showRules(acc);
            }""",
            str(account.uuid),
        )

        # Click the real "Edit" button on the rule row -> rulesOpenForm(rule).
        edit_btn = self.page.get_by_role("button", name="Edit rule")
        expect(edit_btn).to_be_visible()
        edit_btn.click()

        # The form's condition selects must reflect the stored rule. On the
        # buggy code these resolve to "from"/"contains" (first option).
        field_select = self.page.locator(
            'select[x-model="rulesForm.simpleCondition.field"]'
        )
        op_select = self.page.locator('select[x-model="rulesForm.simpleCondition.op"]')
        expect(field_select).to_be_visible()
        expect(field_select).to_have_value("subject")
        expect(op_select).to_have_value("equals")

        # Saving without touching the field must keep it as "subject":
        # proves the displayed value and the persisted value agree. Scope the
        # locator to the rules dialog: other always-in-DOM dialogs (e.g. the
        # signature dialog) also expose a "Save" button, so an unscoped
        # get_by_role would violate strict mode.
        self.page.locator("#mail-rules-dialog").get_by_role(
            "button", name="Save"
        ).click()
        self.page.wait_for_function(
            """(uuid) => {
              const root = document.querySelector('[x-data="mailApp()"]');
              const app = Alpine.$data(root);
              const r = (app.rulesList || []).find(x => x.uuid === uuid);
              return r && r.conditions && r.conditions.field === 'subject';
            }""",
            arg=str(rule.uuid),
        )

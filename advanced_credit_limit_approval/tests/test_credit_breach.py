# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import AclaTestCommon


@tagged('post_install', '-at_install', 'acla')
class TestCreditBreach(AclaTestCommon):
    """Warning / blocking breaches: e-mail, chatter, salesperson activity,
    blacklist tag, ribbon flag and the blacklist policies."""

    def _notes(self, partner, text):
        return partner.message_ids.filtered(lambda m: text in (m.body or ''))

    def _emails(self, partner, subject_part):
        return partner.message_ids.filtered(
            lambda m: m.subject and subject_part in m.subject and partner.commercial_partner_id in m.partner_ids)

    def test_01_warning_breach_actions(self):
        order = self._create_order(1200.0)  # warning 1000 < 1200 < blocking 2000
        order.with_user(self.salesman).action_confirm()
        self.assertEqual(order.state, 'sale', 'Below the blocking amount: no approval needed')

        self.assertEqual(self.customer.acla_credit_state, 'warning')
        self.assertEqual(self.customer.acla_credit_status, 'warning')
        self.assertFalse(self.customer.acla_is_blacklisted)
        # chatter log
        self.assertTrue(self._notes(self.customer, 'Credit warning limit breached'))
        # e-mail to the customer
        self.assertEqual(len(self._emails(self.customer, 'credit limit notice')), 1)
        # follow-up activity for the assigned salesperson
        activities = self._activities(self.customer, self.act_followup)
        self.assertEqual(len(activities), 1)
        self.assertEqual(activities.user_id, self.salesman)

        # Re-evaluation at the same level: no duplicate e-mail nor activity
        self.customer._acla_evaluate_credit()
        self.assertEqual(len(self._emails(self.customer, 'credit limit notice')), 1)
        self.assertEqual(len(self._activities(self.customer, self.act_followup)), 1)

    def test_02_blocking_breach_blacklists_customer(self):
        order = self._create_order(1200.0)
        order.with_user(self.salesman).action_confirm()
        self.assertEqual(self.customer.acla_credit_state, 'warning')

        # A posted invoice on the child contact pushes the parent over blocking
        self._post_invoice(self.contact, 1500.0)  # exposure 2700 > 2000

        self.assertEqual(self.customer.acla_credit_state, 'blocked')
        self.assertIn(self.blacklist_tag, self.customer.category_id)
        self.assertTrue(self.customer.acla_auto_blacklisted)
        # ribbon flag on the company and on its contacts
        self.customer.invalidate_recordset()
        self.contact.invalidate_recordset()
        self.assertTrue(self.customer.acla_is_blacklisted)
        self.assertTrue(self.contact.acla_is_blacklisted)
        # warning actions executed again for the blocking level
        self.assertTrue(self._notes(self.customer, 'Credit blocking limit breached'))
        self.assertEqual(len(self._emails(self.customer, 'credit hold')), 1)
        activities = self._activities(self.customer, self.act_followup)
        self.assertEqual(len(activities), 1, 'The open follow-up activity is escalated, not duplicated')
        self.assertEqual(activities.user_id, self.salesman)
        self.assertIn('blacklisted', activities.summary)

    def test_03_direct_jump_to_blocked(self):
        self._post_invoice(self.customer, 2500.0)
        self.assertEqual(self.customer.acla_credit_state, 'blocked')
        self.assertIn(self.blacklist_tag, self.customer.category_id)
        self.assertEqual(len(self._activities(self.customer, self.act_followup)), 1)

    def test_04_activity_falls_back_to_order_salesperson(self):
        self.customer.user_id = False
        order = self._create_order(1200.0)
        order.with_user(self.salesman).action_confirm()
        activities = self._activities(self.customer, self.act_followup)
        self.assertEqual(activities.user_id, self.salesman)

    def test_05_no_email_address(self):
        self.customer.email = False
        self._post_invoice(self.customer, 1500.0)
        self.assertEqual(self.customer.acla_credit_state, 'warning')
        self.assertTrue(self._notes(self.customer, 'no e-mail address'))
        self.assertEqual(len(self._activities(self.customer, self.act_followup)), 1)

    def test_06_recovery_and_auto_lift(self):
        self._post_invoice(self.customer, 2500.0)
        self.assertIn(self.blacklist_tag, self.customer.category_id)

        # Without auto-lift the tag stays even if the exposure recovers
        self.customer.acla_blocking_amount = 3000.0
        self.customer._acla_evaluate_credit()
        self.assertEqual(self.customer.acla_credit_state, 'warning')
        self.assertIn(self.blacklist_tag, self.customer.category_id)

        # Back to blocked, then recovery with auto-lift enabled
        self.customer.acla_blocking_amount = 2000.0
        self.customer._acla_evaluate_credit()
        self.company.acla_auto_lift_blacklist = True
        self.customer.acla_blocking_amount = 3000.0
        self.customer._acla_evaluate_credit()
        self.assertNotIn(self.blacklist_tag, self.customer.category_id)
        self.assertTrue(self._notes(self.customer, 'removed automatically'))

    def test_07_manual_blacklist_never_auto_lifted(self):
        self.company.acla_auto_lift_blacklist = True
        self.customer.category_id = [(4, self.blacklist_tag.id)]
        self.customer._acla_evaluate_credit()
        self.assertIn(self.blacklist_tag, self.customer.category_id)

    def test_08_hard_block_policy(self):
        self.company.acla_blacklist_action = 'hard_block'
        self.customer.category_id = [(4, self.blacklist_tag.id)]
        with self.assertRaises(UserError):
            self._create_order(10.0)
        with self.assertRaises(UserError):
            self._create_order(10.0, partner=self.contact)
        # switching an existing quotation to a blacklisted customer is blocked too
        other = self.env['res.partner'].create({'name': 'Other customer'})
        quotation = self._create_order(10.0, partner=other)
        with self.assertRaises(UserError):
            quotation.with_user(self.salesman).write({'partner_id': self.customer.id})

    def test_09_onchange_alert_for_blacklisted_customer(self):
        self.customer.category_id = [(4, self.blacklist_tag.id)]
        for policy in ('force_approval', 'hard_block'):
            self.company.acla_blacklist_action = policy
            order = self.env['sale.order'].with_user(self.salesman).new({
                'partner_id': self.contact.id,
                'company_id': self.company.id,
            })
            result = order._onchange_acla_partner_blacklisted()
            self.assertTrue(result and result.get('warning'), policy)
            self.assertIn('blacklisted', result['warning']['message'])
        # not blacklisted: no alert
        self.customer.category_id = [(3, self.blacklist_tag.id)]
        order = self.env['sale.order'].new({'partner_id': self.customer.id, 'company_id': self.company.id})
        self.assertFalse(order._onchange_acla_partner_blacklisted())

    def test_10_tracking_disabled(self):
        self.company.acla_credit_limit_enabled = False
        order = self._create_order(5000.0)
        order.with_user(self.salesman).action_confirm()
        self.assertEqual(order.state, 'sale')
        self.assertFalse(self.customer.acla_credit_state)
        self.assertFalse(self._activities(self.customer, self.act_followup))

    def test_11_cron(self):
        self.company.acla_credit_limit_enabled = False
        self._post_invoice(self.customer, 1500.0)
        self.assertFalse(self.customer.acla_credit_state)
        self.company.acla_credit_limit_enabled = True
        self.env['res.partner']._cron_acla_evaluate_credit()
        self.assertEqual(self.customer.acla_credit_state, 'warning')

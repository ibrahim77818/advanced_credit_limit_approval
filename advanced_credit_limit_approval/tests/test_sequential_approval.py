# -*- coding: utf-8 -*-
from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger
from psycopg2 import IntegrityError

from .common import AclaTestCommon


@tagged('post_install', '-at_install', 'acla')
class TestSequentialApproval(AclaTestCommon):

    def _submit(self, amount=2500.0, partner=None):
        order = self._create_order(amount, partner=partner)
        order.with_user(self.salesman).action_confirm()
        self.assertEqual(order.state, 'waiting_approval')
        return order

    def test_01_submission(self):
        order = self._submit()
        lines = order.acla_approval_line_ids
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines.sorted('sequence').user_id, self.approver_1 + self.approver_2)
        self.assertTrue(all(l.state == 'pending' and l.round == 1 for l in lines))
        self.assertEqual(order.acla_current_approver_id, self.approver_1)
        self.assertIn('exceeds the blocking amount', order.acla_approval_reason)
        # approval activity for tier 1 only
        self.assertEqual(len(self._activities(order, self.act_approval, self.approver_1)), 1)
        self.assertFalse(self._activities(order, self.act_approval, self.approver_2))
        # the order is not confirmed yet: no un-invoiced exposure
        self.assertAlmostEqual(self.customer.acla_uninvoiced_amount, 0.0)

    def test_02_strict_sequence_enforcement(self):
        order = self._submit()

        # Tier 2 cannot act before tier 1, nor can the salesperson
        for user in (self.approver_2, self.salesman, self.sales_manager):
            with self.assertRaises(AccessError):
                order.with_user(user).action_acla_approve()
            with self.assertRaises(AccessError):
                order.with_user(user).action_acla_open_reject_wizard()

        # Tier 1 (own documents only) can see and approve through the record rule
        order_as_1 = order.with_user(self.approver_1)
        self.assertTrue(order_as_1.acla_is_current_approver)
        order_as_1.action_acla_approve()
        self.assertEqual(order.state, 'waiting_approval')
        self.assertEqual(order.acla_current_approver_id, self.approver_2)
        self.assertEqual(
            order.acla_approval_line_ids.filtered(lambda l: l.user_id == self.approver_1).state, 'approved')
        self.assertFalse(self._activities(order, self.act_approval, self.approver_1),
                         'Tier 1 activity is marked done')
        self.assertEqual(len(self._activities(order, self.act_approval, self.approver_2)), 1)

        # Tier 1 cannot approve twice
        with self.assertRaises(AccessError):
            order.with_user(self.approver_1).action_acla_approve()

        # Final tier confirms the order
        order.with_user(self.approver_2).action_acla_approve()
        self.assertEqual(order.state, 'sale')
        self.assertFalse(order.acla_current_approver_id)
        self.assertFalse(self._activities(order, self.act_approval))
        self.assertTrue(order.message_ids.filtered(lambda m: 'All credit approvals granted' in (m.body or '')))
        # once confirmed the customer exposure (2500) is over the blocking amount
        self.assertEqual(self.customer.acla_credit_state, 'blocked')
        self.assertIn(self.blacklist_tag, self.customer.category_id)

    def test_03_no_bypass(self):
        order = self._submit()
        # RPC-like context cannot skip the credit check
        with self.assertRaises(UserError):
            order.with_user(self.salesman).with_context(acla_skip_credit_check=True).action_confirm()
        # direct state write or edition of a waiting order is refused
        with self.assertRaises(UserError):
            order.with_user(self.salesman).with_context(acla_internal_write=True).write({'state': 'sale'})
        with self.assertRaises(UserError):
            order.with_user(self.salesman).write({'order_line': [Command.create({
                'product_id': self.product.id, 'product_uom_qty': 1, 'price_unit': 1.0})]})
        with self.assertRaises(UserError):
            order.order_line.with_user(self.salesman).write({'price_unit': 1.0})
        with self.assertRaises(UserError):
            order.with_user(self.salesman).action_cancel()
        # the approval steps are read-only for users
        with self.assertRaises(AccessError):
            order.acla_approval_line_ids.with_user(self.approver_1).write({'state': 'approved'})
        # a fresh over-limit quotation with the context still goes to approval
        other = self._create_order(2500.0)
        other.with_user(self.salesman).with_context(acla_skip_credit_check=True).action_confirm()
        self.assertEqual(other.state, 'waiting_approval')

    def test_04_reject_cancels_and_emails(self):
        order = self._submit()
        wizard_action = order.with_user(self.approver_1).action_acla_open_reject_wizard()
        wizard = self.env['acla.reject.wizard'].with_user(self.approver_1).with_context(
            wizard_action['context']).create({'reason': 'Overdue invoices'})
        wizard.action_reject()

        self.assertEqual(order.state, 'cancel')
        self.assertEqual(order.acla_rejection_reason, 'Overdue invoices')
        line_1 = order.acla_approval_line_ids.filtered(lambda l: l.user_id == self.approver_1)
        line_2 = order.acla_approval_line_ids.filtered(lambda l: l.user_id == self.approver_2)
        self.assertEqual(line_1.state, 'rejected')
        self.assertEqual(line_1.note, 'Overdue invoices')
        self.assertEqual(line_2.state, 'cancelled')
        self.assertFalse(self._activities(order, self.act_approval))
        # chatter log + cancellation e-mail to the customer
        self.assertTrue(order.message_ids.filtered(lambda m: 'rejected by' in (m.body or '')))
        emails = order.message_ids.filtered(
            lambda m: m.subject and 'Order Cancelled' in m.subject and self.customer in m.partner_ids)
        self.assertEqual(len(emails), 1)
        self.assertIn('Overdue invoices', emails.body)

    def test_05_withdraw_and_resubmit(self):
        order = self._submit()
        with self.assertRaises(AccessError):
            order.with_user(self.approver_2).action_acla_withdraw()
        order.with_user(self.salesman).action_acla_withdraw()
        self.assertEqual(order.state, 'draft')
        self.assertTrue(all(l.state == 'cancelled' for l in order.acla_approval_line_ids))
        self.assertFalse(self._activities(order, self.act_approval))

        # the quotation can be edited again, then resubmitted in a new round
        order.order_line.with_user(self.salesman).write({'price_unit': 2600.0})
        order.with_user(self.salesman).action_confirm()
        self.assertEqual(order.state, 'waiting_approval')
        self.assertEqual(order.acla_approval_round, 2)
        self.assertEqual(len(order.acla_approval_line_ids.filtered(lambda l: l.round == 2)), 2)
        self.assertEqual(order.acla_current_approver_id, self.approver_1)

    def test_06_rejected_order_back_to_quotation(self):
        order = self._submit()
        order.with_user(self.approver_1)._acla_reject('No')
        order.with_user(self.salesman).action_draft()
        self.assertEqual(order.state, 'draft')
        order.with_user(self.salesman).action_confirm()
        self.assertEqual(order.state, 'waiting_approval')
        self.assertEqual(order.acla_approval_round, 2)

    def test_07_force_approval_for_blacklisted(self):
        self.customer.category_id = [(4, self.blacklist_tag.id)]
        order = self._create_order(10.0, partner=self.contact)
        order.with_user(self.salesman).action_confirm()
        self.assertEqual(order.state, 'waiting_approval')
        self.assertIn('blacklisted', order.acla_approval_reason)

    def test_08_within_limits_confirms_directly(self):
        order = self._create_order(900.0)
        order.with_user(self.salesman).action_confirm()
        self.assertEqual(order.state, 'sale')
        self.assertFalse(order.acla_approval_line_ids)

    def test_09_missing_approvers(self):
        self.team.sudo().acla_approver_ids.unlink()
        order = self._create_order(2500.0)
        with self.assertRaises(UserError):
            order.with_user(self.salesman).action_confirm()
        self.assertEqual(order.state, 'draft')

    def test_10_mixed_batch_confirmation(self):
        small = self._create_order(100.0)
        big = self._create_order(2500.0)
        (small | big).with_user(self.salesman).action_confirm()
        self.assertEqual(small.state, 'sale')
        self.assertEqual(big.state, 'waiting_approval')

    def test_11_approver_configuration(self):
        # unique user per team (SQL constraint)
        with mute_logger('odoo.sql_db'), self.assertRaises(IntegrityError), self.cr.savepoint():
            self.team.sudo().write({'acla_approver_ids': [Command.create({'user_id': self.approver_1.id})]})
        self.env.invalidate_all()
        portal_user = self.env['res.users'].sudo().create({
            'name': 'Portal', 'login': 'acla_portal',
            'groups_id': [Command.set(self.env.ref('base.group_portal').ids)],
        })
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.team.sudo().write({'acla_approver_ids': [Command.create({'user_id': portal_user.id})]})
        self.env.invalidate_all()
        self.assertEqual(self.team.acla_approver_ids.mapped('tier'), [1, 2])

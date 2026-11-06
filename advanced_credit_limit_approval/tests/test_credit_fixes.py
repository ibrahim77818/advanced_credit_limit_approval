# -*- coding: utf-8 -*-
from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import AclaTestCommon


@tagged('post_install', '-at_install', 'acla')
class TestCreditFixes(AclaTestCommon):
    """Confirmed-order increases, live status, draft payments and limit
    validation (warning 1000 / blocking 2000 on self.customer)."""

    def _confirmed_order(self, amount):
        order = self._create_order(amount)
        order.with_user(self.salesman).action_confirm()
        self.assertEqual(order.state, 'sale')
        return order

    def _add_line(self, order, amount, user=None, **extra):
        vals = dict(product_id=self.product.id, product_uom_qty=1.0,
                    price_unit=amount, tax_ids=[Command.clear()], **extra)
        order.with_user(user or self.salesman).write({'order_line': [Command.create(vals)]})

    # ------------------------------------------------------------------
    # 1. Raising a confirmed order
    # ------------------------------------------------------------------
    def test_01_increase_over_blocking_is_refused(self):
        order = self._confirmed_order(100.0)
        with self.assertRaises(UserError):
            self._add_line(order, 5000.0)
        with self.assertRaises(UserError):
            order.order_line.with_user(self.salesman).write({'price_unit': 5000.0})
        with self.assertRaises(UserError):
            order.order_line.with_user(self.salesman).write({'product_uom_qty': 50.0})
        self.env.invalidate_all()
        self.assertAlmostEqual(order.amount_total, 100.0)
        self.assertAlmostEqual(self.customer.acla_exposure_amount, 100.0)
        self.assertEqual(self.customer.acla_credit_status, 'normal')
        self.assertNotIn(self.blacklist_tag, self.customer.category_id)

    def test_02_increase_within_limits_refreshes_status_at_once(self):
        order = self._confirmed_order(100.0)
        self._add_line(order, 1000.0)  # exposure 1100: over warning, under blocking
        self.assertAlmostEqual(order.amount_total, 1100.0)
        self.assertEqual(self.customer.acla_credit_state, 'warning',
                         'Status is refreshed immediately, not by the daily job')
        self.assertEqual(len(self._activities(self.customer, self.act_followup)), 1)

    def test_03_increase_refused_for_blacklisted_customer(self):
        order = self._confirmed_order(100.0)
        self.customer.category_id = [Command.link(self.blacklist_tag.id)]
        with self.assertRaises(UserError):
            self._add_line(order, 10.0)

    def test_04_decrease_and_cancel_refresh_status(self):
        order = self._confirmed_order(1500.0)
        self.assertEqual(self.customer.acla_credit_state, 'warning')
        order.order_line.with_user(self.salesman).write({'price_unit': 500.0})
        self.assertEqual(self.customer.acla_credit_state, 'normal')
        order.order_line.with_user(self.salesman).write({'price_unit': 1500.0})
        self.assertEqual(self.customer.acla_credit_state, 'warning')
        order._action_cancel()
        self.assertEqual(order.state, 'cancel')
        self.assertEqual(self.customer.acla_credit_state, 'normal')

    def test_05_system_lines_are_not_blocked(self):
        order = self._confirmed_order(100.0)
        # re-invoiced expenses / down payments are created by Odoo flows
        self._add_line(order, 5000.0, is_expense=True)
        self.assertAlmostEqual(order.amount_total, 5100.0)

    def test_06_quotations_are_not_checked_on_edit(self):
        quotation = self._create_order(100.0)
        self._add_line(quotation, 5000.0)
        self.assertAlmostEqual(quotation.amount_total, 5100.0)
        quotation.with_user(self.salesman).action_confirm()
        self.assertEqual(quotation.state, 'waiting_approval')

    # ------------------------------------------------------------------
    # 2. Live status
    # ------------------------------------------------------------------
    def test_07_status_is_live(self):
        # stale stored state must not drive what the user sees
        self.customer.acla_credit_state = 'blocked'
        self.customer.invalidate_recordset()
        self.assertEqual(self.customer.acla_credit_status, 'normal')
        self._post_invoice(self.customer, 1500.0)
        self.customer.invalidate_recordset()
        self.assertEqual(self.customer.acla_credit_status, 'warning')
        self.assertEqual(self.contact.acla_credit_status, 'warning')

    def test_08_limit_change_applies_immediately(self):
        self._post_invoice(self.customer, 1500.0)
        self.assertEqual(self.customer.acla_credit_state, 'warning')
        self.customer.write({'acla_warning_amount': 2000.0, 'acla_blocking_amount': 3000.0})
        self.assertEqual(self.customer.acla_credit_state, 'normal')
        self.customer.write({'acla_warning_amount': 500.0, 'acla_blocking_amount': 1000.0})
        self.assertEqual(self.customer.acla_credit_state, 'blocked')
        self.assertIn(self.blacklist_tag, self.customer.category_id)

    # ------------------------------------------------------------------
    # 3. Draft payments
    # ------------------------------------------------------------------
    def test_09_draft_payments_ignored_by_default(self):
        self._post_invoice(self.customer, 2500.0)
        self.assertEqual(self.customer.acla_credit_state, 'blocked')
        self.env['account.payment'].create({
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': self.customer.id,
            'amount': 1000.0,
            'journal_id': self.company_data['default_journal_bank'].id,
        })
        self.customer.invalidate_recordset()
        self.assertFalse(self.company.acla_deduct_draft_payments)
        self.assertAlmostEqual(self.customer.acla_draft_payment_amount, 0.0)
        self.assertAlmostEqual(self.customer.acla_exposure_amount, 2500.0)
        self.assertEqual(self.customer.acla_credit_status, 'blocked')

        self.company.acla_deduct_draft_payments = True
        self.customer.invalidate_recordset()
        self.assertAlmostEqual(self.customer.acla_draft_payment_amount, 1000.0)
        self.assertAlmostEqual(self.customer.acla_exposure_amount, 1500.0)

    # ------------------------------------------------------------------
    # 4. Limit validation
    # ------------------------------------------------------------------
    def test_10_limit_validation(self):
        with self.assertRaises(ValidationError):
            self.customer.write({'acla_warning_amount': 3000.0})  # blocking is 2000
        with self.assertRaises(ValidationError):
            self.customer.write({'acla_blocking_amount': -5.0})
        with self.assertRaises(ValidationError):
            self.customer.write({'acla_warning_amount': -1.0})
        with self.assertRaises(ValidationError):
            self.env['res.partner'].create({
                'name': 'Bad limits', 'is_company': True,
                'acla_warning_amount': 500.0, 'acla_blocking_amount': 100.0,
            })
        self.env.invalidate_all()
        self.assertEqual(self.customer.acla_warning_amount, 1000.0)
        self.assertEqual(self.customer.acla_blocking_amount, 2000.0)
        # a warning amount alone (blocking disabled) is fine, and so is equality
        self.customer.write({'acla_blocking_amount': 0.0, 'acla_warning_amount': 3000.0})
        self.customer.write({'acla_blocking_amount': 3000.0})
        self.assertEqual(self.customer.acla_blocking_amount, 3000.0)

    # ------------------------------------------------------------------
    # v18: confirmed payments without a journal entry
    # ------------------------------------------------------------------
    def test_11_in_process_payment_without_entry_reduces_exposure(self):
        self._post_invoice(self.customer, 2500.0)
        self.assertEqual(self.customer.acla_credit_state, 'blocked')
        # payment method without outstanding account: no journal entry on confirm
        self.inbound_payment_method_line.payment_account_id = False
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': self.customer.id,
            'amount': 1000.0,
            'journal_id': self.company_data['default_journal_bank'].id,
            'payment_method_line_id': self.inbound_payment_method_line.id,
        })
        self.customer.invalidate_recordset()
        self.assertAlmostEqual(self.customer.acla_exposure_amount, 2500.0, msg='draft: not deducted')
        payment.action_post()
        self.assertEqual(payment.state, 'in_process')
        self.assertFalse(payment.move_id)
        self.customer.invalidate_recordset()
        self.assertAlmostEqual(self.customer.acla_ar_amount, 2500.0)
        self.assertAlmostEqual(self.customer.acla_unbooked_payment_amount, 1000.0)
        self.assertAlmostEqual(self.customer.acla_exposure_amount, 1500.0)
        self.assertEqual(self.customer.acla_credit_status, 'warning')

    def test_12_in_process_payment_with_entry_not_counted_twice(self):
        self._post_invoice(self.customer, 2500.0)
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': self.customer.id,
            'amount': 1000.0,
            'journal_id': self.company_data['default_journal_bank'].id,
            'payment_method_line_id': self.inbound_payment_method_line.id,
        })
        payment.action_post()
        self.assertTrue(payment.move_id)
        self.customer.invalidate_recordset()
        self.assertAlmostEqual(self.customer.acla_unbooked_payment_amount, 0.0)
        self.assertAlmostEqual(self.customer.acla_ar_amount, 1500.0)
        self.assertAlmostEqual(self.customer.acla_exposure_amount, 1500.0)

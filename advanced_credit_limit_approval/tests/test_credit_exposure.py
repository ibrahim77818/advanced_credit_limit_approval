# -*- coding: utf-8 -*-

from odoo.exceptions import AccessError
from odoo.tests import tagged

from .common import AclaTestCommon


@tagged('post_install', '-at_install', 'acla')
class TestCreditExposure(AclaTestCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # A customer without limits: exposure maths only, no breach side effects
        cls.exp_customer = cls.env['res.partner'].create({
            'name': 'Exposure Customer', 'is_company': True,
            'email': 'exposure@example.com', 'user_id': cls.salesman.id,
        })
        cls.exp_contact = cls.env['res.partner'].create({
            'name': 'Exposure Contact', 'parent_id': cls.exp_customer.id,
        })

    def assertExposure(self, partner, ar, uninvoiced, draft_payments, total):
        partner.invalidate_recordset()
        self.assertAlmostEqual(partner.acla_ar_amount, ar)
        self.assertAlmostEqual(partner.acla_uninvoiced_amount, uninvoiced)
        self.assertAlmostEqual(partner.acla_draft_payment_amount, draft_payments)
        self.assertAlmostEqual(partner.acla_exposure_amount, total)

    def test_01_empty_exposure(self):
        self.assertExposure(self.exp_customer, 0.0, 0.0, 0.0, 0.0)

    def test_02_quotation_is_not_exposure(self):
        self._create_order(400.0, partner=self.exp_customer)
        self.assertExposure(self.exp_customer, 0.0, 0.0, 0.0, 0.0)

    def test_03_consolidated_real_time_exposure(self):
        # Confirmed, un-invoiced sales on the company
        order_a = self._create_order(500.0, partner=self.exp_customer)
        order_a.with_user(self.salesman).action_confirm()
        self.assertEqual(order_a.state, 'sale')
        self.assertExposure(self.exp_customer, 0.0, 500.0, 0.0, 500.0)

        # Invoice and post order A: moves from un-invoiced to receivables
        invoice_a = order_a._create_invoices()
        self.assertExposure(self.exp_customer, 0.0, 500.0, 0.0, 500.0)  # draft invoice
        invoice_a.action_post()
        self.assertExposure(self.exp_customer, 500.0, 0.0, 0.0, 500.0)

        # Confirmed order on the child contact is consolidated on the parent
        order_b = self._create_order(300.0, partner=self.exp_contact)
        order_b.with_user(self.salesman).action_confirm()
        # Manual posted invoice on the child contact
        self._post_invoice(self.exp_contact, 200.0)
        self.assertExposure(self.exp_customer, 700.0, 300.0, 0.0, 1000.0)

        # Draft customer payment reduces the exposure
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound',
            'partner_type': 'customer',
            'partner_id': self.exp_customer.id,
            'amount': 150.0,
            'journal_id': self.company_data['default_journal_bank'].id,
            'payment_method_line_id': self.inbound_payment_method_line.id,
        })
        self.assertEqual(payment.state, 'draft')
        # off by default: a draft payment does not free up credit
        self.assertExposure(self.exp_customer, 700.0, 300.0, 0.0, 1000.0)
        self.company.acla_deduct_draft_payments = True
        self.assertExposure(self.exp_customer, 700.0, 300.0, 150.0, 850.0)

        # Same figures read from the child contact
        self.assertExposure(self.exp_contact, 700.0, 300.0, 150.0, 850.0)

        # Posting the payment: no longer draft, receivables reduced instead
        payment.action_post()
        self.assertExposure(self.exp_customer, 550.0, 300.0, 0.0, 850.0)

    def test_04_credit_note_and_other_company(self):
        order = self._create_order(800.0, partner=self.exp_customer)
        order.with_user(self.salesman).action_confirm()
        invoice = order._create_invoices()
        invoice.action_post()
        self.assertExposure(self.exp_customer, 800.0, 0.0, 0.0, 800.0)

        # Full credit note linked to the order: receivables and order net to 0
        reversal = self.env['account.move.reversal'].with_context(
            active_model='account.move', active_ids=invoice.ids,
        ).create({'journal_id': invoice.journal_id.id})
        credit_note = self.env['account.move'].browse(reversal.refund_moves()['res_id'])
        credit_note.action_post()
        self.assertExposure(self.exp_customer, 0.0, 800.0, 0.0, 800.0)

        # Exposure is per company: nothing in the second company
        company_2 = self.company_data_2['company']
        partner_c2 = self.exp_customer.with_company(company_2)
        self.assertAlmostEqual(partner_c2.acla_exposure_amount, 0.0)

    def test_05_order_projected_exposure(self):
        confirmed = self._create_order(600.0, partner=self.exp_customer)
        confirmed.with_user(self.salesman).action_confirm()
        quotation = self._create_order(250.0, partner=self.exp_contact)
        self.assertAlmostEqual(quotation._acla_get_projected_exposure(), 850.0)
        self.assertAlmostEqual(confirmed._acla_get_projected_exposure(), 600.0)

    def test_06_only_managers_change_limits(self):
        with self.assertRaises(AccessError):
            self.exp_customer.with_user(self.salesman).write({'acla_blocking_amount': 5000.0})
        self.exp_customer.with_user(self.sales_manager).write({'acla_blocking_amount': 5000.0})
        self.assertEqual(self.exp_customer.acla_blocking_amount, 5000.0)
        # commercial field: synchronised on the child contact
        self.assertEqual(self.exp_contact.acla_blocking_amount, 5000.0)
        # a salesman can still create contacts under a limited company
        contact = self.env['res.partner'].with_user(self.salesman).create({
            'name': 'New Contact', 'parent_id': self.exp_customer.id,
        })
        self.assertEqual(contact.acla_blocking_amount, 5000.0)

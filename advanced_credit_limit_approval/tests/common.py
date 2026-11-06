# -*- coding: utf-8 -*-
from odoo import Command, fields
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class AclaTestCommon(AccountTestInvoicingCommon):
    """Shared fixtures. AccountTestInvoicingCommon is a TransactionCase that
    installs a chart of accounts in a dedicated test company."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_data_2 = cls.setup_other_company()
        cls.company = cls.company_data['company']
        cls.company.write({
            'acla_credit_limit_enabled': True,
            'acla_blacklist_action': 'force_approval',
            'acla_auto_lift_blacklist': False,
        })
        cls.blacklist_tag = cls.env.ref('advanced_credit_limit_approval.res_partner_category_blacklisted')
        cls.act_followup = cls.env.ref('advanced_credit_limit_approval.mail_act_credit_followup')
        cls.act_approval = cls.env.ref('advanced_credit_limit_approval.mail_act_credit_approval')

        group_salesman = cls.env.ref('sales_team.group_sale_salesman')
        group_all_leads = cls.env.ref('sales_team.group_sale_salesman_all_leads')
        group_manager = cls.env.ref('sales_team.group_sale_manager')
        cls.salesman = cls._acla_user('acla_salesman', group_salesman)
        # own-documents-only salesman: needs the approver record rule
        cls.approver_1 = cls._acla_user('acla_approver_1', group_salesman)
        cls.approver_2 = cls._acla_user('acla_approver_2', group_all_leads)
        cls.sales_manager = cls._acla_user('acla_sales_manager', group_manager)

        cls.team = cls.env['crm.team'].sudo().create({
            'name': 'Credit Control Team',
            'company_id': cls.company.id,
            'acla_approver_ids': [
                Command.create({'sequence': 1, 'user_id': cls.approver_1.id}),
                Command.create({'sequence': 2, 'user_id': cls.approver_2.id}),
            ],
        })
        cls.pricelist = cls.env['product.pricelist'].create({
            'name': 'ACLA pricelist',
            'currency_id': cls.company.currency_id.id,
            'company_id': cls.company.id,
        })
        cls.product = cls.env['product.product'].create({
            'name': 'ACLA Service',
            'type': 'service',
            'list_price': 100.0,
            'invoice_policy': 'order',
            'taxes_id': [Command.clear()],
        })
        cls.customer = cls.env['res.partner'].create({
            'name': 'ACLA Customer',
            'is_company': True,
            'email': 'acla.customer@example.com',
            'user_id': cls.salesman.id,
        })
        cls.contact = cls.env['res.partner'].create({
            'name': 'ACLA Contact',
            'parent_id': cls.customer.id,
            'email': 'acla.contact@example.com',
        })
        cls.customer.write({
            'acla_warning_amount': 1000.0,
            'acla_blocking_amount': 2000.0,
        })

    @classmethod
    def _acla_user(cls, login, groups):
        return cls.env['res.users'].sudo().create({
            'name': login,
            'login': login,
            'email': '%s@example.com' % login,
            'company_id': cls.company.id,
            'company_ids': [Command.set(cls.company.ids)],
            'group_ids': [Command.set(groups.ids)],
        })

    def _create_order(self, amount, partner=None, user=None):
        user = user or self.salesman
        return self.env['sale.order'].with_user(user).create({
            'partner_id': (partner or self.customer).id,
            'user_id': user.id,
            'team_id': self.team.id,
            'pricelist_id': self.pricelist.id,
            'company_id': self.company.id,
            'order_line': [Command.create({
                'product_id': self.product.id,
                'product_uom_qty': 1.0,
                'price_unit': amount,
                'tax_ids': [Command.clear()],
            })],
        })

    def _post_invoice(self, partner, amount):
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': partner.id,
            'invoice_date': fields.Date.today(),
            'invoice_line_ids': [Command.create({
                'name': 'ACLA manual line',
                'quantity': 1.0,
                'price_unit': amount,
                'tax_ids': [Command.clear()],
            })],
        })
        invoice.action_post()
        return invoice

    def _activities(self, record, act_type, user=None):
        domain = [
            ('res_model', '=', record._name),
            ('res_id', '=', record.id),
            ('activity_type_id', '=', act_type.id),
        ]
        if user:
            domain.append(('user_id', '=', user.id))
        return self.env['mail.activity'].search(domain)

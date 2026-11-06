# -*- coding: utf-8 -*-
from collections import defaultdict

from odoo import models


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        posted._acla_reevaluate_credit()
        return posted

    def button_draft(self):
        res = super().button_draft()
        self._acla_reevaluate_credit()
        return res

    def _acla_reevaluate_credit(self):
        """Receivable movements change the exposure: re-evaluate the credit
        status of the customers involved (per company)."""
        partners_by_company = defaultdict(lambda: self.env['res.partner'])
        for move in self:
            company = move.company_id
            if not company.acla_credit_limit_enabled:
                continue
            receivable_partners = move.line_ids.filtered(
                lambda l: l.account_id.account_type == 'asset_receivable' and l.partner_id
            ).partner_id.commercial_partner_id
            if receivable_partners:
                partners_by_company[company] |= receivable_partners
        if partners_by_company:
            self.env['res.partner']._acla_evaluate_by_company(partners_by_company)

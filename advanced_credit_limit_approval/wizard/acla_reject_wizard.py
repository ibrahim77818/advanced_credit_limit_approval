# -*- coding: utf-8 -*-
from odoo import fields, models


class AclaRejectWizard(models.TransientModel):
    _name = 'acla.reject.wizard'
    _description = 'Reject Sales Order Credit Approval'

    order_id = fields.Many2one('sale.order', string='Order', required=True, readonly=True)
    reason = fields.Text(string='Rejection Reason', required=True)

    def action_reject(self):
        self.ensure_one()
        self.order_id._acla_reject(self.reason)
        return {'type': 'ir.actions.act_window_close'}

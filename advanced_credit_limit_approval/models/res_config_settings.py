# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    acla_credit_limit_enabled = fields.Boolean(
        related='company_id.acla_credit_limit_enabled', readonly=False)
    acla_blacklist_action = fields.Selection(
        related='company_id.acla_blacklist_action', readonly=False)
    acla_deduct_draft_payments = fields.Boolean(
        related='company_id.acla_deduct_draft_payments', readonly=False)
    acla_auto_lift_blacklist = fields.Boolean(
        related='company_id.acla_auto_lift_blacklist', readonly=False)

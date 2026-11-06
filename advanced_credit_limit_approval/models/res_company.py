# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    acla_credit_limit_enabled = fields.Boolean(
        string='Credit Limit Tracking',
        default=False,
        help='Track the real-time credit exposure of customers and enforce '
             'their Warning / Blocking amounts.')
    acla_blacklist_action = fields.Selection(
        selection=[
            ('hard_block', 'Hard Block'),
            ('force_approval', 'Force Approval'),
        ],
        string='Blacklisted Customer Policy',
        default='force_approval',
        required=True,
        help='Hard Block: sales orders cannot be created for blacklisted customers.\n'
             'Force Approval: quotations are allowed but confirmation always '
             'goes through the Sales Team approval sequence.')
    acla_deduct_draft_payments = fields.Boolean(
        string='Deduct Draft Payments',
        default=False,
        help='Deduct customer payments that are still in draft from the credit '
             'exposure. Off by default: only posted payments free up credit.')
    acla_auto_lift_blacklist = fields.Boolean(
        string='Auto-lift Blacklist',
        default=False,
        help='Remove the Blacklisted tag automatically once the exposure falls '
             'back under the Blocking amount (only when the tag was applied '
             'automatically).')

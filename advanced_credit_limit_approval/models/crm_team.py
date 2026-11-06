# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CrmTeam(models.Model):
    _inherit = 'crm.team'

    acla_approver_ids = fields.One2many(
        'acla.team.approver', 'team_id',
        string='Credit Approvers', copy=True,
        help='Users who approve, in this exact order, sales orders that '
             'exceed the customer credit limits.')


class AclaTeamApprover(models.Model):
    _name = 'acla.team.approver'
    _description = 'Sales Team Credit Approver'
    _order = 'sequence, id'

    team_id = fields.Many2one(
        'crm.team', string='Sales Team', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(string='Sequence', default=10)
    tier = fields.Integer(string='Tier', compute='_compute_tier')
    user_id = fields.Many2one(
        'res.users', string='Approver', required=True, ondelete='restrict',
        domain="[('share', '=', False)]")
    company_id = fields.Many2one(related='team_id.company_id', store=True)

    _team_user_uniq = models.Constraint(
        'unique(team_id, user_id)',
        'A user can appear only once in the approval sequence of a Sales Team.',
    )

    @api.depends('team_id.acla_approver_ids', 'sequence')
    def _compute_tier(self):
        for rec in self:
            ordered = rec.team_id.acla_approver_ids.sorted(lambda a: (a.sequence, a.id or 0))
            rec.tier = (ordered.ids.index(rec.id) + 1) if rec.id in ordered.ids else 0

    @api.constrains('user_id')
    def _check_user_is_salesman(self):
        for rec in self:
            if not rec.user_id.has_group('sales_team.group_sale_salesman'):
                raise ValidationError(_(
                    '%(user)s cannot be a credit approver: approvers must have '
                    'access to the Sales application.', user=rec.user_id.name))

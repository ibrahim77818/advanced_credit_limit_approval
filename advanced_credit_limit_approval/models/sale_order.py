# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare
from odoo.tools.misc import formatLang


ACT_APPROVAL_XMLID = 'advanced_credit_limit_approval.mail_act_credit_approval'
TPL_REJECT_XMLID = 'advanced_credit_limit_approval.mail_template_sale_credit_rejected'

# States in which an order counts in the exposure as confirmed
CONFIRMED_STATES = ('sale',)

# Context keys that allow internal state transitions of waiting orders
INTERNAL_CTX_KEYS = ('acla_internal_write', 'acla_skip_credit_check')

# Header fields that cannot change while an order waits for approval
LOCKED_ORDER_FIELDS = {
    'partner_id', 'partner_invoice_id', 'partner_shipping_id', 'pricelist_id',
    'currency_id', 'payment_term_id', 'fiscal_position_id', 'company_id',
    'team_id', 'order_line',
}


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    state = fields.Selection(
        selection_add=[('waiting_approval', 'Waiting Approval'), ('sale',)],
        ondelete={'waiting_approval': lambda recs: recs.write({'state': 'draft'})})

    acla_tracking_enabled = fields.Boolean(related='company_id.acla_credit_limit_enabled', string='Credit Tracking Enabled')
    acla_company_currency_id = fields.Many2one(related='company_id.currency_id', string='Company Currency')
    acla_partner_blacklisted = fields.Boolean(
        string='Customer Blacklisted', compute='_compute_acla_partner_blacklisted')
    acla_approval_line_ids = fields.One2many(
        'acla.sale.approval.line', 'order_id', string='Credit Approvals', copy=False)
    acla_approval_round = fields.Integer(string='Approval Round', copy=False, default=0)
    acla_approval_reason = fields.Text(string='Approval Reason', copy=False, readonly=True)
    acla_rejection_reason = fields.Text(string='Rejection Reason', copy=False, readonly=True)
    acla_current_approver_id = fields.Many2one(
        'res.users', string='Current Approver', compute='_compute_acla_current_approver',
        store=True, index=True)
    acla_is_current_approver = fields.Boolean(string='Is Current Approver', compute='_compute_acla_is_current_approver')
    acla_can_withdraw = fields.Boolean(string='Can Withdraw', compute='_compute_acla_is_current_approver')
    acla_projected_exposure = fields.Monetary(
        string='Projected Credit Exposure', currency_field='acla_company_currency_id',
        compute='_compute_acla_projected_exposure',
        help='Current exposure of the customer plus this order when it is not '
             'confirmed yet, in company currency.')

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends('partner_id', 'partner_id.category_id',
                 'partner_id.commercial_partner_id.category_id')
    def _compute_acla_partner_blacklisted(self):
        for order in self:
            order.acla_partner_blacklisted = bool(order.partner_id) and order.partner_id.acla_is_blacklisted

    @api.depends('state', 'acla_approval_round', 'acla_approval_line_ids.state',
                 'acla_approval_line_ids.sequence', 'acla_approval_line_ids.user_id')
    def _compute_acla_current_approver(self):
        for order in self:
            line = order._acla_get_current_line()
            order.acla_current_approver_id = line.user_id if order.state == 'waiting_approval' else False

    @api.depends_context('uid')
    @api.depends('acla_current_approver_id', 'state', 'user_id')
    def _compute_acla_is_current_approver(self):
        user = self.env.user
        is_manager = user.has_group('sales_team.group_sale_manager')
        for order in self:
            waiting = order.state == 'waiting_approval'
            order.acla_is_current_approver = waiting and order.acla_current_approver_id == user
            order.acla_can_withdraw = waiting and (order.user_id == user or is_manager)

    @api.depends('partner_id', 'amount_total', 'state', 'currency_id', 'company_id')
    def _compute_acla_projected_exposure(self):
        for order in self:
            if order.partner_id and order.company_id.acla_credit_limit_enabled and order.id:
                order.acla_projected_exposure = order._acla_get_projected_exposure()
            else:
                order.acla_projected_exposure = 0.0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _acla_get_uninvoiced_amount(self):
        """Tax-included amount of the order not yet covered by posted
        customer invoices (net of posted credit notes), in order currency."""
        self.ensure_one()
        today = fields.Date.context_today(self)
        remaining = 0.0
        for line in self.order_line.filtered(lambda l: not l.display_type):
            invoiced = 0.0
            for inv_line in line.invoice_lines.filtered(lambda l: l.parent_state == 'posted'):
                move = inv_line.move_id
                amount = inv_line.price_total
                if move.currency_id != self.currency_id:
                    amount = move.currency_id._convert(
                        amount, self.currency_id, self.company_id, move.invoice_date or today)
                sign = -1.0 if move.move_type == 'out_refund' else 1.0
                invoiced += sign * amount
            remaining += line.price_total - invoiced
        return max(self.currency_id.round(remaining), 0.0)

    def _acla_get_projected_exposure(self):
        self.ensure_one()
        company = self.company_id
        partner = self.partner_id.commercial_partner_id.with_company(company)
        exposure = partner._acla_get_exposure()
        if self.state in ('draft', 'sent', 'waiting_approval'):
            exposure += self.currency_id._convert(
                self.amount_total, company.currency_id, company,
                fields.Date.context_today(self))
        return company.currency_id.round(exposure)

    def _acla_check_requires_approval(self):
        """:return: reason (str) when the order must go through approval,
        False otherwise. Raises for blacklisted customers under Hard Block."""
        self.ensure_one()
        company = self.company_id
        if not company.acla_credit_limit_enabled or not self.partner_id:
            return False
        partner = self.partner_id.commercial_partner_id.with_company(company)
        if partner.acla_is_blacklisted:
            if company.acla_blacklist_action == 'hard_block':
                raise UserError(_(
                    'The customer %s is blacklisted: sales orders are blocked by '
                    'the credit limit policy.', partner.display_name))
            return _('The customer %s is blacklisted.', partner.display_name)
        blocking = partner.acla_blocking_amount
        if blocking:
            projected = self._acla_get_projected_exposure()
            if float_compare(projected, blocking, precision_rounding=company.currency_id.rounding) > 0:
                return _(
                    'Projected credit exposure %(exposure)s exceeds the blocking amount %(limit)s.',
                    exposure=partner._acla_format_amount(projected),
                    limit=partner._acla_format_amount(blocking))
        return False

    def _acla_get_current_line(self):
        self.ensure_one()
        lines = self.acla_approval_line_ids.filtered(
            lambda l: l.round == self.acla_approval_round and l.state == 'pending')
        return lines.sorted(lambda l: (l.sequence, l.id or 0))[:1]

    def _acla_hard_block_check(self):
        for order in self:
            company = order.company_id
            if (company.acla_credit_limit_enabled
                    and company.acla_blacklist_action == 'hard_block'
                    and order.partner_id
                    and order.partner_id.with_company(company).acla_is_blacklisted):
                raise UserError(_(
                    'The customer %s is blacklisted: sales orders are blocked by '
                    'the credit limit policy.', order.partner_id.commercial_partner_id.display_name))

    def _acla_is_internal(self):
        """Internal transitions are only honoured in sudo mode, so the
        context keys cannot be abused through RPC calls."""
        return self.env.su and any(self.env.context.get(k) for k in INTERNAL_CTX_KEYS)

    # ------------------------------------------------------------------
    # ORM overrides
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        orders._acla_hard_block_check()
        return orders

    def write(self, vals):
        if not self._acla_is_internal():
            waiting = self.filtered(lambda o: o.state == 'waiting_approval')
            if waiting:
                locked = LOCKED_ORDER_FIELDS.intersection(vals)
                if locked:
                    raise UserError(_(
                        'Order %(orders)s is waiting for credit approval; it cannot be '
                        'modified. Withdraw it from approval first.',
                        orders=', '.join(waiting.mapped('name'))))
                if 'state' in vals and vals['state'] != 'waiting_approval':
                    raise UserError(_(
                        'Order %(orders)s is waiting for credit approval; use the '
                        'Approve, Reject or Withdraw actions.',
                        orders=', '.join(waiting.mapped('name'))))
            if vals.get('state') == 'waiting_approval':
                raise UserError(_('Orders can only be submitted for credit approval by confirming them.'))
        res = super().write(vals)
        if 'partner_id' in vals:
            self._acla_hard_block_check()
        return res

    # ------------------------------------------------------------------
    # Onchange: immediate UI alert
    # ------------------------------------------------------------------
    @api.onchange('partner_id')
    def _onchange_acla_partner_blacklisted(self):
        if not self.partner_id or not self.company_id.acla_credit_limit_enabled:
            return None
        partner = self.partner_id.commercial_partner_id.with_company(self.company_id)
        if not partner.acla_is_blacklisted:
            return None
        if self.company_id.acla_blacklist_action == 'hard_block':
            message = _(
                '%s is blacklisted for exceeding the credit limit. Sales orders '
                'for this customer are blocked and this quotation cannot be saved.',
                partner.display_name)
        else:
            message = _(
                '%s is blacklisted for exceeding the credit limit. You can prepare '
                'the quotation, but its confirmation will require approval from '
                'the Sales Team approvers.', partner.display_name)
        return {'warning': {'title': _('Blacklisted Customer'), 'message': message}}

    # ------------------------------------------------------------------
    # Confirmation & approval workflow
    # ------------------------------------------------------------------
    def action_confirm(self):
        if self.env.su and self.env.context.get('acla_skip_credit_check'):
            res = super().action_confirm()
            self._acla_evaluate_customers()
            return res

        waiting = self.filtered(lambda o: o.state == 'waiting_approval')
        if waiting:
            raise UserError(_(
                'Order %s is waiting for credit approval; only its approvers can confirm it.',
                ', '.join(waiting.mapped('name'))))

        to_approve = {}
        for order in self:
            reason = order._acla_check_requires_approval()
            if reason:
                order._acla_check_approvers()
                to_approve[order] = reason

        approval_orders = self.browse([o.id for o in to_approve])
        others = self - approval_orders
        res = True
        if others:
            res = super(SaleOrder, others).action_confirm()
            others._acla_evaluate_customers()
        for order, reason in to_approve.items():
            order._acla_submit_for_approval(reason)
        return res

    def _can_be_confirmed(self):
        # v17: action_confirm() only accepts draft/sent orders. The final
        # approval (sudo + internal context) confirms a 'waiting_approval' one.
        if (self.state == 'waiting_approval' and self.env.su
                and self.env.context.get('acla_skip_credit_check')):
            return True
        return super()._can_be_confirmed()

    def _acla_evaluate_customers(self):
        for order in self:
            if order.company_id.acla_credit_limit_enabled and order.partner_id:
                order.partner_id.with_company(order.company_id)._acla_evaluate_credit(
                    salesperson=order.user_id)

    def _acla_check_approvers(self):
        self.ensure_one()
        if not self.team_id:
            raise UserError(_(
                'Order %s requires credit approval but has no Sales Team. Set a '
                'Sales Team with approvers first.', self.name))
        if not self.team_id.acla_approver_ids.filtered(lambda a: a.user_id.active):
            raise UserError(_(
                'Order %(order)s requires credit approval but the Sales Team '
                '%(team)s has no approvers. Configure them in the Approvers tab '
                'of the Sales Team.', order=self.name, team=self.team_id.name))

    def _acla_submit_for_approval(self, reason):
        self.ensure_one()
        approvers = self.team_id.acla_approver_ids.filtered(lambda a: a.user_id.active)
        new_round = self.acla_approval_round + 1
        line_cmds = [
            Command.create({
                'round': new_round,
                'sequence': tier,
                'user_id': approver.user_id.id,
                'state': 'pending',
            })
            for tier, approver in enumerate(approvers.sorted(lambda a: (a.sequence, a.id)), start=1)
        ]
        self.sudo().with_context(acla_internal_write=True).write({
            'state': 'waiting_approval',
            'acla_approval_round': new_round,
            'acla_approval_reason': reason,
            'acla_rejection_reason': False,
            'acla_approval_line_ids': line_cmds,
        })
        self.message_post(
            body=Markup('<p><strong>%s</strong><br/>%s</p>') % (
                _('Submitted for credit approval.'), reason),
            subtype_xmlid='mail.mt_note')
        self._acla_schedule_current_approver_activity()

    def _acla_schedule_current_approver_activity(self):
        self.ensure_one()
        line = self._acla_get_current_line()
        if not line:
            return
        total = len(self.acla_approval_line_ids.filtered(lambda l: l.round == self.acla_approval_round))
        self.sudo().activity_schedule(
            ACT_APPROVAL_XMLID,
            user_id=line.user_id.id,
            summary=_('Credit approval required (tier %(tier)s of %(total)s)',
                      tier=line.sequence, total=total),
            note=self.acla_approval_reason or '',
        )

    def _acla_check_current_approver(self):
        """Return the pending line of the current user; raise otherwise."""
        self.ensure_one()
        if self.state != 'waiting_approval':
            raise UserError(_('Order %s is not waiting for credit approval.', self.name))
        line = self._acla_get_current_line()
        if not line:
            raise UserError(_('Order %s has no pending approval step.', self.name))
        if line.user_id != self.env.user:
            raise AccessError(_(
                'You cannot approve or reject %(order)s: tier %(tier)s must be '
                'handled by %(user)s.',
                order=self.name, tier=line.sequence, user=line.user_id.name))
        return line

    def action_acla_approve(self):
        for order in self:
            line = order._acla_check_current_approver()
            line.sudo().write({'state': 'approved', 'action_date': fields.Datetime.now()})
            order.sudo().activity_feedback([ACT_APPROVAL_XMLID], user_id=line.user_id.id,
                                           feedback=_('Approved'))
            total = len(order.acla_approval_line_ids.filtered(lambda l: l.round == order.acla_approval_round))
            order.message_post(
                body=_('Credit approval granted by %(user)s (tier %(tier)s of %(total)s).',
                       user=line.user_id.name, tier=line.sequence, total=total),
                subtype_xmlid='mail.mt_note')
            if order._acla_get_current_line():
                order._acla_schedule_current_approver_activity()
            else:
                order.sudo().with_context(acla_skip_credit_check=True).action_confirm()
                order.message_post(
                    body=_('All credit approvals granted: the order is confirmed.'),
                    subtype_xmlid='mail.mt_note')
        return True

    def action_acla_open_reject_wizard(self):
        self.ensure_one()
        self._acla_check_current_approver()
        return {
            'name': _('Reject Order'),
            'type': 'ir.actions.act_window',
            'res_model': 'acla.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_order_id': self.id},
        }

    def _acla_reject(self, reason):
        self.ensure_one()
        line = self._acla_check_current_approver()
        now = fields.Datetime.now()
        line.sudo().write({'state': 'rejected', 'action_date': now, 'note': reason})
        (self.acla_approval_line_ids.filtered(
            lambda l: l.round == self.acla_approval_round and l.state == 'pending'
        ) - line).sudo().write({'state': 'cancelled'})
        self.sudo().activity_unlink([ACT_APPROVAL_XMLID])
        self.sudo().with_context(acla_internal_write=True).write({'acla_rejection_reason': reason})
        self.sudo().with_context(acla_internal_write=True, disable_cancel_warning=True)._action_cancel()
        self.message_post(
            body=Markup('<p><strong>%s</strong><br/>%s</p>') % (
                _('Credit approval rejected by %s; the order is cancelled.', line.user_id.name),
                reason),
            subtype_xmlid='mail.mt_note')
        template = self.env.ref(TPL_REJECT_XMLID, raise_if_not_found=False)
        if template and self.partner_id.email:
            self.sudo().message_post_with_source(
                template,
                email_layout_xmlid='mail.mail_notification_light',
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
            )
        return True

    def action_acla_withdraw(self):
        for order in self:
            if order.state != 'waiting_approval':
                raise UserError(_('Order %s is not waiting for credit approval.', order.name))
            if not order.acla_can_withdraw:
                raise AccessError(_(
                    'Only the salesperson of %s or a Sales Administrator can withdraw it.', order.name))
            order.acla_approval_line_ids.filtered(
                lambda l: l.round == order.acla_approval_round and l.state == 'pending'
            ).sudo().write({'state': 'cancelled', 'action_date': fields.Datetime.now()})
            order.sudo().activity_unlink([ACT_APPROVAL_XMLID])
            order.sudo().with_context(acla_internal_write=True).write({'state': 'draft'})
            order.message_post(body=_('Withdrawn from credit approval by %s.', self.env.user.name),
                               subtype_xmlid='mail.mt_note')
        return True

    def action_cancel(self):
        waiting = self.filtered(lambda o: o.state == 'waiting_approval')
        if waiting and not self._acla_is_internal():
            raise UserError(_('Orders waiting for credit approval must be rejected or withdrawn.'))
        return super().action_cancel()

    def _action_cancel(self):
        # pending approval activities make no sense on a cancelled order
        waiting = self.filtered(lambda o: o.state == 'waiting_approval')
        if waiting and not self._acla_is_internal():
            raise UserError(_('Orders waiting for credit approval must be rejected or withdrawn.'))
        confirmed = self.filtered(lambda o: o.state in CONFIRMED_STATES)
        orders = self.sudo().with_context(acla_internal_write=True) if waiting else self
        res = super(SaleOrder, orders)._action_cancel()
        # a cancelled confirmed order frees up credit right away
        confirmed._acla_evaluate_customers()
        return res

    # ------------------------------------------------------------------
    # Confirmed orders whose amount changes
    # ------------------------------------------------------------------
    def _acla_snapshot_totals(self):
        """Totals of the confirmed orders of ``self`` under credit tracking."""
        return {
            order.id: order.amount_total
            for order in self
            if order.state in CONFIRMED_STATES and order.company_id.acla_credit_limit_enabled
        }

    def _acla_check_confirmed_totals(self, before):
        """Called after the lines of confirmed orders changed.
        Increases that break the credit policy are refused (the transaction is
        rolled back); any change re-evaluates the customer status at once."""
        if not before:
            return
        orders = self.browse(list(before)).exists()
        changed = orders.filtered(
            lambda o: o.currency_id.compare_amounts(o.amount_total, before[o.id]) != 0)
        if not changed:
            return
        if not self.env.su:
            for order in changed:
                increase = order.amount_total - before[order.id]
                if order.currency_id.compare_amounts(increase, 0.0) > 0:
                    order._acla_check_increase(increase)
        changed._acla_evaluate_customers()

    def _acla_check_increase(self, increase):
        self.ensure_one()
        company = self.company_id
        partner = self.partner_id.commercial_partner_id.with_company(company)
        increase_txt = formatLang(self.env, increase, currency_obj=self.currency_id)
        if partner.acla_is_blacklisted:
            raise UserError(_(
                'Order %(order)s is already confirmed and the customer %(customer)s is '
                'blacklisted: it cannot be increased by %(increase)s. Put the additional '
                'amount on a new quotation so it goes through credit approval.',
                order=self.name, customer=partner.display_name, increase=increase_txt))
        blocking = partner.acla_blocking_amount
        if not blocking:
            return
        exposure = partner._acla_get_exposure()  # already includes this confirmed order
        if float_compare(exposure, blocking, precision_rounding=company.currency_id.rounding) > 0:
            raise UserError(_(
                'Order %(order)s is already confirmed: increasing it by %(increase)s brings '
                'the credit exposure of %(customer)s to %(exposure)s, above the blocking '
                'amount %(limit)s. Put the additional amount on a new quotation so it goes '
                'through credit approval.',
                order=self.name, increase=increase_txt, customer=partner.display_name,
                exposure=partner._acla_format_amount(exposure),
                limit=partner._acla_format_amount(blocking)))


class AclaSaleApprovalLine(models.Model):
    _name = 'acla.sale.approval.line'
    _description = 'Sales Order Credit Approval Step'
    _order = 'round desc, sequence, id'

    order_id = fields.Many2one('sale.order', required=True, ondelete='cascade', index=True)
    round = fields.Integer(string='Round', required=True, default=1)
    sequence = fields.Integer(string='Tier', required=True, default=1)
    user_id = fields.Many2one('res.users', string='Approver', required=True, ondelete='restrict')
    state = fields.Selection(
        selection=[
            ('pending', 'Pending'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
            ('cancelled', 'Cancelled'),
        ],
        string='Status', required=True, default='pending')
    action_date = fields.Datetime(string='Date')
    note = fields.Text(string='Comment')
    company_id = fields.Many2one(related='order_id.company_id', store=True)

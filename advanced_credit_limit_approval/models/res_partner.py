# -*- coding: utf-8 -*-
import logging

from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import float_compare
from odoo.tools.misc import formatLang

_logger = logging.getLogger(__name__)

BLACKLIST_TAG_XMLID = 'advanced_credit_limit_approval.res_partner_category_blacklisted'
ACT_FOLLOWUP_XMLID = 'advanced_credit_limit_approval.mail_act_credit_followup'
TPL_WARNING_XMLID = 'advanced_credit_limit_approval.mail_template_credit_warning'
TPL_BLOCKED_XMLID = 'advanced_credit_limit_approval.mail_template_credit_blocked'

CREDIT_STATES = [
    ('normal', 'Normal'),
    ('warning', 'Warning'),
    ('blocked', 'Blocked'),
]
CREDIT_STATE_RANK = {'normal': 0, 'warning': 1, 'blocked': 2}
THRESHOLD_FIELDS = ('acla_warning_amount', 'acla_blocking_amount')


class ResPartner(models.Model):
    _inherit = 'res.partner'

    # ------------------------------------------------------------------
    # Thresholds & persisted status (per company)
    # ------------------------------------------------------------------
    acla_warning_amount = fields.Float(
        string='Credit Warning Amount',
        company_dependent=True,
        digits='Account',
        help='When the credit exposure exceeds this amount the customer is '
             'e-mailed, the event is logged and a follow-up activity is '
             'assigned to the salesperson. 0 disables the warning.')
    acla_blocking_amount = fields.Float(
        string='Credit Blocking Amount',
        company_dependent=True,
        digits='Account',
        help='When the credit exposure exceeds this amount the warning actions '
             'are executed, the customer is blacklisted and new orders '
             'require approval. 0 disables blocking.')
    acla_credit_state = fields.Selection(
        selection=CREDIT_STATES,
        string='Last Notified Credit Status',
        company_dependent=True,
        copy=False,
        help='Credit status at the last evaluation; used to send the breach '
             'notifications only once per escalation.')
    acla_auto_blacklisted = fields.Boolean(
        string='Blacklisted Automatically',
        company_dependent=True,
        copy=False)

    # ------------------------------------------------------------------
    # Real-time figures (company currency, not stored)
    # ------------------------------------------------------------------
    acla_tracking_enabled = fields.Boolean(string='Credit Tracking Enabled', compute='_compute_acla_tracking_enabled')
    acla_currency_id = fields.Many2one('res.currency', string='Credit Currency', compute='_compute_acla_tracking_enabled')
    acla_company_id = fields.Many2one('res.company', string='Credit Company', compute='_compute_acla_tracking_enabled')
    acla_ar_amount = fields.Monetary(
        string='Posted Receivables', currency_field='acla_currency_id',
        compute='_compute_acla_exposure')
    acla_uninvoiced_amount = fields.Monetary(
        string='Un-invoiced Confirmed Sales', currency_field='acla_currency_id',
        compute='_compute_acla_exposure')
    acla_draft_payment_amount = fields.Monetary(
        string='Draft Customer Payments', currency_field='acla_currency_id',
        compute='_compute_acla_exposure',
        help='Customer payments not yet posted. Deducted from the exposure only '
             'when "Deduct Draft Payments" is enabled in the Sales settings.')
    acla_unbooked_payment_amount = fields.Monetary(
        string='Confirmed Payments Not Yet Booked', currency_field='acla_currency_id',
        compute='_compute_acla_exposure',
        help='Confirmed customer payments ("In Process") that have no journal entry '
             'yet because their payment method has no outstanding account. They are '
             'deducted from the exposure until they are matched with the bank.')
    acla_exposure_amount = fields.Monetary(
        string='Credit Exposure', currency_field='acla_currency_id',
        compute='_compute_acla_exposure',
        help='Posted receivables + un-invoiced confirmed sales (- draft '
             'customer payments when enabled), consolidated on the commercial entity.')
    acla_available_amount = fields.Monetary(
        string='Available Before Blocking', currency_field='acla_currency_id',
        compute='_compute_acla_exposure')
    acla_credit_status = fields.Selection(
        selection=CREDIT_STATES, string='Credit Status',
        compute='_compute_acla_exposure',
        help='Live status computed from the current exposure and limits.')
    acla_deduct_draft_payments = fields.Boolean(
        string='Draft Payments Deducted', compute='_compute_acla_tracking_enabled')
    acla_is_blacklisted = fields.Boolean(
        string='Blacklisted', compute='_compute_acla_is_blacklisted')
    acla_can_edit_limits = fields.Boolean(
        string='Can Edit Credit Limits', compute='_compute_acla_can_edit_limits')

    # ------------------------------------------------------------------
    # Commercial-entity delegation
    # ------------------------------------------------------------------
    @api.model
    def _commercial_fields(self):
        return super()._commercial_fields() + list(THRESHOLD_FIELDS)

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends_context('company')
    def _compute_acla_tracking_enabled(self):
        company = self.env.company
        for partner in self:
            partner.acla_tracking_enabled = company.acla_credit_limit_enabled
            partner.acla_currency_id = company.currency_id
            partner.acla_company_id = company
            partner.acla_deduct_draft_payments = company.acla_deduct_draft_payments

    @api.depends_context('company')
    def _compute_acla_exposure(self):
        data = self._acla_get_exposure_data() if self.ids else {}
        for partner in self:
            vals = data.get(partner.commercial_partner_id.id) if partner.id else None
            if not vals:
                vals = dict(ar=0.0, uninvoiced=0.0, draft_payments=0.0, unbooked=0.0, total=0.0)
            partner.acla_ar_amount = vals['ar']
            partner.acla_unbooked_payment_amount = vals['unbooked']
            partner.acla_uninvoiced_amount = vals['uninvoiced']
            partner.acla_draft_payment_amount = vals['draft_payments']
            partner.acla_exposure_amount = vals['total']
            commercial = partner.commercial_partner_id
            blocking = commercial.acla_blocking_amount
            partner.acla_available_amount = (blocking - vals['total']) if blocking else 0.0
            partner.acla_credit_status = (
                commercial._acla_state_for_exposure(vals['total']) if commercial else 'normal')

    @api.depends_context('uid')
    def _compute_acla_can_edit_limits(self):
        user = self.env.user
        can_edit = (user.has_group('sales_team.group_sale_manager')
                    or user.has_group('account.group_account_manager'))
        for partner in self:
            partner.acla_can_edit_limits = can_edit

    @api.depends('category_id', 'commercial_partner_id.category_id')
    def _compute_acla_is_blacklisted(self):
        tag = self.env.ref(BLACKLIST_TAG_XMLID, raise_if_not_found=False)
        for partner in self:
            partner.acla_is_blacklisted = bool(tag) and (
                tag in partner.category_id
                or tag in partner.commercial_partner_id.category_id)

    # ------------------------------------------------------------------
    # Exposure engine
    # ------------------------------------------------------------------
    def _acla_get_exposure_data(self):
        """Compute the credit exposure of the commercial entities of ``self``
        for the current company (``self.env.company``), in company currency.

        exposure = posted receivables (open residual)
                 + un-invoiced part of confirmed sales orders (tax included)
                 - confirmed payments without a journal entry (v18 "In Process")
                 - draft customer payments (only with "Deduct Draft Payments")

        :return: {commercial_partner_id: {'ar', 'uninvoiced', 'draft_payments', 'total'}}
        """
        company = self.env.company
        commercials = self.filtered('id').commercial_partner_id
        result = {
            cp.id: dict(ar=0.0, uninvoiced=0.0, draft_payments=0.0, unbooked=0.0, total=0.0)
            for cp in commercials
        }
        if not commercials:
            return result

        Partner = self.env['res.partner'].sudo().with_context(active_test=False)
        members = Partner.search([('commercial_partner_id', 'in', commercials.ids)])
        to_commercial = {p.id: p.commercial_partner_id.id for p in members}
        today = fields.Date.context_today(self)
        company_currency = company.currency_id

        # 1. Posted, open receivables
        aml_groups = self.env['account.move.line'].sudo()._read_group(
            [
                ('partner_id', 'in', members.ids),
                ('company_id', '=', company.id),
                ('account_id.account_type', '=', 'asset_receivable'),
                ('parent_state', '=', 'posted'),
                ('reconciled', '=', False),
            ],
            groupby=['partner_id'], aggregates=['amount_residual:sum'])
        for partner, amount in aml_groups:
            cp_id = to_commercial.get(partner.id)
            if cp_id in result:
                result[cp_id]['ar'] += amount or 0.0

        # 2. Un-invoiced part of confirmed sales orders (tax included)
        orders = self.env['sale.order'].sudo().search([
            ('partner_id', 'in', members.ids),
            ('company_id', '=', company.id),
            ('state', 'in', ('sale',)),
        ])
        for order in orders:
            remaining = order._acla_get_uninvoiced_amount()
            if float_compare(remaining, 0.0, precision_rounding=order.currency_id.rounding) <= 0:
                continue
            if order.currency_id != company_currency:
                remaining = order.currency_id._convert(remaining, company_currency, company, today)
            cp_id = to_commercial.get(order.partner_id.id)
            if cp_id in result:
                result[cp_id]['uninvoiced'] += remaining

        # 3. Draft customer payments: optional, off by default (an unconfirmed
        #    draft must not free up credit unless the company decides so)
        if company.acla_deduct_draft_payments:
            pay_groups = self.env['account.payment'].sudo()._read_group(
                [
                    ('partner_id', 'in', members.ids),
                    ('company_id', '=', company.id),
                    ('state', '=', 'draft'),
                    ('partner_type', '=', 'customer'),
                ],
                groupby=['partner_id'], aggregates=['amount_company_currency_signed:sum'])
            for partner, amount in pay_groups:
                cp_id = to_commercial.get(partner.id)
                if cp_id in result:
                    result[cp_id]['draft_payments'] += amount or 0.0

        # 4. Confirmed payments without a journal entry (v18: payment methods
        #    without an outstanding account only book an entry at bank matching)
        unbooked_groups = self.env['account.payment'].sudo()._read_group(
            [
                ('partner_id', 'in', members.ids),
                ('company_id', '=', company.id),
                ('state', '=', 'in_process'),
                ('move_id', '=', False),
                ('partner_type', '=', 'customer'),
            ],
            groupby=['partner_id'], aggregates=['amount_company_currency_signed:sum'])
        for partner, amount in unbooked_groups:
            cp_id = to_commercial.get(partner.id)
            if cp_id in result:
                result[cp_id]['unbooked'] += amount or 0.0

        for vals in result.values():
            vals['total'] = company_currency.round(
                vals['ar'] + vals['uninvoiced'] - vals['draft_payments'] - vals['unbooked'])
        return result

    def _acla_get_exposure(self):
        """Exposure (company currency) of the commercial entity of ``self``."""
        self.ensure_one()
        return self._acla_get_exposure_data()[self.commercial_partner_id.id]['total']

    def _acla_state_for_exposure(self, exposure):
        """Map an exposure amount to a credit state for this commercial partner."""
        self.ensure_one()
        rounding = self.env.company.currency_id.rounding
        blocking = self.acla_blocking_amount
        warning = self.acla_warning_amount
        if blocking and float_compare(exposure, blocking, precision_rounding=rounding) > 0:
            return 'blocked'
        if warning and float_compare(exposure, warning, precision_rounding=rounding) > 0:
            return 'warning'
        return 'normal'

    # ------------------------------------------------------------------
    # Evaluation & breach actions
    # ------------------------------------------------------------------
    def _acla_evaluate_credit(self, salesperson=None):
        """Re-evaluate the credit status of the commercial entities of ``self``
        in the current company and run the breach actions on escalation.

        :param salesperson: res.users used for the follow-up activity when the
                            customer has no assigned salesperson.
        :return: {commercial_partner_id: new_state}
        """
        company = self.env.company
        if not company.acla_credit_limit_enabled:
            return {}
        partners = self.filtered('id').commercial_partner_id.sudo().with_company(company)
        if not partners:
            return {}
        data = partners._acla_get_exposure_data()
        tag = self.env.ref(BLACKLIST_TAG_XMLID, raise_if_not_found=False)
        result = {}
        for partner in partners:
            exposure = data[partner.id]['total']
            old_state = partner.acla_credit_state or 'normal'
            new_state = partner._acla_state_for_exposure(exposure)
            vals = {}
            lifted = False
            if new_state != old_state:
                vals['acla_credit_state'] = new_state
            if new_state == 'blocked' and tag and tag not in partner.category_id:
                vals['category_id'] = [Command.link(tag.id)]
                vals['acla_auto_blacklisted'] = True
            elif (new_state != 'blocked' and tag and tag in partner.category_id
                  and partner.acla_auto_blacklisted and company.acla_auto_lift_blacklist):
                vals['category_id'] = [Command.unlink(tag.id)]
                vals['acla_auto_blacklisted'] = False
                lifted = True
            if vals:
                partner.with_context(acla_internal_write=True).write(vals)

            if CREDIT_STATE_RANK[new_state] > CREDIT_STATE_RANK[old_state]:
                partner._acla_notify_breach(new_state, exposure, salesperson=salesperson)
            elif CREDIT_STATE_RANK[new_state] < CREDIT_STATE_RANK[old_state]:
                partner._acla_log_recovery(new_state, exposure, lifted)
            result[partner.id] = new_state
        return result

    def _acla_format_amount(self, amount):
        return formatLang(self.env, amount, currency_obj=self.env.company.currency_id)

    def _acla_notify_breach(self, level, exposure, salesperson=None):
        """Warning actions (+ blacklisting, done by the caller) on a breach:
        e-mail the customer, log in the chatter, schedule a follow-up activity
        for the salesperson."""
        self.ensure_one()
        if level == 'blocked':
            limit = self.acla_blocking_amount
            text = _(
                "Credit blocking limit breached: exposure %(exposure)s exceeds the "
                "blocking amount %(limit)s. The customer has been blacklisted.",
                exposure=self._acla_format_amount(exposure),
                limit=self._acla_format_amount(limit))
            summary = _('Credit blocked: customer blacklisted')
            template_xmlid = TPL_BLOCKED_XMLID
        else:
            limit = self.acla_warning_amount
            text = _(
                "Credit warning limit breached: exposure %(exposure)s exceeds the "
                "warning amount %(limit)s.",
                exposure=self._acla_format_amount(exposure),
                limit=self._acla_format_amount(limit))
            summary = _('Credit warning: follow up with the customer')
            template_xmlid = TPL_WARNING_XMLID

        # 1. Chatter log
        self.message_post(
            body=Markup('<p><strong>%s</strong></p>') % text,
            message_type='comment',
            subtype_xmlid='mail.mt_note',
        )

        # 2. E-mail to the customer
        template = self.env.ref(template_xmlid, raise_if_not_found=False)
        if template and self.email:
            self.message_post_with_source(
                template,
                email_layout_xmlid='mail.mail_notification_light',
                message_type='comment',
                subtype_xmlid='mail.mt_comment',
            )
        elif template:
            self.message_post(
                body=_('Credit notification e-mail not sent: the customer has no e-mail address.'),
                subtype_xmlid='mail.mt_note',
            )

        # 3. Follow-up activity for the salesperson
        user = self._acla_get_followup_user(salesperson)
        if user:
            act_type = self.env.ref(ACT_FOLLOWUP_XMLID, raise_if_not_found=False)
            existing = self.activity_ids.filtered(
                lambda a: a.activity_type_id == act_type and a.user_id == user)
            note = Markup('<p>%s</p>') % text
            if existing:
                existing.write({'summary': summary, 'note': note,
                                'date_deadline': fields.Date.context_today(self)})
            else:
                self.activity_schedule(
                    ACT_FOLLOWUP_XMLID, user_id=user.id, summary=summary, note=note)

    def _acla_get_followup_user(self, salesperson=None):
        self.ensure_one()
        candidates = [self.user_id, salesperson, self.env.user]
        for user in candidates:
            if user and user.active and not user.share and not user._is_superuser():
                return user
        return self.env['res.users']

    def _acla_log_recovery(self, new_state, exposure, lifted):
        self.ensure_one()
        state_label = dict(self._fields['acla_credit_state']._description_selection(self.env))[new_state]
        text = _("Credit status improved to %(state)s (exposure %(exposure)s).",
                 state=state_label, exposure=self._acla_format_amount(exposure))
        if lifted:
            text = '%s %s' % (text, _('The Blacklisted tag was removed automatically.'))
        self.message_post(body=text, subtype_xmlid='mail.mt_note')

    # ------------------------------------------------------------------
    # Guards, actions, cron
    # ------------------------------------------------------------------
    def _acla_check_threshold_access(self, vals_list, on_create=False):
        """Only credit managers may set credit limits. On create, only non-zero
        limits are checked; on write, any change on a commercial entity."""
        if self.env.su:
            return
        if on_create:
            touched = any(vals.get(f) for vals in vals_list for f in THRESHOLD_FIELDS)
        else:
            touched = bool(self) and any(f in vals for vals in vals_list for f in THRESHOLD_FIELDS)
        if not touched:
            return
        user = self.env.user
        if not (user.has_group('sales_team.group_sale_manager')
                or user.has_group('account.group_account_manager')):
            raise AccessError(_(
                'Only Sales Administrators or Accounting Advisers can change '
                'customer credit limits.'))

    def _acla_check_threshold_values(self):
        """Limits must be positive (or 0 = disabled) and the warning amount
        cannot exceed the blocking amount when both are set."""
        for partner in self:
            warning, blocking = partner.acla_warning_amount, partner.acla_blocking_amount
            if warning < 0 or blocking < 0:
                raise ValidationError(_(
                    'Credit limits of %s cannot be negative. Use 0 to disable a limit.',
                    partner.display_name))
            if warning and blocking and warning > blocking:
                raise ValidationError(_(
                    'The credit warning amount (%(warning)s) of %(partner)s cannot be '
                    'higher than its blocking amount (%(blocking)s).',
                    warning=partner._acla_format_amount(warning),
                    blocking=partner._acla_format_amount(blocking),
                    partner=partner.display_name))

    @api.model_create_multi
    def create(self, vals_list):
        roots = [v for v in vals_list if not v.get('parent_id')]
        if roots:
            self._acla_check_threshold_access(roots, on_create=True)
        partners = super().create(vals_list)
        if any(v.get(f) for v in roots for f in THRESHOLD_FIELDS):
            partners.filtered(lambda p: not p.parent_id)._acla_check_threshold_values()
        return partners

    def write(self, vals):
        limits_changed = any(f in vals for f in THRESHOLD_FIELDS)
        roots = self.filtered(lambda p: not p.parent_id) if limits_changed else self.browse()
        if roots:
            # children receive commercial fields through the sync mechanism
            roots._acla_check_threshold_access([vals])
        res = super().write(vals)
        if roots:
            roots._acla_check_threshold_values()
            # new limits apply immediately, not at the next daily run
            roots._acla_evaluate_credit()
        return res

    def action_acla_evaluate_credit(self):
        self._acla_evaluate_credit()
        return True

    @api.model
    def _cron_acla_evaluate_credit(self):
        companies = self.env['res.company'].sudo().search([('acla_credit_limit_enabled', '=', True)])
        for company in companies:
            Partner = self.sudo().with_company(company)
            partners = Partner.search([
                '|', '|',
                ('acla_warning_amount', '>', 0),
                ('acla_blocking_amount', '>', 0),
                ('acla_credit_state', 'in', ['warning', 'blocked']),
            ]).commercial_partner_id
            for partner in partners:
                try:
                    with self.env.cr.savepoint():
                        partner._acla_evaluate_credit()
                except Exception:  # pylint: disable=broad-except
                    _logger.exception('Credit evaluation failed for partner %s (company %s)',
                                      partner.id, company.id)

    @api.model
    def _acla_evaluate_by_company(self, partners_by_company, salesperson=None):
        for company, partners in partners_by_company.items():
            if company.acla_credit_limit_enabled and partners:
                partners.with_company(company)._acla_evaluate_credit(salesperson=salesperson)

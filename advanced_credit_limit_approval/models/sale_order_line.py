# -*- coding: utf-8 -*-
from odoo import _, api, models
from odoo.exceptions import UserError

# Line fields that change the amount of the order
LOCKED_LINE_FIELDS = {
    'product_id', 'product_template_id', 'product_uom_qty', 'product_uom_id',
    'price_unit', 'discount', 'tax_ids', 'order_id', 'display_type',
}


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def _acla_check_not_waiting(self, orders=None):
        orders = orders if orders is not None else self.order_id
        waiting = orders.filtered(lambda o: o.state == 'waiting_approval')
        if waiting and not waiting._acla_is_internal():
            raise UserError(_(
                'Order %s is waiting for credit approval; its lines cannot be '
                'modified. Withdraw it from approval first.',
                ', '.join(waiting.mapped('name'))))

    @api.model
    def _acla_is_system_line(self, vals):
        """Down payments and re-invoiced expenses are created by Odoo flows,
        not by the salesperson: they are not credit-checked."""
        return bool(vals.get('is_downpayment') or vals.get('is_expense'))

    @api.model_create_multi
    def create(self, vals_list):
        order_ids = {vals.get('order_id') for vals in vals_list if vals.get('order_id')}
        orders = self.env['sale.order'].browse(order_ids)
        if orders:
            self._acla_check_not_waiting(orders)
        user_order_ids = {vals.get('order_id') for vals in vals_list
                          if vals.get('order_id') and not self._acla_is_system_line(vals)}
        before = self.env['sale.order'].browse(user_order_ids)._acla_snapshot_totals()
        lines = super().create(vals_list)
        orders._acla_check_confirmed_totals(before)
        return lines

    def write(self, vals):
        before = {}
        if LOCKED_LINE_FIELDS.intersection(vals):
            self._acla_check_not_waiting()
            before = self.filtered(
                lambda l: not (l.is_downpayment or l.is_expense)
            ).order_id._acla_snapshot_totals()
        res = super().write(vals)
        self.order_id._acla_check_confirmed_totals(before)
        return res

    def unlink(self):
        self._acla_check_not_waiting()
        orders = self.order_id
        before = orders._acla_snapshot_totals()
        res = super().unlink()
        orders._acla_check_confirmed_totals(before)
        return res

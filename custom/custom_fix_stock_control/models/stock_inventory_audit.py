# -*- coding: utf-8 -*-
from odoo import models, fields, api, _


class StockInventoryAudit(models.Model):
    _name = 'stock.inventory.audit'
    _inherit = ['stock.inventory.audit', 'mail.thread', 'mail.activity.mixin']

    company_id = fields.Many2one(
        'res.company',
        string='Empresa',
        default=lambda self: self.env.company,
        readonly=True,
        tracking=True,
    )

    @api.model
    def create(self, vals):
        audit = super().create(vals)
        audit.message_post(
            body="Auditoría %s creada por %s."
            % (audit.name, audit.user_id.name)
        )
        return audit

    def _find_product_locations(self, product_id, lot_id=False, exclude_location_id=False):
        domain = [
            ('product_id', '=', product_id),
            ('quantity', '>', 0),
            ('location_id.usage', '=', 'internal'),
        ]
        if lot_id:
            domain.append(('lot_id', '=', lot_id))
        if exclude_location_id:
            domain.append(('location_id', '!=', exclude_location_id))
        quants = self.env['stock.quant'].search(domain)
        result = []
        for q in quants:
            result.append({
                'location': q.location_id.complete_name,
                'qty': q.quantity,
            })
        return result

    def _format_location_list(self, loc_list):
        if not loc_list:
            return ''
        parts = []
        for item in loc_list:
            plural = 'uds' if item['qty'] != 1 else 'ud'
            parts.append('[%s] (%s %s)' % (item['location'], item['qty'], plural))
        return ', '.join(parts)

    def _build_summary_reason(self, data):
        lines = data['lines']
        product_id = data['product_id']
        lot_id = data['lot_id']
        theoretical_qty = data['theoretical_qty']
        counted_qty = data['counted_qty']
        current_location_id = lines[0].location_id.id

        # Faltante: no fue escaneado, sistema dice que pertenece aquí
        if counted_qty == 0 and theoretical_qty > 0:
            loc_name = lines[0].location_id.complete_name
            return (
                'No escaneado en [%s]. De acuerdo al sistema, este lote está '
                'registrado en esta ubicación.' % loc_name
            )

        # Sobrante: escaneado aquí, pero no pertenece a esta ubicación
        if theoretical_qty == 0 and counted_qty > 0:
            loc_name = lines[0].location_id.complete_name
            loc_list = self._find_product_locations(
                product_id, lot_id, exclude_location_id=current_location_id)
            loc_str = self._format_location_list(loc_list)
            if loc_str:
                return (
                    'No corresponde a [%s]. De acuerdo al sistema, '
                    'este lote está registrado en %s.' % (loc_name, loc_str)
                )
            return (
                'No corresponde a [%s]. '
                'No se encuentra registrado en el sistema.' % loc_name
            )

        # Diferencia de cantidad
        total_diff = counted_qty - theoretical_qty
        diff_label = 'sobrante' if total_diff > 0 else 'faltante'
        return (
            'En [%s]: sistema indica %s, contadas %s (%s %s).'
            % (lines[0].location_id.complete_name,
               theoretical_qty, counted_qty,
               diff_label, abs(total_diff))
        )


class StockInventoryAuditLine(models.Model):
    _inherit = 'stock.inventory.audit.line'

    def _get_difference_reason(self, all_lines):
        self.ensure_one()
        if self.counted_qty == self.theoretical_qty:
            return ''

        # Faltante: no fue escaneado, sistema dice que pertenece aquí
        if self.counted_qty == 0 and self.theoretical_qty > 0:
            duplicate = all_lines.filtered(
                lambda l: l != self
                and l.product_id == self.product_id
                and l.lot_id == self.lot_id
                and l.counted_qty > 0
            )
            if duplicate:
                locs = duplicate.mapped('location_id.complete_name')
                return (
                    'No registrado en [%s]. '
                    'Escaneado en: %s'
                    % (self.location_id.complete_name, ', '.join(locs))
                )
            loc_list = self.audit_id._find_product_locations(
                self.product_id.id,
                self.lot_id.id if self.lot_id else False,
                exclude_location_id=self.location_id.id,
            )
            loc_str = self.audit_id._format_location_list(loc_list)
            if loc_str:
                return (
                    'No escaneado en [%s]. De acuerdo al sistema, este lote está '
                    'registrado en %s.' % (self.location_id.complete_name, loc_str)
                )
            return (
                'No escaneado en [%s]. De acuerdo al sistema, este lote está '
                'registrado en esta ubicación.' % self.location_id.complete_name
            )

        # Sobrante: escaneado aquí, pero no pertenece a esta ubicación
        if self.theoretical_qty == 0 and self.counted_qty > 0:
            missing = all_lines.filtered(
                lambda l: l != self
                and l.product_id == self.product_id
                and l.lot_id == self.lot_id
                and l.counted_qty == 0
            )
            if missing:
                locs = missing.mapped('location_id.complete_name')
                return (
                    'Escaneado en [%s] pero registrado en [%s].'
                    % (self.location_id.complete_name, ', '.join(locs))
                )
            loc_list = self.audit_id._find_product_locations(
                self.product_id.id,
                self.lot_id.id if self.lot_id else False,
                exclude_location_id=self.location_id.id,
            )
            loc_str = self.audit_id._format_location_list(loc_list)
            if loc_str:
                return (
                    'No corresponde a la ubicación: [%s]. De acuerdo al sistema, '
                    'este lote está registrado en %s.'
                    % (self.location_id.complete_name, loc_str)
                )
            return (
                'No corresponde a la ubicación: [%s]. '
                'No se encuentra registrado en el sistema.'
                % self.location_id.complete_name
            )

        # Diferencia de cantidad
        diff = self.counted_qty - self.theoretical_qty
        return (
            'Contadas %s de %s unidades en [%s]. Diferencia: %s'
            % (self.counted_qty, self.theoretical_qty,
               self.location_id.complete_name, diff)
        )

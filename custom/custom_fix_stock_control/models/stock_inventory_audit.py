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
            if isinstance(exclude_location_id, (list, tuple, set)):
                domain.append(('location_id', 'not in', list(exclude_location_id)))
            else:
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
            parts.append('[%s] (%s en sistema)' % (item['location'], item['qty']))
        return ', '.join(parts)

    def _format_qty(self, qty, label=''):
        if label == 'contada' and qty != 1:
            label = 'contadas'
        if label:
            return '(%s %s)' % (qty, label)
        plural = 'uds' if qty != 1 else 'ud'
        return '(%s %s)' % (qty, plural)

    def _process_lot(self, audit, lot):
        existing_lines = audit.line_ids.filtered(
            lambda l: l.lot_id == lot
        )
        if existing_lines:
            same_location = existing_lines.filtered(
                lambda l: l.location_id == audit.current_scan_location_id
            )
            if same_location:
                return {
                    'error': 'El lote %s ya fue escaneado en esta ubicacion.' % lot.name,
                }
            other_locs = existing_lines.mapped('location_id.complete_name')
            other_str = ', '.join('[%s]' % loc for loc in other_locs)
            return {
                'error': 'El lote %s ya fue escaneado en %s. No se puede escanear en otra ubicacion.'
                % (lot.name, other_str),
            }
        theoretical_qty = audit._get_theoretical_qty(
            lot.product_id.id, lot.id,
            location_id=audit.current_scan_location_id.id,
        )
        audit.line_ids = [(0, 0, {
            'product_id': lot.product_id.id,
            'lot_id': lot.id,
            'location_id': audit.current_scan_location_id.id,
            'theoretical_qty': theoretical_qty,
            'counted_qty': 1.0,
        })]
        return {
            'product_id': lot.product_id.id,
            'product_name': lot.product_id.display_name,
            'lot_name': lot.name,
            'scanned_qty': 1.0,
            'theoretical_qty': theoretical_qty,
            'location_id': audit.current_scan_location_id.id,
            'location_name': audit.current_scan_location_id.complete_name,
        }

    def _build_summary_reason(self, data):
        lines = data['lines']
        product_id = data['product_id']
        lot_id = data['lot_id']

        missing = [l for l in lines
                   if l.counted_qty == 0 and l.theoretical_qty > 0]
        excess = [l for l in lines
                  if l.theoretical_qty == 0 and l.counted_qty > 0]

        # Desplazado: escaneado en una ubicación pero registrado en otra
        if missing and excess:
            scanned = ', '.join(
                '[%s] %s' % (l.location_id.complete_name,
                             self._format_qty(l.counted_qty, 'contada'))
                for l in excess)
            registered = ', '.join(
                '[%s] %s' % (l.location_id.complete_name,
                             self._format_qty(l.theoretical_qty, 'en sistema'))
                for l in missing)
            return 'Escaneado en %s pero registrado en %s.' % (scanned, registered)

        # Sobrante: escaneado aquí, pero no pertenece a esta ubicación
        if excess:
            scanned = ', '.join(
                '[%s] %s' % (l.location_id.complete_name,
                             self._format_qty(l.counted_qty, 'contada'))
                for l in excess)
            scanned_locs = [l.location_id.id for l in excess]
            loc_list = self._find_product_locations(
                product_id, lot_id, exclude_location_id=scanned_locs)
            if loc_list:
                loc_str = self._format_location_list(loc_list)
                return 'Escaneado en %s pero registrado en %s.' % (scanned, loc_str)
            return (
                'Escaneado en %s pero de acuerdo al sistema no se encuentra '
                'en stock.' % scanned
            )

        # Faltante: no fue escaneado, sistema dice que pertenece aquí
        if missing:
            registered = ', '.join(
                '[%s]' % l.location_id.complete_name for l in missing)
            return (
                'Sistema indica que este lote está en %s pero no fue escaneado.'
                % registered
            )

        # Diferencia de cantidad
        total_diff = data['counted_qty'] - data['theoretical_qty']
        diff_label = 'sobrante' if total_diff > 0 else 'faltante'
        return (
            'En [%s]: el sistema indica %s, se contaron %s (%s de %s).'
            % (lines[0].location_id.complete_name,
               data['theoretical_qty'], data['counted_qty'],
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
                scanned = ', '.join(
                    '[%s] %s' % (l.location_id.complete_name,
                                 self.audit_id._format_qty(l.counted_qty, 'contada'))
                    for l in duplicate)
                registered = '%s %s' % (
                    '[%s]' % self.location_id.complete_name,
                    self.audit_id._format_qty(self.theoretical_qty, 'en sistema'))
                return 'Escaneado en %s pero registrado en %s.' % (scanned, registered)
            return (
                'Sistema indica que este lote está en [%s] pero no fue escaneado.'
                % self.location_id.complete_name
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
                registered = ', '.join(
                    '[%s] %s' % (l.location_id.complete_name,
                                 self.audit_id._format_qty(l.theoretical_qty, 'en sistema'))
                    for l in missing)
                scanned = '%s %s' % (
                    '[%s]' % self.location_id.complete_name,
                    self.audit_id._format_qty(self.counted_qty, 'contada'))
                return 'Escaneado en %s pero registrado en %s.' % (scanned, registered)
            scanned_locs = all_lines.filtered(
                lambda l: l.product_id == self.product_id
                and l.lot_id == self.lot_id
                and l.counted_qty > 0
            ).mapped('location_id.id')
            loc_list = self.audit_id._find_product_locations(
                self.product_id.id,
                self.lot_id.id if self.lot_id else False,
                exclude_location_id=scanned_locs or self.location_id.id,
            )
            if loc_list:
                loc_str = self.audit_id._format_location_list(loc_list)
                return 'Escaneado en [%s] pero registrado en %s.' % (
                    self.location_id.complete_name, loc_str
                )
            return (
                'Escaneado en [%s] pero de acuerdo al sistema no se encuentra '
                'en stock.' % self.location_id.complete_name
            )

        # Diferencia de cantidad
        diff = self.counted_qty - self.theoretical_qty
        diff_label = 'sobrante' if diff > 0 else 'faltante'
        return (
            'En [%s]: el sistema indica %s, se contaron %s (%s de %s).'
            % (self.location_id.complete_name,
               self.theoretical_qty, self.counted_qty,
               diff_label, abs(diff))
        )

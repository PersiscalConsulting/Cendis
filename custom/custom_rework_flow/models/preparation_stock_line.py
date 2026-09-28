# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare


class PreparationStockLine(models.Model):
    """Extiende la linea de preparacion para el re-proceso."""
    _inherit = 'preparation.stock.line'

    excess_removed_qty = fields.Float(
        string='Excedente Retirado',
        default=0.0,
        copy=False,
        digits='Product Unit of Measure',
        help="Cantidad de este producto ya retirada de la ubicacion de "
             "separacion durante el re-proceso (via wizard de excedentes).",
    )

    @api.constrains('qty_demanded')
    def _check_qty_demanded(self):
        for line in self:
            if float_compare(line.qty_demanded, 0.0, precision_digits=4) < 0:
                raise UserError('La cantidad demandada no puede ser negativa.')
            if line.has_scans and float_compare(
                    line.qty_demanded, line.qty_scanned, precision_digits=4) < 0:
                # Durante un re-proceso se permite bajar la demanda por
                # debajo de lo escaneado: el stock excedente se retira luego
                # con el wizard de gestion de excedentes.
                if not getattr(line.preparation_id, 'is_rework', False):
                    raise UserError(
                        'No se puede reducir la demanda de %s por debajo de '
                        'lo ya escaneado (%s). Elimine primero las '
                        'cantidades excedentes.'
                        % (line.product_id.display_name, line.qty_scanned))

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line in lines:
            # Se registra a partir de En Proceso en adelante (incluye el
            # re-proceso y la etapa Para Validar).
            if line.preparation_id.state in ('in_progress', 'to_validate'):
                line.preparation_id._log_rework_event(
                    'added',
                    detail='Linea agregada: %s (%s).'
                           % (line.product_id.display_name, line.qty_demanded),
                )
        return lines

    def write(self, vals):
        res = super().write(vals)
        if 'qty_demanded' in vals:
            for line in self:
                if line.preparation_id.state in ('in_progress', 'to_validate'):
                    line.preparation_id._log_rework_event(
                        'qty_changed',
                        detail='%s: demanda a %s.'
                               % (line.product_id.display_name, line.qty_demanded),
                    )
        return res

    def unlink(self):
        for line in self:
            # Durante un re-proceso, si la linea tiene stock fisico en la
            # separacion (escaneos con stock_move_id), no se puede eliminar
            # directamente: el stock quedaria huerfano. Se debe poner la
            # demanda en 0 y retirar el excedente con el wizard.
            if getattr(line.preparation_id, 'is_rework', False) \
                    and line.scan_line_ids.filtered('stock_move_id'):
                raise UserError(
                    'No se puede eliminar la linea de %s durante el '
                    're-proceso porque tiene stock fisico en la separacion '
                    '(%s). Ponga la cantidad demandada en 0 y retire el '
                    'excedente con el wizard.'
                    % (line.product_id.display_name,
                       line.preparation_id.location_dest_id.name or 'separacion'))
        for line in self:
            if line.preparation_id.state in ('in_progress', 'to_validate'):
                line.preparation_id._log_rework_event(
                    'removed',
                    detail='Linea quitada: %s.'
                           % line.product_id.display_name,
                )
        return super().unlink()
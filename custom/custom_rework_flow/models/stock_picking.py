# -*- coding: utf-8 -*-
from odoo import api, fields, models


class StockPicking(models.Model):
    """Extiende el stock.picking de salida para poder devolverlo a
    preparacion antes de su validacion."""
    _inherit = 'stock.picking'

    is_rework_eligible = fields.Boolean(
        string='Puede devolverse a preparacion',
        compute='_compute_is_rework_eligible',
        help="True si este picking de salida fue generado desde repasos y "
             "aun no fue validado (admite devolucion a preparacion).",
    )

    @api.depends()
    def _compute_is_rework_eligible(self):
        """Determina para todos los pickings del registro con una unica
        busqueda (evita una consulta por picking)."""
        linked = set()
        if self.ids:
            reviews = self.env['review.stock.picking'].search([
                ('expedition_picking_ids', 'in', self.ids),
            ])
            linked = set(reviews.mapped('expedition_picking_ids').ids)
        for picking in self:
            picking.is_rework_eligible = picking.id in linked

    def action_return_to_preparation_picking(self):
        """Boton 'Devolver a Preparacion': abre el wizard de autorizacion."""
        self.ensure_one()
        return {
            'name': 'Devolver a Preparacion',
            'type': 'ir.actions.act_window',
            'res_model': 'stock.rework.auth.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_picking_id': self.id,
                'default_origin': 'picking',
            },
        }
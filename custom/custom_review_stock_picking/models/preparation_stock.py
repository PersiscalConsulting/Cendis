# -*- coding: utf-8 -*-
from odoo import models


class PreparationStock(models.Model):
    """Extiende preparation.stock para archivar/reactivar en cascada
    sus repasos asociados (review.stock.picking)."""
    _inherit = 'preparation.stock'

    def write(self, vals):
        res = super().write(vals)
        if 'active' in vals:
            reviews = self.env['review.stock.picking'].sudo().with_context(
                active_test=False,
            ).search([('preparation_id', 'in', self.ids)])
            if reviews:
                reviews.write({'active': vals['active']})
        return res

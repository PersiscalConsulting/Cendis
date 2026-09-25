# -*- coding: utf-8 -*-
from odoo import fields, models


class ReviewStockConfig(models.Model):
    """Extiende la configuracion de repasos con los autorizadores del
    re-proceso (volver a preparar)."""
    _inherit = 'review.stock.config'

    rework_auth_user_ids = fields.Many2many(
        'res.users',
        string='Usuarios autorizados a volver a preparar',
        help="Solo los usuarios que se validen con sus credenciales en el "
             "wizard de autorizacion y esten en esta lista pueden devolver "
             "una preparacion a En Proceso (re-proceso).",
    )

    def rework_auth_applies(self, user):
        self.ensure_one()
        if not user:
            return False
        return user.id in self.rework_auth_user_ids.ids
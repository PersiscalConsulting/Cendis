# -*- coding: utf-8 -*-
from odoo import fields, models, _
from odoo.exceptions import UserError


class ReviewPickingGenerateWizard(models.TransientModel):
    """Wizard para generar un stock.picking de salida agrupando repasos finalizados.

    El boton "Generar expedicion" solo selecciona los repasos y delega la
    logica completa (traslado Separacion -> Ubicacion de salida + creacion
    del stock.picking draft Expedicion -> Clientes) en la server action
    'INVENTARIO REPASO: generar picking de salida', editable desde Odoo web.
    """
    _name = 'review.picking.generate.wizard'
    _description = 'Generar Picking de Salida desde Repasos'

    review_ids = fields.Many2many(
        'review.stock.picking',
        string='Repasos a Agrupar',
        required=True,
        domain=[('state', '=', 'done')],
        help="Seleccione repasos finalizados para generar un picking de salida.",
    )

    def action_generate_picking(self):
        """Delega la generacion del picking a la server action correspondiente.

        Ejecuta 'custom_review_stock_picking.action_generate_out_picking'
        pasando los repasos seleccionados como active_ids (mismo patron que
        review.stock.picking.action_finish). Devuelve la accion que abre el
        picking generado.
        """
        self.ensure_one()
        if not self.review_ids:
            raise UserError(_('Seleccione al menos un repaso.'))
        server_action = self.env.ref(
            'custom_review_stock_picking.action_generate_out_picking',
            raise_if_not_found=False,
        )
        if not server_action:
            raise UserError(_(
                'No existe la server action de generacion de picking de salida.'
            ))
        return server_action.with_context(
            active_ids=self.review_ids.ids
        ).run()

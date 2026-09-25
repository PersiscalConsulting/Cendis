# -*- coding: utf-8 -*-
from odoo import api, fields, models


class PreparationStockScanLine(models.Model):
    """Extiende el detalle de escaneo para mostrar la ubicacion FISICA
    actual del stock durante el re-proceso.

    Un escaneo ya movido a separacion (stock_move_id) tiene su
    'Ubicacion Real' (location_id) apuntando a la ubicacion de origen
    donde fue capturado. Tras validar (y tras la reversa del repaso) el
    stock esta fisicamente en la separacion: este campo la expone para
    mostrarla en el OWL y en los formularios.
    """
    _inherit = 'preparation.stock.scan.line'

    current_location_id = fields.Many2one(
        'stock.location',
        string='Ubicacion Actual',
        compute='_compute_current_location',
        store=False,
        help="Ubicacion fisica actual del stock de este escaneo: la "
             "separacion si el escaneo ya fue movido; si no, la ubicacion "
             "de escaneo (origen).",
    )
    current_location_name = fields.Char(
        string='Ubicacion Actual',
        compute='_compute_current_location',
        store=False,
    )
    moved_to_separation = fields.Boolean(
        string='Movido a separacion',
        compute='_compute_current_location',
        store=False,
        help="True si este escaneo ya genero el stock.move hacia la "
             "separacion (ya no esta en la ubicacion de origen).",
    )

    @api.depends('stock_move_id',
                 'preparation_id.location_dest_id',
                 'location_id')
    def _compute_current_location(self):
        for scan in self:
            if scan.stock_move_id and scan.preparation_id.location_dest_id:
                scan.current_location_id = scan.preparation_id.location_dest_id
                scan.moved_to_separation = True
            else:
                scan.current_location_id = scan.location_id
                scan.moved_to_separation = False
            scan.current_location_name = (
                scan.current_location_id.complete_name
                if scan.current_location_id else ''
            )
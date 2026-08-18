# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    """Configuracion del modulo de Movimientos entre Propietarios."""
    _inherit = 'res.config.settings'

    # -- Comportamiento del escaneo --
    owner_move_manual_qty = fields.Boolean(
        string='Permitir ingreso manual de cantidad en el escaneo',
        config_parameter='owner_move_stock.manual_qty',
    )
    owner_move_allow_delete = fields.Boolean(
        string='Permitir eliminar lineas escaneadas',
        config_parameter='owner_move_stock.allow_delete',
    )

    # -- Validaciones --
    owner_move_validate_stock = fields.Boolean(
        string='Validar existencias al empaquetar',
        config_parameter='owner_move_stock.validate_stock',
    )
    owner_move_require_packed = fields.Boolean(
        string='Exigir empaquetado completo para finalizar',
        config_parameter='owner_move_stock.require_packed',
    )

    # -- Ubicaciones (almacenan el ID como string en ir.config.parameter) --
    owner_move_entry_location = fields.Many2one(
        'stock.location',
        string='Ubicacion de Entrada',
        config_parameter='owner_move_stock.entry_location',
        domain="[('usage', '=', 'internal')]",
    )
    owner_move_exit_location = fields.Many2one(
        'stock.location',
        string='Ubicacion de Salida',
        config_parameter='owner_move_stock.exit_location',
        domain="[('usage', '=', 'internal')]",
    )

    # -- Tipos de operacion (picking types) --
    owner_move_exit_picking_type = fields.Many2one(
        'stock.picking.type',
        string='Operacion de salida',
        config_parameter='owner_move_stock.exit_picking_type',
        domain="[('code', '=', 'outgoing')]",
    )
    owner_move_entry_picking_type = fields.Many2one(
        'stock.picking.type',
        string='Operacion de entrada',
        config_parameter='owner_move_stock.entry_picking_type',
        domain="[('code', '=', 'incoming')]",
    )

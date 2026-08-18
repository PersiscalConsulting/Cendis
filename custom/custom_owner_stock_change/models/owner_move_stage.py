# -*- coding: utf-8 -*-
from odoo import fields, models


class OwnerMoveStage(models.Model):
    _name = 'owner.move.stage'
    _description = 'Etapa de Movimiento entre Propietarios'
    _order = 'sequence, id'

    name = fields.Char(
        string='Etapa',
        required=True,
        translate=True,
    )
    sequence = fields.Integer(
        string='Secuencia',
        default=10,
    )
    state = fields.Selection(
        [
            ('draft', 'Borrador'),
            ('waiting', 'En espera'),
            ('available', 'Disponible'),
            ('done', 'Hecho'),
            ('cancelled', 'Rechazado'),
        ],
        string='Estado Tecnico',
        required=True,
    )
    fold = fields.Boolean(
        string='Plegado en Kanban',
    )
    color = fields.Integer(
        string='Color',
    )

    _sql_constraints = [
        (
            'state_uniq',
            'unique(state)',
            'El estado tecnico debe ser unico por etapa.',
        ),
    ]

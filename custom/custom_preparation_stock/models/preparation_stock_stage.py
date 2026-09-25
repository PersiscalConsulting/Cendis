# -*- coding: utf-8 -*-
from odoo import fields, models


class PreparationStockStage(models.Model):
    _name = 'preparation.stock.stage'
    _description = 'Etapa de Preparacion de Stock'
    _order = 'sequence, id'

    name = fields.Char(string='Etapa', required=True, translate=True)
    sequence = fields.Integer(string='Secuencia', default=10)
    state = fields.Selection(
        [
            ('draft', 'Borrador'),
            ('assigned', 'Asignado'),
            ('in_progress', 'En Proceso'),
            ('to_validate', 'Para Validar'),
            ('done', 'Hecho'),
            ('cancelled', 'Cancelado'),
        ],
        string='Estado Tecnico',
        required=True,
    )
    fold = fields.Boolean(string='Plegado en Kanban')
    color = fields.Integer(string='Color')

    _sql_constraints = [
        ('state_uniq', 'unique(state)',
         'El estado tecnico debe ser unico por etapa.'),
    ]
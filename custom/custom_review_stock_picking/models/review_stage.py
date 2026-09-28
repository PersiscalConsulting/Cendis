# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ReviewStage(models.Model):
    """Etapa configurable del repaso.

    Patron de preparation.stock.stage pero con flags booleanos
    en vez de un selection hardcodeado (decision del PLAN seccion 3).
    El campo state es un campo normal (no compute) para compatibilidad
    con carga de datos XML y el widget statusbar de Odoo.
    Se sincroniza automaticamente via constrains cuando cambian los flags.
    """
    _name = 'review.stage'
    _description = 'Etapa de Repaso de Stock Picking'
    _order = 'sequence, id'

    name = fields.Char(string='Etapa', required=True, translate=True)
    sequence = fields.Integer(string='Secuencia', default=10)

    is_initial = fields.Boolean(
        string='Etapa Inicial',
        help="Marcada para la etapa inicial del flujo (DISPONIBLE).",
    )
    is_final = fields.Boolean(
        string='Etapa Final',
        help="Marcada para la etapa final del flujo (FINALIZADO).",
    )
    is_cancel = fields.Boolean(
        string='Etapa de Cancelacion',
        help="Marcada para la etapa de cancelacion.",
    )

    state = fields.Selection(
        [
            ('available', 'Disponible'),
            ('in_progress', 'En Proceso'),
            ('done', 'Finalizado'),
            ('modified', 'Modificado'),
            ('cancelled', 'Cancelado'),
        ],
        string='Estado Tecnico',
        required=True,
    )

    fold = fields.Boolean(string='Plegado en Kanban')
    color = fields.Integer(string='Color')

    @api.constrains('is_initial', 'is_final', 'is_cancel')
    def _check_flags_and_sync_state(self):
        for stage in self:
            flags = [stage.is_initial, stage.is_final, stage.is_cancel]
            if sum(flags) > 1:
                raise ValidationError(
                    'Los flags is_initial, is_final y is_cancel son '
                    'mutuamente excluyentes: solo uno puede estar activo '
                    'por etapa.'
                )
            if stage.is_initial:
                stage.state = 'available'
            elif stage.is_final:
                stage.state = 'done'
            elif stage.is_cancel:
                stage.state = 'cancelled'
            else:
                # Etapas sin flags conservan su estado tecnico (in_progress o modified)
                if stage.state not in ('in_progress', 'modified'):
                    stage.state = 'in_progress'

    _sql_constraints = [
        (
            'state_uniq',
            'unique(state)',
            'El estado tecnico debe ser unico por etapa.',
        ),
    ]

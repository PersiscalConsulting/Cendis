# -*- coding: utf-8 -*-
from odoo import fields, models


class PreparationStockModificationLog(models.Model):
    """Historial de modificaciones del re-proceso de una preparacion.

    Solo se registran eventos de linea/accion (no cada escaneo individual).
    Guarda ademas quién autorizo el re-proceso, que puede diferir del
    usuario de la sesion (caso: el operario lleva la tablet al autorizador).
    """
    _name = 'preparation.stock.modification.log'
    _description = 'Log de Modificaciones del Re-Proceso'
    _order = 'create_date desc, id desc'

    preparation_id = fields.Many2one(
        'preparation.stock',
        string='Preparacion',
        required=True,
        ondelete='cascade',
        index=True,
    )
    review_id = fields.Many2one(
        'review.stock.picking',
        string='Repaso Relacionado',
        ondelete='set null',
        index=True,
    )
    datetime = fields.Datetime(
        string='Fecha/Hora',
        default=fields.Datetime.now,
        readonly=True,
    )
    user_id = fields.Many2one(
        'res.users',
        string='Usuario',
        default=lambda self: self.env.user,
        readonly=True,
    )
    authorized_by_id = fields.Many2one(
        'res.users',
        string='Autorizado por',
        readonly=True,
        help="Usuario cuyas credenciales se validaron en el wizard de "
             "autorizacion. Puede ser distinto del usuario de la sesion.",
    )
    action = fields.Selection(
        [
            ('reopen', 'Preparacion reabierta'),
            ('review_modified', 'Repaso modificado'),
            ('picking_cancelled', 'Picking cancelado'),
            ('added', 'Linea agregada'),
            ('removed', 'Linea quitada'),
            ('qty_changed', 'Cantidad cambiada'),
            ('stage_changed', 'Etapa cambiada'),
            ('excess_moved', 'Excedente movido'),
            ('validated', 'Preparacion revalidada'),
            ('review_created', 'Repaso creado'),
        ],
        string='Accion',
        required=True,
    )
    reason = fields.Text(
        string='Motivo',
        readonly=True,
    )
    detail = fields.Text(
        string='Detalle',
        readonly=True,
    )
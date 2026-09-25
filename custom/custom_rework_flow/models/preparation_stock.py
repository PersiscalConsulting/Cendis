# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_compare


class PreparationStock(models.Model):
    """Extiende la preparacion para el re-proceso (volver a preparar)."""
    _inherit = 'preparation.stock'

    is_rework = fields.Boolean(
        string='En Re-Proceso',
        default=False,
        copy=False,
        help="True cuando la preparacion fue finalizada y reabierta para "
             "volver a preparar. Habilita la edicion de lineas durante "
             "En Proceso y el historial de modificaciones.",
    )
    version = fields.Integer(
        string='Version',
        default=1,
        copy=False,
        readonly=True,
        help="Iteracion de la misma preparacion (v1, v2, ...). Se incrementa "
             "en cada re-proceso (volver a preparar) y coincide con la "
             "version del proximo repaso generado al revalidar.",
    )
    review_ids = fields.One2many(
        'review.stock.picking',
        'preparation_id',
        string='Repasos',
        readonly=True,
    )
    modification_log_ids = fields.One2many(
        'preparation.stock.modification.log',
        'preparation_id',
        string='Modificaciones',
        readonly=True,
    )
    has_excess = fields.Boolean(
        string='Tiene excedentes',
        compute='_compute_has_excess',
        help="Existe cantidad escaneada por encima de la demandada en la "
             "ubicacion de separacion (debe retirarse con el wizard).",
    )
    active_reviews_count = fields.Integer(
        string='Repasos Activos',
        compute='_compute_review_counts',
    )
    modified_reviews_count = fields.Integer(
        string='Repasos Modificados',
        compute='_compute_review_counts',
    )
    reviews_count = fields.Integer(
        string='Repasos',
        compute='_compute_review_counts',
    )

    @api.depends('line_ids.qty_scanned', 'line_ids.qty_demanded')
    def _compute_has_excess(self):
        for prep in self:
            prep.has_excess = any(
                float_compare(
                    l.qty_scanned,
                    l.qty_demanded,
                    precision_digits=4,
                ) > 0
                for l in prep.line_ids
            )

    def _compute_review_counts(self):
        """Contadores de repasos de TODAS las preparaciones del registro
        en una sola consulta (en lugar de una busqueda por preparacion)."""
        counts = {}
        if self.ids:
            rows = self.env['review.stock.picking'].with_context(
                active_test=False).search_read(
                [('preparation_id', 'in', self.ids)],
                ['preparation_id', 'state'])
            for row in rows:
                prep_id = row['preparation_id'][0] if row['preparation_id'] else False
                if not prep_id:
                    continue
                totals = counts.setdefault(prep_id, [0, 0, 0])
                totals[0] += 1
                if row['state'] == 'modified':
                    totals[1] += 1
                if row['state'] not in ('modified', 'cancelled'):
                    totals[2] += 1
        for prep in self:
            total, modified, active = counts.get(prep.id, (0, 0, 0))
            prep.reviews_count = total
            prep.modified_reviews_count = modified
            prep.active_reviews_count = active

    def action_view_reviews(self):
        """Abre la lista de repasos asociados a esta preparacion."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Repasos',
            'res_model': 'review.stock.picking',
            'view_mode': 'list,form',
            'domain': [('preparation_id', '=', self.id)],
            'context': {
                'default_preparation_id': self.id,
                'active_test': not self.active,
            },
        }

    @api.model
    def _log_rework_event(self, action, review_id=False, detail='', reason=''):
        """Registra un evento en el historial de modificaciones.

        Solo genera ruido util: se invoca desde los puntos de re-proceso.
        Si la sesion trae 'authorized_by_id' en contexto, lo guarda.
        """
        self.ensure_one()
        vals = {
            'preparation_id': self.id,
            'action': action,
            'detail': detail,
            'reason': reason,
        }
        if review_id:
            vals['review_id'] = review_id
        authorized = self.env.context.get('authorized_by_id')
        if authorized:
            vals['authorized_by_id'] = authorized
        return self.env['preparation.stock.modification.log'].create(vals)

    def write(self, vals):
        """Registra los cambios de etapa a partir de que la preparacion
        pasa a En Proceso (la transicion a En Proceso y todas las
        posteriores). Durante el re-proceso no se registra para no
        duplicar el evento 'reopen'."""
        track_stage = 'stage_id' in vals
        old_stages = {}
        if track_stage:
            old_stages = {
                p.id: (p.stage_id.display_name, getattr(p.stage_id, 'state', False))
                for p in self
            }
        res = super(PreparationStock, self).write(vals)
        if track_stage:
            for p in self:
                if p.is_rework:
                    continue
                old_name, old_state = old_stages.get(p.id, (False, False))
                new_state = getattr(p.stage_id, 'state', False)
                if old_name and old_name != p.stage_id.display_name and (
                        old_state == 'in_progress' or new_state == 'in_progress'):
                    p._log_rework_event(
                        'stage_changed',
                        detail='%s -> %s' % (old_name, p.stage_id.display_name),
                    )
        return res

    def action_gestionar_excedentes(self):
        """Boton 'Gestionar Excedentes': abre el wizard de retiro de stock.

        El wizard se pre-crea en el servidor (res_id) con sus lineas de
        excedente ya persistidas, para que el cliente conserve la identidad
        (producto y cantidades) de cada linea al editar ubicacion/paquete.
        """
        self.ensure_one()
        if not (self.is_rework and self.state == 'in_progress'):
            raise UserError(_(
                'Los excedentes solo se gestionan durante el re-proceso '
                'con la preparacion En Proceso.'
            ))
        Wizard = self.env['preparation.rework.excess.wizard']
        wizard = Wizard.create({
            'preparation_id': self.id,
            'line_ids': Wizard._excess_line_vals(self),
        })
        return {
            'name': 'Gestionar Excedentes',
            'type': 'ir.actions.act_window',
            'res_model': 'preparation.rework.excess.wizard',
            'res_id': wizard.id,
            'views': [(False, 'form')],
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_preparation_id': self.id,
            },
        }

    def action_mark_to_validate(self):
        """Intercepta el paso a 'Para Validar': si hay excedentes durante un
        re-proceso, fuerza primero el wizard de gestion de excedentes."""
        self.ensure_one()
        if self.is_rework and self.state == 'in_progress' and self.has_excess:
            return self.action_gestionar_excedentes()
        return super().action_mark_to_validate()
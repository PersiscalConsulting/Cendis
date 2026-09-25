# -*- coding: utf-8 -*-
from collections import defaultdict

from odoo import fields, models, _, api
from odoo.exceptions import UserError
from odoo.tools import float_compare


class ReviewStockPicking(models.Model):
    """Extiende el repaso para el re-proceso (versionado y 'Volver a Preparar')."""
    _inherit = 'review.stock.picking'

    version = fields.Integer(
        string='Version',
        default=1,
        copy=False,
        readonly=True,
        help="Version del repaso para la misma preparacion (v1, v2, ...). "
             "Se incrementa al revalidar una preparacion re-procesada.",
    )
    previous_review_id = fields.Many2one(
        'review.stock.picking',
        string='Repaso Anterior',
        copy=False,
        readonly=True,
        ondelete='set null',
        index=True,
        help="Repaso que dio origen a este cuando la preparacion fue "
             "reprocesada (heredaron sus cantidades repasadas).",
    )
    previous_review_name = fields.Char(
        related='previous_review_id.name',
        string='Repaso Anterior',
        readonly=True,
    )
    expedition_picking_ids = fields.Many2many(
        'stock.picking',
        'review_expedition_picking_rel',
        'review_id',
        'picking_id',
        string='Pickings de Salida',
        readonly=True,
        help="Pickings OUT generados desde este repaso (y sus repasos "
             "agrupados). Se usa para el re-proceso desde el picking.",
    )
    rework_authorized_by_id = fields.Many2one(
        'res.users',
        string='Autorizado por',
        readonly=True,
        help="Usuario que autorizo devolver a preparacion este repaso.",
    )
    rework_auth_datetime = fields.Datetime(
        string='Fecha de autorizacion del re-proceso',
        readonly=True,
    )
    rework_reason = fields.Text(
        string='Motivo del re-proceso',
        readonly=True,
    )

    def _mark_modified(self, auth_user=False, reason=''):
        """Pasa el repaso a la etapa MODIFICADO y deja trazabilidad."""
        self.ensure_one()
        modified = self.env['review.stage'].search(
            [('state', '=', 'modified')], limit=1)
        if not modified:
            raise UserError(_('No existe una etapa "Modificado" configurada para el repaso.'))
        vals = {'stage_id': modified.id, 'date_end': False}
        if auth_user:
            vals.update({
                'rework_authorized_by_id': auth_user.id,
                'rework_auth_datetime': fields.Datetime.now(),
                'rework_reason': reason,
            })
        self.write(vals)

    @api.model
    def _inherit_previous_reviewed_qty(self, prev_review):
        """Copia las cantidades repasadas del repaso anterior al actual.

        Match por (producto, lote); las nuevas lineas quedan en 0 y se
        repasan de cero. Solo copia sobre lineas con cantidad <= 0 para no
        pisar cantidades que ya se cargaron.

        Ademas de la cantidad, hereda el paquete (armado de v1) y las
        marcas de exceso/foraneo. Si una linea previa agrupo varias
        unidades, la cantidad se distribuye acumulativamente sobre las
        lineas nuevas del mismo (producto, lote) en orden de id: como las
        unidades del paquete estan fisicamente en la ubicacion de
        separacion tras la reversa, cada linea nueva que hereda unidades
        conserva el mismo paquete.

        Excepcion: si la demanda de un producto SE REDUJO entre el repaso
        anterior y este (qty_preparation menor), el paquete de v1 dejo de
        ser valido (el wizard de excedentes retiro unidades de el) y el
        producto se repasa completamente: NO hereda cantidad repasada ni
        paquete; sus lineas nacen en 0/pending/sin paquetizar. La subida
        de demanda no entra aqui: las unidades extra llegan como lineas
        nuevas sin herencia (delta) y las originales conservan su paquete.
        """
        self.ensure_one()
        if not prev_review:
            return

        # Productos cuya demanda bajo respecto del repaso anterior.
        prev_qty_by_product = {
            move.product_id.id: move.qty_preparation
            for move in prev_review.move_ids
        }
        decreased_products = set()
        for move in self.move_ids:
            prev_qty = prev_qty_by_product.get(move.product_id.id)
            if prev_qty is None:
                continue
            if float_compare(move.qty_preparation, prev_qty,
                             precision_digits=4) < 0:
                decreased_products.add(move.product_id.id)

        prev_map = defaultdict(list)
        for prev in prev_review.move_ids.move_line_ids.sorted('id'):
            if float_compare(prev.qty_reviewed, 0.0, precision_digits=4) <= 0:
                continue
            prev_map[(prev.product_id.id, prev.lot_id.id or False)].append(prev)

        for ml in self.move_ids.move_line_ids.sorted('id'):
            if float_compare(ml.qty_reviewed, 0.0, precision_digits=4) > 0:
                continue
            if ml.product_id.id in decreased_products:
                continue
            key = (ml.product_id.id, ml.lot_id.id or False)
            prev_lines = prev_map.get(key)
            if not prev_lines:
                continue
            prev = prev_lines[0]
            capacity = (
                ml.qty
                if float_compare(ml.qty, 0.0, precision_digits=4) > 0
                else prev.qty_reviewed
            )
            take = min(prev.qty_reviewed, capacity)
            if float_compare(take, 0.0, precision_digits=4) <= 0:
                continue
            vals = {
                'qty_reviewed': take,
                'state': 'done',
            }
            if prev.package_id:
                vals['package_id'] = prev.package_id.id
            if prev.is_excess:
                vals['is_excess'] = True
            if prev.is_foreign:
                vals['is_foreign'] = True
            ml.with_context(review_auto_create=True).write(vals)
            prev.qty_reviewed -= take
            if float_compare(prev.qty_reviewed, 0.0, precision_digits=4) <= 0:
                prev_lines.pop(0)

    def action_return_to_preparation(self):
        """Boton 'Volver a Preparar': abre el wizard de autorizacion."""
        self.ensure_one()
        return {
            'name': 'Volver a Preparar',
            'type': 'ir.actions.act_window',
            'res_model': 'stock.rework.auth.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_review_id': self.id,
                'default_preparation_id': self.preparation_id.id,
                'default_origin': 'review',
            },
        }

    def action_open_scan(self):
        """Bloquea el escaneo en etapas que ya no admiten modificacion."""
        self.ensure_one()
        if self.state in ('done', 'modified', 'cancelled'):
            raise UserError(_('Este repaso ya no admite escaneo.'))
        return super().action_open_scan()
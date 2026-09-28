# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_compare

from ..services.config_cache import get_cached_config


class ReviewMove(models.Model):
    """Linea agrupada por producto del repaso.

    Analogia funcional: review.move <-> stock.move.
    Espejo de la linea de preparacion, con 3 cantidades:
    definida en preparacion, paquetizada, restante por repasar.
    """
    _name = 'review.move'
    _description = 'Linea de Repaso de Stock Picking'
    _order = 'id asc'

    review_id = fields.Many2one(
        'review.stock.picking',
        string='Repaso',
        required=True,
        ondelete='cascade',
        index=True,
    )
    auto_created = fields.Boolean(
        string='Creado automaticamente',
        default=False,
        copy=False,
        help="True cuando la linea fue generada por el sistema en la "
             "validacion de una preparacion. Exime del control de "
             "'repaso manual'.",
    )
    stock_move_id = fields.Many2one(
        'stock.move',
        string='Movimiento Original',
        readonly=True,
        index=True,
        help="stock.move del picking de preparacion origen.",
    )
    product_id = fields.Many2one(
        'product.product',
        string='Producto',
        required=True,
        index=True,
    )
    company_id = fields.Many2one(
        related='review_id.company_id',
        store=True,
        index=True,
    )
    owner_id = fields.Many2one(
        related='review_id.owner_id',
        store=True,
        string='Propietario',
        index=True,
    )

    total_weight = fields.Float(
        string='Peso Total',
        compute='_compute_total_weight',
        digits='Stock Weight',
        help="Peso total de la cantidad repasada.",
    )

    # -- Cantidades --
    qty_preparation = fields.Float(
        string='Cantidad Definida en Preparacion',
        digits='Product Unit of Measure',
        help="Cantidad original del stock.move de preparacion.",
    )
    qty_packaged = fields.Float(
        string='Cantidad Paquetizada',
        compute='_compute_qty_packaged',
        store=True,
        digits='Product Unit of Measure',
    )
    qty_remaining = fields.Float(
        string='Cantidad Restante',
        compute='_compute_qty_packaged',
        store=True,
        digits='Product Unit of Measure',
    )

    # -- Lineas de detalle --
    move_line_ids = fields.One2many(
        'review.move.line',
        'review_move_id',
        string='Detalles por Lote',
    )

    # -- COMPUTED --

    @api.depends('move_line_ids', 'move_line_ids.qty', 'move_line_ids.package_id')
    def _compute_qty_packaged(self):
        """La cantidad paquetizada es la suma de las lineas de detalle que
        tienen un paquete asignado."""
        for move in self:
            packaged = sum(
                move.move_line_ids.filtered('package_id').mapped('qty')
            )
            move.qty_packaged = packaged
            move.qty_remaining = max(move.qty_preparation - packaged, 0.0)

    @api.depends('move_line_ids.weight', 'move_line_ids.state', 'move_line_ids.qty_reviewed')
    def _compute_total_weight(self):
        for move in self:
            # Suma únicamente el peso de las líneas en estado 'done' (Repasadas)
            done_lines = move.move_line_ids.filtered(lambda l: l.state == 'done')
            move.total_weight = sum(done_lines.mapped('weight'))

    # -- CRUD --

    @api.constrains('review_id', 'product_id')
    def _check_product_unique(self):
        """Un producto solo puede aparecer una vez por repaso."""
        for move in self:
            duplicated = move.review_id.move_ids.filtered(
                lambda m: m.product_id == move.product_id and m.id != move.id
            )
            if duplicated:
                raise UserError(_(
                    'El producto %s ya existe en el repaso %s.'
                ) % (move.product_id.display_name, move.review_id.name))

    @api.constrains('qty_preparation')
    def _check_qty_preparation(self):
        for move in self:
            if float_compare(move.qty_preparation, 0.0, precision_digits=4) < 0:
                raise UserError(_('La cantidad definida en preparacion no puede ser negativa.'))

    # -- CONTROL DE CREACION MANUAL --
    @api.model_create_multi
    def create(self, vals_list):
        auto = self.env.context.get('review_auto_create')
        if auto:
            for vals in vals_list:
                vals.setdefault('auto_created', True)
        else:
            to_check = []
            for vals in vals_list:
                rid = vals.get('review_id')
                rev = self.env['review.stock.picking'].browse(rid) if rid else self.env['review.stock.picking']
                if not (rev and rev.auto_created):
                    to_check.append(vals)
            if to_check:
                self._check_manual_create(to_check)
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.context.get('review_auto_create'):
            to_check = self.filtered(
                lambda r: not (r.auto_created or r.review_id.auto_created))
            if to_check:
                self._check_manual_write(vals)
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get('review_auto_create'):
            to_check = self.filtered(
                lambda r: not (r.auto_created or r.review_id.auto_created))
            for move in to_check:
                cfg = get_cached_config(self.env, move.company_id.id)
                if not cfg.manual_review_applies(move.owner_id):
                    raise UserError(_(
                        'No esta habilitada la creacion manual de lineas de '
                        'repaso para este propietario.'
                    ))
        return super().unlink()

    @api.model
    def _check_manual_create(self, vals_list):
        for vals in vals_list:
            review_id = vals.get('review_id')
            if not review_id:
                continue
            review = self.env['review.stock.picking'].browse(review_id)
            cfg = get_cached_config(self.env, review.company_id.id)
            if not cfg.manual_review_applies(review.owner_id):
                raise UserError(_(
                    'No esta habilitada la creacion manual de lineas de '
                    'repaso para este propietario.'
                ))

    def _check_manual_write(self, vals):
        for move in self:
            cfg = get_cached_config(self.env, move.company_id.id)
            if not cfg.manual_review_applies(move.owner_id):
                raise UserError(_(
                    'No esta habilitada la creacion manual de lineas de '
                    'repaso para este propietario.'
                ))

    def _get_config(self):
        self.ensure_one()
        return get_cached_config(self.env, self.company_id.id)

    def action_view_line_form(self):
        """Abre el formulario de la linea en popup (vista emergente)."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Linea: %s' % self.product_id.display_name,
            'res_model': self._name,
            'res_id': self.id,
            'views': [(False, 'form')],
            'target': 'new',
        }

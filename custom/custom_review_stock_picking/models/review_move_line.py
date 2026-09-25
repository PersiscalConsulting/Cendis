# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError

from ..services.config_cache import get_cached_config


class ReviewMoveLine(models.Model):
    """Detalle por lote/owner/cantidad del repaso.

    Analogia funcional: review.move.line <-> stock.move.line.
    Espejo de la linea de escaneo de preparacion, con lote,
    fecha de vencimiento, cantidad y estado visual.
    """
    _name = 'review.move.line'
    _description = 'Detalle de Linea de Repaso'
    _order = 'id asc'

    review_move_id = fields.Many2one(
        'review.move',
        string='Linea de Repaso',
        required=True,
        ondelete='cascade',
        index=True,
    )
    stock_move_line_id = fields.Many2one(
        'stock.move.line',
        string='Linea Original',
        readonly=True,
        index=True,
        help="stock.move.line del picking de preparacion origen.",
    )
    product_id = fields.Many2one(
        related='review_move_id.product_id',
        store=True,
        readonly=True,
        string='Producto',
        index=True,
    )
    company_id = fields.Many2one(
        related='review_move_id.company_id',
        store=True,
        index=True,
    )
    owner_id = fields.Many2one(
        'res.partner',
        string='Propietario',
        index=True,
    )

    # -- Unidad de medida --
    uom_id = fields.Many2one(
        'uom.uom',
        string='Unidad de Medida',
        related='product_id.uom_id',
        readonly=True,
    )

    # -- Ubicaciones --
    location_id = fields.Many2one(
        'stock.location',
        string='Ubicacion Origen',
        related='review_move_id.review_id.preparation_id.location_dest_id',
        readonly=True,
        help="Ubicacion de separacion de la preparacion de origen.",
    )
    location_dest_id = fields.Many2one(
        'stock.location',
        string='Ubicacion Destino',
        related='review_move_id.review_id.preparation_id.location_dest_id',
        readonly=True,
        help="Ubicacion destino (misma que origen en repaso).",
    )

    # -- Detalle por lote --
    lot_id = fields.Many2one(
        'stock.lot',
        string='Lote',
        index=True,
    )
    expiration_date = fields.Datetime(
        string='Fecha de Vencimiento',
        related='lot_id.expiration_date',
        readonly=True,
    )

    # -- Cantidad --
    qty = fields.Float(
        string='Cantidad',
        default=1.0,
        digits='Product Unit of Measure',
    )
    qty_reviewed = fields.Float(
        string='Cantidad Repasada',
        default=0.0,
        digits='Product Unit of Measure',
        help="Cantidad fisicamente verificada durante el repaso.",
    )
    weight = fields.Float(
        string='Peso',
        compute='_compute_weight',
        store=True,
        digits='Stock Weight',
        help="Peso del detalle calculado según el lote o el producto.",
    )

    # -- Marcas de escaneo --
    is_excess = fields.Boolean(
        string='Cantidad en Exceso',
        default=False,
        index=True,
        help="True si la cantidad repasada supera la definida en "
             "preparacion. Se marca visualmente en naranja.",
    )
    is_foreign = fields.Boolean(
        string='Fuera de Preparacion',
        default=False,
        index=True,
        help="True si el lote/producto escaneado no proviene de la "
             "preparacion de origen. Se marca visualmente en rojo.",
    )

    # -- Paquete --
    package_id = fields.Many2one(
        'stock.quant.package',
        string='Paquete',
        index=True,
        help="Paquete asignado. Se crea vacio al paquetizar y se "
             "impacta con quants reales al Finalizar. Solo editable si "
             "la configuracion permite la paquetizacion manual para el "
             "propietario.",
    )
    can_manual_packaging = fields.Boolean(
        string='Puede paquetizar manualmente',
        compute='_compute_can_manual_packaging',
        help="Habilita la edicion del paquete segun la configuracion "
             "del propietario.",
    )
    can_manual_review = fields.Boolean(
        string='Modo manual activo',
        compute='_compute_can_manual_review',
        help="True si el repaso manual esta habilitado para el propietario.",
    )
    auto_created = fields.Boolean(
        string='Creado automaticamente',
        default=False,
        copy=False,
        help="True cuando la linea fue generada por el sistema en la "
             "validacion de una preparacion. Exime del control de "
             "'repaso manual'.",
    )

    @api.depends('owner_id')
    def _compute_can_manual_packaging(self):
        for line in self:
            cfg = get_cached_config(self.env, line.company_id.id)
            line.can_manual_packaging = cfg.manual_review_applies(
                line._get_owner()
            )

    @api.depends('owner_id')
    def _compute_can_manual_review(self):
        for line in self:
            cfg = get_cached_config(self.env, line.company_id.id)
            line.can_manual_review = cfg.manual_review_applies(
                line._get_owner()
            )

    @api.depends('qty', 'product_id', 'product_id.weight', 'lot_id')
    def _compute_weight(self):
        # 1. Agrupar lotes válidos para consultar en batch (evita N+1 queries)
        lot_ids = self.mapped('lot_id').ids
        lote_weights = {}

        if lot_ids:
            sml_env = self.env['stock.move.line'].sudo()
            
            domain_base = [
                ('lot_id', 'in', lot_ids),
                ('x_studio_kilos', '>', 0.0),
            ]
            
            # Consultar líneas históricas de los lotes en orden cronológico
            move_lines = sml_env.search(
                domain_base,
                order='create_date asc, id asc'
            )

            # Mapear Lote -> Peso unitario prioritario (preferir incoming + done)
            for sml in move_lines:
                lot_id = sml.lot_id.id
                if lot_id in lote_weights and lote_weights[lot_id]['is_incoming_done']:
                    continue

                is_incoming_done = (sml.picking_code == 'incoming' and sml.state == 'done')
                qty_origin = getattr(sml, 'qty_done', 0.0) or getattr(sml, 'quantity', 0.0) or 1.0
                unit_weight = sml.x_studio_kilos / qty_origin if qty_origin > 0 else sml.x_studio_kilos

                lote_weights[lot_id] = {
                    'unit_weight': unit_weight,
                    'is_incoming_done': is_incoming_done,
                }

        # 2. Asignar el peso en memoria a cada línea
        for line in self:
            lot_data = lote_weights.get(line.lot_id.id) if line.lot_id else None
            
            if lot_data and lot_data['unit_weight'] > 0:
                unit_weight = lot_data['unit_weight']
            else:
                # Fallback seguro: Peso maestro definido en la ficha del producto
                unit_weight = line.product_id.weight or 0.0

            line.weight = (line.qty or 0.0) * unit_weight

    # -- Estado visual --
    state = fields.Selection(
        [
            ('pending', 'Pendiente'),
            ('done', 'Repasado'),
        ],
        string='Estado',
        default='pending',
        index=True,
    )

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
                move_id = vals.get('review_move_id')
                move = self.env['review.move'].browse(move_id) if move_id else self.env['review.move']
                if not (move and (move.auto_created or move.review_id.auto_created)):
                    to_check.append(vals)
            if to_check:
                self._check_manual_create(to_check)
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.context.get('review_auto_create'):
            to_check = self.filtered(
                lambda l: not (l.auto_created
                               or l.review_move_id.auto_created
                               or l.review_move_id.review_id.auto_created))
            if to_check:
                self._check_manual_write(vals)
        return super().write(vals)

    def unlink(self):
        if not self.env.context.get('review_auto_create'):
            to_check = self.filtered(
                lambda l: not (l.auto_created
                               or l.review_move_id.auto_created
                               or l.review_move_id.review_id.auto_created))
            for line in to_check:
                cfg = get_cached_config(self.env, line.company_id.id)
                if not cfg.manual_review_applies(line._get_owner()):
                    raise UserError(_(
                        'No esta habilitada la creacion manual de lineas de '
                        'repaso para este propietario.'
                    ))
        return super().unlink()

    @api.model
    def _check_manual_create(self, vals_list):
        for vals in vals_list:
            move_id = vals.get('review_move_id')
            if not move_id:
                continue
            move = self.env['review.move'].browse(move_id)
            cfg = get_cached_config(self.env, move.company_id.id)
            if not cfg.manual_review_applies(move.owner_id):
                raise UserError(_(
                    'No esta habilitada la creacion manual de lineas de '
                    'repaso para este propietario.'
                ))

    def _check_manual_write(self, vals):
        for line in self:
            cfg = get_cached_config(self.env, line.company_id.id)
            owner = line._get_owner()
            if not cfg.manual_review_applies(owner):
                raise UserError(_(
                    'No esta habilitado el repaso manual para este propietario.'
                ))

    def _get_owner(self):
        return self.review_move_id.owner_id

    def _get_config(self):
        self.ensure_one()
        return get_cached_config(self.env, self.company_id.id)

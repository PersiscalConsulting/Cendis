# -*- coding: utf-8 -*-
from odoo import api, fields, models


class OwnerMoveStockLine(models.Model):
    _name = 'owner.move.stock.line'
    _description = 'Linea resumen de Movimiento entre Propietarios'

    move_id = fields.Many2one(
        'owner.move.stock',
        string='Movimiento',
        required=True,
        ondelete='cascade',
    )
    product_id = fields.Many2one(
        'product.product',
        string='Producto',
        required=True,
    )
    uom_id = fields.Many2one(
        'uom.uom',
        string='Unidad de Medida',
        related='product_id.uom_id',
        readonly=True,
    )

    # -- Totales calculados a partir de los detail_ids --
    qty = fields.Float(
        string='Cantidad Preparada',
        compute='_compute_summary',
        store=True,
    )
    packed_qty = fields.Float(
        string='Cantidad Empaquetada',
        compute='_compute_summary',
        store=True,
    )
    package_count = fields.Integer(
        string='Paquetes',
        compute='_compute_summary',
        store=True,
    )

    detail_ids = fields.One2many(
        'owner.move.stock.line.detail',
        'line_id',
        string='Detalles',
    )
    location_ids = fields.Many2many(
        'stock.location',
        compute='_compute_location_ids',
        string='Ubicaciones',
    )

    # -- Propietarios heredados del movimiento padre --
    owner_id = fields.Many2one(
        'res.partner',
        string='Propietario Origen',
        related='move_id.owner_id',
        readonly=True,
        store=True,
    )
    dest_owner_id = fields.Many2one(
        'res.partner',
        string='Propietario Destino',
        related='move_id.dest_owner_id',
        readonly=True,
        store=True,
    )

    @api.depends('detail_ids', 'detail_ids.qty', 'detail_ids.package_id')
    def _compute_summary(self):
        """Agrega cantidades y paquetes desde los detalles hacia arriba."""
        for line in self:
            line.qty = sum(line.detail_ids.mapped('qty'))
            line.packed_qty = sum(line.detail_ids.filtered('package_id').mapped('qty'))
            line.package_count = len(line.detail_ids.mapped('package_id'))

    @api.depends('detail_ids', 'detail_ids.location_id')
    def _compute_location_ids(self):
        """Obtiene las ubicaciones unicas de todos los detalles."""
        for line in self:
            line.location_ids = line.detail_ids.mapped('location_id')


class OwnerMoveStockLineDetail(models.Model):
    """Detalle individual de escaneo: un producto/lote en una ubicacion."""
    _name = 'owner.move.stock.line.detail'
    _description = 'Detalle de escaneo de Movimiento entre Propietarios'
    _order = 'id desc'

    move_id = fields.Many2one(
        'owner.move.stock',
        string='Movimiento',
        required=True,
        ondelete='cascade',
    )
    line_id = fields.Many2one(
        'owner.move.stock.line',
        string='Linea',
        required=True,
        ondelete='cascade',
    )
    product_id = fields.Many2one(
        'product.product',
        string='Producto',
        required=True,
    )
    location_id = fields.Many2one(
        'stock.location',
        string='Ubicacion',
        required=True,
        domain=[('usage', '=', 'internal')],
    )
    lot_id = fields.Many2one(
        'stock.lot',
        string='Lote/Numero de Serie',
    )
    quant_id = fields.Many2one(
        'stock.quant',
        string='Quant de Origen',
        readonly=True,
    )

    # -- Paquete y empaquetado --
    package_id = fields.Many2one(
        'stock.quant.package',
        string='Paquete',
    )
    packed = fields.Boolean(
        string='Empaquetado',
        compute='_compute_packed',
        store=True,
    )
    pack_seq = fields.Integer(
        string='Secuencia de Empaquetado',
        default=0,
    )

    # -- Datos del producto escaneado --
    expiration_date = fields.Datetime(
        string='Fecha de Vencimiento',
    )
    qty = fields.Float(
        string='Cantidad',
        default=1.0,
    )
    uom_id = fields.Many2one(
        'uom.uom',
        string='Unidad de Medida',
        related='product_id.uom_id',
        readonly=True,
    )
    weight = fields.Float(
        string='Peso',
    )

    # -- Propietarios --
    owner_id = fields.Many2one(
        'res.partner',
        string='Propietario Origen',
        related='quant_id.owner_id',
        readonly=True,
    )
    dest_owner_id = fields.Many2one(
        'res.partner',
        string='Propietario Destino',
        related='move_id.dest_owner_id',
        readonly=True,
        store=True,
    )

    @api.depends('package_id')
    def _compute_packed(self):
        """Un detalle se considera empaquetado si tiene package_id asignado."""
        for detail in self:
            detail.packed = bool(detail.package_id)

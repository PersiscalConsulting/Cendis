# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_compare


class StockQuantReservation(models.Model):
    """Reserva de Inventario por Preparacion.

    Entidad unica que fluye por todo el ciclo de vida:
      1. Se crea al escaneo de preparacion (bloquea stock en origen).
      2. Persiste durante validacion y repaso.
      3. Se libera al generar la expedicion (action_finish).

    Verifica por:
      - Quant especifico (productos con lote, en origen).
      - Cantidades (productos sin lote, en separacion):
        disponible = total_en_ubicacion - reservas_ajenas.
    """
    _name = 'stock.quant.reservation'
    _description = 'Reserva de Inventario'
    _order = 'create_date desc, id desc'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    preparation_id = fields.Many2one(
        'preparation.stock',
        string='Preparacion',
        required=True,
        ondelete='cascade',
        index=True,
    )
    company_id = fields.Many2one(
        related='preparation_id.company_id',
        store=True,
        index=True,
    )
    product_id = fields.Many2one(
        'product.product',
        string='Producto',
        required=True,
        index=True,
    )
    lot_id = fields.Many2one(
        'stock.lot',
        string='Lote',
        index=True,
    )
    owner_id = fields.Many2one(
        'res.partner',
        string='Propietario',
        index=True,
    )
    qty = fields.Float(
        string='Cantidad Reservada',
        required=True,
        digits='Product Unit of Measure',
        help="Cantidad escaneada y reservada por esta preparacion.",
    )
    state = fields.Selection(
        [
            ('reserved', 'Reservado'),
            ('released', 'Liberado'),
        ],
        string='Estado',
        default='reserved',
        required=True,
        index=True,
    )
    release_reason = fields.Selection(
        [
            ('expedition', 'Expedicion generada'),
            ('cancel', 'Cancelacion de preparacion'),
            ('archive', 'Archivo de preparacion'),
            ('manual', 'Liberacion manual'),
        ],
        string='Razon de liberacion',
        readonly=True,
    )
    user_id = fields.Many2one(
        'res.users',
        string='Reservado por',
        default=lambda self: self.env.user,
        readonly=True,
    )
    create_date = fields.Datetime(
        string='Fecha de creacion',
        readonly=True,
    )
    release_date = fields.Datetime(
        string='Fecha de liberacion',
        readonly=True,
    )

    _sql_constraints = [
        (
            'reservation_unique_per_prep',
            'unique(preparation_id, product_id, lot_id, owner_id, state) '
            'WHERE state = \'reserved\'',
            'Ya existe una reserva activa para este producto/lote/propietario '
            'en esta preparacion.',
        ),
    ]

    def release(self, reason='manual'):
        """Libera la reserva."""
        for rec in self:
            if rec.state != 'reserved':
                continue
            rec.write({
                'state': 'released',
                'release_reason': reason,
                'release_date': fields.Datetime.now(),
            })

    def action_release_manual(self):
        """Boton de liberacion manual desde la vista."""
        self.ensure_one()
        if self.state != 'reserved':
            raise UserError(_('Esta reserva ya fue liberada.'))
        self.release(reason='manual')
        return True

    @api.model
    def _check_conflict(self, product_id, lot_id, owner_id, preparation_id,
                        location_id=False, qty=1.0):
        """Verifica si escanear qty unidades bloquearia el stock.

        Calcula la cantidad disponible (stock en ubicacion - reservas de
        otras preps) y bloquea solo si la cantidad solicitada la supera.
        Retorna: string con mensaje de error si hay conflicto, False si no.
        """
        if not location_id:
            return False
        available = self._get_available_for_prep(
            product_id, lot_id, owner_id, preparation_id, location_id,
        )
        if float_compare(qty, available, precision_digits=4) <= 0:
            return False
        product = self.env['product.product'].browse(product_id)
        lot = self.env['stock.lot'].browse(lot_id) if lot_id else False
        lot_name = lot.name if lot else 'Sin lote'
        location = self.env['stock.location'].browse(location_id)
        other_res = self.sudo().search([
            ('product_id', '=', product_id),
            ('lot_id', '=', lot_id or False),
            ('owner_id', '=', owner_id or False),
            ('state', '=', 'reserved'),
            ('preparation_id', '!=', preparation_id),
        ])
        if other_res:
            preparaciones = '\n'.join(
                '  - %s (usuario: %s, reservado: %s)'
                % (r.preparation_id.name, r.user_id.name or '-',
                   round(r.qty, 4))
                for r in other_res
            )
            return _(
                'Stock insuficiente de %s (lote %s) en %s.\n'
                'Solicitado: %s | Disponible: %s.\n'
                'Reservado por otras preparaciones:\n%s'
            ) % (
                product.display_name, lot_name,
                location.complete_name, round(qty, 4),
                round(available, 4), preparaciones,
            )
        return _(
            'Stock insuficiente de %s (lote %s) en %s.\n'
            'Solicitado: %s | Disponible: %s.'
        ) % (
            product.display_name, lot_name,
            location.complete_name, round(qty, 4),
            round(available, 4),
        )

    @api.model
    def _sync_reservation(self, preparation, product_id, lot_id=False,
                          owner_id=False):
        """Recalcula la reserva para un producto/lote en una preparacion.

        Lee todos los scan_line_ids del producto/lote en la preparacion
        y ajusta (crea/actualiza/libera) la reserva para que refleje
        la cantidad escaneada real. Ubicacion-agnostic: la reserva
        cubre el producto/lote/owner sin importar la ubicacion.
        """
        cfg = preparation._get_config()
        if not cfg.reservation_applies(preparation.owner_id):
            return
        StockReservation = self.env['stock.quant.reservation'].sudo()
        total_scanned = sum(
            s.qty for s in preparation.scan_line_ids
            if s.product_id.id == product_id
            and (s.lot_id.id if s.lot_id else False) == (lot_id or False)
        )
        reservation = StockReservation.search([
            ('preparation_id', '=', preparation.id),
            ('product_id', '=', product_id),
            ('lot_id', '=', lot_id or False),
            ('owner_id', '=', owner_id or False),
            ('state', '=', 'reserved'),
        ], limit=1)
        if float_compare(total_scanned, 0.0, precision_digits=4) <= 0:
            if reservation:
                reservation.release(reason='manual')
            return
        if reservation:
            reservation.write({'qty': total_scanned})
        else:
            StockReservation.create({
                'preparation_id': preparation.id,
                'product_id': product_id,
                'lot_id': lot_id or False,
                'owner_id': owner_id or False,
                'qty': total_scanned,
            })

    @api.model
    def _get_available_for_prep(self, product_id, lot_id, owner_id,
                                preparation_id, location_id):
        """Calcula la cantidad disponible para una preparacion en una ubicacion.

        Disponible = stock total en ubicacion - reservas de OTRAS preparaciones.

        Cuenta quants sueltos Y empaquetados: tras el re-proceso la reversa
        preserva los paquetes en destino, por lo que limitarse a stock suelto
        subestimaria la disponibilidad real.
        """
        quant_model = self.env['stock.quant'].sudo()
        q_domain = [
            ('product_id', '=', product_id),
            ('location_id', '=', location_id),
            ('quantity', '!=', 0.0),
            ('lot_id', '=', lot_id or False),
        ]
        if owner_id:
            q_domain.append(('owner_id', '=', owner_id))
        quants = quant_model.search(q_domain)
        available = (
            sum(quants.mapped('quantity'))
            - sum(quants.mapped('reserved_quantity'))
        )
        domain = [
            ('product_id', '=', product_id),
            ('lot_id', '=', lot_id or False),
            ('owner_id', '=', owner_id or False),
            ('state', '=', 'reserved'),
            ('preparation_id', '!=', preparation_id),
        ]
        other_reservations = sum(
            self.sudo().search(domain).mapped('qty')
        )
        disponible = available - other_reservations
        return max(disponible, 0.0)

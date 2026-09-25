# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_compare

from ..services.validation_engine import PreparationValidationEngine

EPS = 0.0001


class PreparationStockScanLine(models.Model):
    """Detalle individual de escaneo.

    Analogia funcional: preparation.stock.scan.line <-> stock.move.line.
    Al validar se ejecutan como move lines espejo (1 a 1).
    """
    _name = 'preparation.stock.scan.line'
    _description = 'Detalle de Escaneo de Preparacion de Stock'
    _order = 'id desc'

    preparation_id = fields.Many2one(
        related='preparation_line_id.preparation_id',
        store=True,
        index=True,
    )
    location_dest_id = fields.Many2one(
        'stock.location',
        related='preparation_id.location_dest_id',
        readonly=True,
        store=False,
    )
    # Mirror fields para compatibilidad con widgets enterprise (pick_from)
    owner_id = fields.Many2one('res.partner', related='preparation_id.owner_id', readonly=True, store=False)
    product_uom_qty = fields.Float(related='qty', readonly=True, store=False)
    quantity = fields.Float(related='qty', readonly=True, store=False)
    qty_done = fields.Float(related='qty', readonly=True, store=False)
    result_package_id = fields.Many2one(
        'stock.quant.package',
        compute='_compute_pick_mirrors',
        readonly=True,
        store=False,
    )
    picking_id = fields.Many2one(
        'stock.picking',
        compute='_compute_pick_mirrors',
        readonly=True,
        store=False,
    )
    move_id = fields.Many2one('stock.move', related='stock_move_id', readonly=True, store=False)
    state = fields.Selection(
        [('draft', 'Draft'), ('confirmed', 'Waiting'), ('assigned', 'Ready'),
         ('in_transit', 'In Transit'), ('done', 'Done'), ('cancel', 'Cancelled')],
        related='stock_move_id.state',
        readonly=True,
        store=False,
    )
    is_done = fields.Boolean(
        compute='_compute_pick_mirrors',
        readonly=True,
        store=False,
    )
    date = fields.Datetime(related='scan_datetime', readonly=True, store=False)
    in_date = fields.Datetime(related='scan_datetime', readonly=True, store=False)
    reference = fields.Char(related='preparation_id.name', readonly=True, store=False)
    preparation_line_id = fields.Many2one(
        'preparation.stock.line',
        string='Linea',
        required=True,
        ondelete='cascade',
        index=True,
    )
    company_id = fields.Many2one(
        related='preparation_id.company_id',
        store=True,
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
    scan_weight = fields.Float(
        string='Peso',
        compute='_compute_scan_weight',
        digits='Stock Weight',
        help="Peso del escaneo (qty * peso del producto).",
    )
    expiration_date = fields.Datetime(
        string='Vencimiento',
        related='lot_id.expiration_date',
        readonly=True,
        store=False,
    )
    location_id = fields.Many2one(
        'stock.location',
        string='Ubicacion Real',
        required=True,
        domain=[('usage', '=', 'internal')],
        index=True,
    )
    lot_id = fields.Many2one(
        'stock.lot',
        string='Lote/Numero de Serie',
        index=True,
    )
    package_id = fields.Many2one(
        'stock.quant.package',
        string='Paquete',
    )
    quant_id = fields.Many2one(
        'stock.quant',
        string='Quant de Origen',
        help='Cuant de origen al momento del escaneo (trazabilidad). '
             'Puede quedar apuntando a la ubicacion original cuando el stock '
             'ya fue trasladado a separacion en una iteracion posterior; la '
             'logica de disponibilidad usa location_id, no este campo.',
    )
    stock_move_id = fields.Many2one(
        'stock.move',
        string='Stock Move',
        readonly=True,
        index=True,
        copy=False,
        ondelete='set null',
        help='Move generado al validar la preparacion.',
    )
    stock_move_line_id = fields.Many2one(
        'stock.move.line',
        string='Stock Move Line',
        readonly=True,
        index=True,
        copy=False,
        ondelete='set null',
        help='Linea de movimiento espejo de este escaneo.',
    )
    qty = fields.Float(
        string='Cantidad',
        default=1.0,
        digits='Product Unit of Measure',
    )
    user_id = fields.Many2one(
        'res.users',
        string='Escaneado por',
        default=lambda self: self.env.user,
        readonly=True,
    )
    scan_datetime = fields.Datetime(
        string='Fecha de Escaneo',
        default=fields.Datetime.now,
        readonly=True,
    )
    is_expired = fields.Boolean(
        string='Lote vencido',
        compute='_compute_is_expired',
        store=True,
    )

    # COMPUTED
    # ==================================================================

    @api.depends('lot_id', 'lot_id.expiration_date')
    def _compute_is_expired(self):
        now = fields.Datetime.now()
        for scan in self:
            if scan.lot_id and scan.lot_id.expiration_date:
                scan.is_expired = scan.lot_id.expiration_date < now
            else:
                scan.is_expired = False

    @api.depends('qty', 'product_id', 'product_id.weight', 'lot_id')
    def _compute_scan_weight(self):
        # 1. Agrupar lotes válidos para consultar en batch (evita N+1 queries)
        lot_ids = self.mapped('lot_id').ids
        lote_weights = {}

        if lot_ids:
            sml_env = self.env['stock.move.line'].sudo()
            
            # Traer de una sola vez todas las líneas de entrada de los lotes presentes
            domain_base = [
                ('lot_id', 'in', lot_ids),
                ('x_studio_kilos', '>', 0.0),
            ]
            
            # Búsqueda batch en orden cronológico
            move_lines = sml_env.search(
                domain_base,
                order='create_date asc, id asc'
            )

            # Mapear lote -> peso unitario prioritario (prefiere incoming + done)
            for sml in move_lines:
                lot_id = sml.lot_id.id
                # Si el lote ya fue asignado con una recepción "done", no lo sobreescribimos
                if lot_id in lote_weights and lote_weights[lot_id]['is_incoming_done']:
                    continue

                is_incoming_done = (sml.picking_code == 'incoming' and sml.state == 'done')
                qty_origin = getattr(sml, 'qty_done', 0.0) or getattr(sml, 'quantity', 0.0) or 1.0
                unit_weight = sml.x_studio_kilos / qty_origin if qty_origin > 0 else sml.x_studio_kilos

                lote_weights[lot_id] = {
                    'unit_weight': unit_weight,
                    'is_incoming_done': is_incoming_done,
                }

        # 2. Asignar el peso a cada registro en memoria
        for scan in self:
            lot_data = lote_weights.get(scan.lot_id.id) if scan.lot_id else None
            
            if lot_data and lot_data['unit_weight'] > 0:
                unit_weight = lot_data['unit_weight']
            else:
                # Fallback seguro: peso configurado en la ficha del producto
                unit_weight = scan.product_id.weight or 0.0

            scan.scan_weight = (scan.qty or 0.0) * unit_weight

    # PICK_FROM (patron stock.move.line)
    # ==================================================================

    @api.depends('stock_move_id', 'stock_move_id.state')
    def _compute_pick_mirrors(self):
        """Mirrors computed store=False: existen solo para satisfacer la
        sub-spec de lectura del widget pick_from (sin columnas en DB)."""
        for scan in self:
            scan.result_package_id = False
            scan.picking_id = False
            scan.is_done = bool(
                scan.stock_move_id and scan.stock_move_id.state == 'done'
            )

    @api.onchange('quant_id')
    def _onchange_quant_id(self):
        """Al elegir un quant desde el picker completa la fila de escaneo,
        limitando la cantidad al restante de la demanda de la linea."""
        for scan in self:
            quant = scan.quant_id
            if not quant:
                continue
            scan.product_id = quant.product_id
            scan.location_id = quant.location_id
            scan.lot_id = quant.lot_id
            scan.package_id = quant.package_id
            line = scan.preparation_line_id
            if line:
                others = sum(
                    s.qty for s in line.scan_line_ids if s.id != scan.id
                )
                remaining = max(line.qty_demanded - others, 0.0)
                scan.qty = min(quant.quantity, remaining)
            else:
                scan.qty = quant.quantity

    # VALIDACIONES BLOQUEANTES
    # ==================================================================

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            PreparationValidationEngine.validate_scan_line_create(self, vals, self.env.context)
        lines = super().create(vals_list)
        lines._auto_sync_demand()
        PreparationValidationEngine.validate_scan_line_operations(lines)
        PreparationValidationEngine.check_quant_availability_optimized(lines)
        StockReservation = self.env['stock.quant.reservation'].sudo()
        synced = set()
        for scan in lines:
            prep = scan.preparation_id
            key = (prep.id, scan.product_id.id,
                   scan.lot_id.id if scan.lot_id else False,
                   prep.owner_id.id if prep.owner_id else False)
            if key not in synced:
                StockReservation._sync_reservation(
                    prep, scan.product_id.id,
                    scan.lot_id.id if scan.lot_id else False,
                    prep.owner_id.id if prep.owner_id else False,
                )
                synced.add(key)
        return lines

    def _create_delta_scan(self, delta, location=False):
        """Crea un renglon nuevo con la cantidad delta de un escaneo dado.

        En re-proceso se usa para registrar unidades adicionales de un renglon
        ya trasladado a la separacion: el stock extra debe pasar por una nueva
        validacion para llegar a la separacion. El renglon original queda
        intacto.
        """
        self.ensure_one()
        if not location and self.preparation_id.current_scan_location_id:
            location = self.preparation_id.current_scan_location_id
        location = location or self.location_id
        return self.env['preparation.stock.scan.line'].create({
            'preparation_line_id': self.preparation_line_id.id,
            'product_id': self.product_id.id,
            'location_id': location.id if location else False,
            'lot_id': self.lot_id.id if self.lot_id else False,
            'package_id': self.package_id.id if self.package_id else False,
            'quant_id': self.quant_id.id if self.quant_id else False,
            'qty': delta,
        })

    def write(self, vals):
        # En re-proceso, subir la cantidad de un renglon ya trasladado a la
        # separacion (desde el form o cualquier write directo) se deriva como
        # renglon nuevo (delta), para que la validacion lo transporte a la
        # separacion. El renglon movido queda intacto; las bajas y los cambios
        # de otros campos siguen bloqueados por validate_scan_line_write.
        if not self.env.context.get('excess_wizard') and 'qty' in vals \
                and set(vals.keys()) - {'qty', 'id'} == set():
            try:
                new_qty = float(vals['qty'])
            except (TypeError, ValueError):
                new_qty = -1.0
            handled = self.env['preparation.stock.scan.line']
            for scan in self:
                if not (getattr(scan.preparation_id, 'is_rework', False)
                        and scan.stock_move_id):
                    continue
                if float_compare(new_qty, scan.qty, precision_digits=4) > 0:
                    delta = round(new_qty - scan.qty, 4)
                    if delta > 0:
                        scan._create_delta_scan(
                            delta,
                            location=(scan.preparation_id.current_scan_location_id
                                      or scan.location_id),
                        )
                    handled |= scan
                elif float_compare(new_qty, scan.qty, precision_digits=4) == 0:
                    handled |= scan
            if handled and len(handled) == len(self):
                rest = {k: v for k, v in vals.items() if k != 'qty'}
                return super().write(rest)
        PreparationValidationEngine.validate_scan_line_write(self, vals, self.env.context)
        affected = set()
        for scan in self:
            affected.add((
                scan.preparation_id,
                scan.product_id.id,
                scan.lot_id.id if scan.lot_id else False,
                scan.preparation_id.owner_id.id if scan.preparation_id.owner_id else False,
            ))
        res = super().write(vals)
        if {'qty', 'location_id', 'lot_id', 'preparation_line_id'} & set(vals.keys()):
            self._auto_sync_demand()
            PreparationValidationEngine.validate_scan_line_operations(self)
            PreparationValidationEngine.check_quant_availability_optimized(self)
        StockReservation = self.env['stock.quant.reservation'].sudo()
        for prep, prod_id, lot_id, owner_id in affected:
            StockReservation._sync_reservation(prep, prod_id, lot_id, owner_id)
        for scan in self:
            key = (
                scan.preparation_id,
                scan.product_id.id,
                scan.lot_id.id if scan.lot_id else False,
                scan.preparation_id.owner_id.id if scan.preparation_id.owner_id else False,
            )
            if key not in affected:
                StockReservation._sync_reservation(
                    scan.preparation_id, scan.product_id.id,
                    scan.lot_id.id if scan.lot_id else False,
                    scan.preparation_id.owner_id.id if scan.preparation_id.owner_id else False,
                )
                affected.add(key)
        return res

    def unlink(self):
        PreparationValidationEngine.validate_scan_line_unlink(self, self.env.context)
        affected = set()
        for scan in self:
            affected.add((
                scan.preparation_id,
                scan.product_id.id,
                scan.lot_id.id if scan.lot_id else False,
                scan.preparation_id.owner_id.id if scan.preparation_id.owner_id else False,
            ))
        res = super().unlink()
        StockReservation = self.env['stock.quant.reservation'].sudo()
        for prep, prod_id, lot_id, owner_id in affected:
            StockReservation._sync_reservation(prep, prod_id, lot_id, owner_id)
        return res

    def _manual_test_allowed(self):
        """Autorizado solo para testing manual (contexto) y propietarios habilitados."""
        prep = self.preparation_id
        cfg = prep._get_config()
        return self.env.context.get('prep_manual_add') and cfg.manual_scan_create_applies(prep.owner_id)

    def _auto_sync_demand(self):
        """Herramienta pick_from: la linea acompaña los escaneos
        agregados desde quants, subiendo la demanda y sumando ubicaciones.
        """
        cfg = self.preparation_id._get_config()
        if not cfg.manual_scan_create_applies(self.preparation_id.owner_id):
            return
        if not self.env.context.get('scan_auto_sync_demand'):
            return
        ctx = dict(self.env.context, prep_manual_add=True)
        for scan in self:
            line = scan.preparation_line_id
            if not line or not scan.quant_id:
                continue
            total_scanned = sum(line.scan_line_ids.mapped('qty'))
            if float_compare(total_scanned, line.qty_demanded,
                             precision_digits=4) > 0:
                line.with_context(**ctx).write({'qty_demanded': total_scanned})
            missing = line.scan_line_ids.mapped('location_id').filtered(
                lambda loc: loc not in line.location_ids
            )
            if missing:
                new_ids = list(set(line.location_ids.ids) | set(missing.ids))
                line.with_context(**ctx).write({
                    'location_ids': [(6, 0, new_ids)],
                    'location_manual': True,
                })
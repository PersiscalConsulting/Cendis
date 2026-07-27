# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class StockInventoryAudit(models.Model):
    _name = 'stock.inventory.audit'
    _description = 'Auditoria de Inventario a Ciegas'
    _order = 'date desc, id desc'

    name = fields.Char(
        string='Referencia',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('Nuevo'),
    )
    location_id = fields.Many2one(
        'stock.location',
        string='Primera Ubicacion',
        required=False,
        domain=[('usage', '=', 'internal')],
        readonly=True,
        help='Primera ubicacion escaneada. Campo legacy de compatibilidad.',
    )
    current_scan_location_id = fields.Many2one(
        'stock.location',
        string='Ubicacion Activa de Escaneo',
        domain=[('usage', '=', 'internal')],
        readonly=True,
    )
    scanned_location_ids = fields.Many2many(
        'stock.location',
        compute='_compute_scanned_location_ids',
        store=True,
        string='Ubicaciones Escaneadas',
    )
    user_id = fields.Many2one(
        'res.users',
        string='Auditor',
        default=lambda self: self.env.user,
        readonly=True,
    )
    date = fields.Datetime(
        string='Fecha',
        default=fields.Datetime.now,
        readonly=True,
    )
    state = fields.Selection(
        [
            ('draft', 'Borrador'),
            ('progress', 'En Proceso'),
            ('review', 'Por Revisar'),
            ('done', 'Finalizado y Aplicado'),
        ],
        string='Estado',
        default='draft',
        tracking=True,
    )
    line_ids = fields.One2many(
        'stock.inventory.audit.line',
        'audit_id',
        string='Lineas de Auditoria',
    )
    scanned_qty = fields.Integer(
        string='Items Escaneados',
        compute='_compute_scanned_qty',
    )
    error_summary_ids = fields.One2many(
        'stock.inventory.audit.error.summary',
        'audit_id',
        string='Errores por Producto/Lote',
    )

    @api.depends('line_ids')
    def _compute_scanned_qty(self):
        for audit in self:
            audit.scanned_qty = len(audit.line_ids)

    @api.depends('line_ids', 'line_ids.location_id')
    def _compute_scanned_location_ids(self):
        for audit in self:
            audit.scanned_location_ids = audit.line_ids.mapped('location_id')

    @api.model
    def create(self, vals):
        if vals.get('name', _('Nuevo')) == _('Nuevo'):
            vals['name'] = self.env['ir.sequence'].next_by_code(
                'stock.inventory.audit'
            ) or _('Nuevo')
        return super().create(vals)

    def action_start_audit(self):
        pass

    def action_barcode_scan(self):
        self.ensure_one()
        if self.state == 'draft':
            self.state = 'progress'
        action = self.env.ref(
            'custom_stock_control_barcode.action_audit_barcode_owl'
        ).read()[0]
        action['context'] = {
            'default_audit_id': self.id,
            'active_id': self.id,
        }
        return action

    @api.model
    def process_barcode_scan(self, audit_id, barcode):
        audit = self.browse(audit_id)
        if not audit.exists():
            return {'error': 'Auditoria no encontrada.'}

        if audit.state != 'progress':
            return {'error': 'La auditoria no esta en estado de escaneo.'}

        location = self.env['stock.location'].search([
            ('barcode', '=', barcode),
            ('usage', '=', 'internal'),
        ], limit=1)
        if location:
            audit.current_scan_location_id = location.id
            if not audit.location_id:
                audit.location_id = location.id
            return {
                'message': 'Ubicacion cambiada a: %s' % location.complete_name,
                'location_id': location.id,
                'location_name': location.complete_name,
                'is_location': True,
            }

        if not audit.current_scan_location_id:
            return {'error': 'Primero escanee una ubicacion.'}

        lot = self.env['stock.lot'].search([
            '|',
            ('name', '=', barcode),
            ('ref', '=', barcode),
        ], limit=1)

        if lot:
            return self._process_lot(audit, lot)

        product = self.env['product.product'].search([
            '|',
            ('barcode', '=', barcode),
            ('default_code', '=', barcode),
        ], limit=1)

        if product:
            return self._process_product(audit, product)

        return {'error': 'Codigo no reconocido: %s' % barcode}

    def _get_theoretical_qty(self, product_id, lot_id=False, location_id=False):
        self.ensure_one()
        loc_id = location_id or self.current_scan_location_id.id
        if not loc_id:
            return 0.0
        domain = [
            ('location_id', '=', loc_id),
            ('product_id', '=', product_id),
        ]
        if lot_id:
            domain.append(('lot_id', '=', lot_id))
        quants = self.env['stock.quant'].search(domain)
        return sum(quants.mapped('quantity')) if quants else 0.0

    def _process_lot(self, audit, lot):
        existing_line = audit.line_ids.filtered(
            lambda l: l.product_id == lot.product_id
            and l.lot_id == lot
            and l.location_id == audit.current_scan_location_id
        )
        if existing_line:
            return {
                'error': 'El lote %s ya fue escaneado en esta ubicacion.' % lot.name,
            }
        theoretical_qty = audit._get_theoretical_qty(
            lot.product_id.id, lot.id,
            location_id=audit.current_scan_location_id.id,
        )
        audit.line_ids = [(0, 0, {
            'product_id': lot.product_id.id,
            'lot_id': lot.id,
            'location_id': audit.current_scan_location_id.id,
            'theoretical_qty': theoretical_qty,
            'counted_qty': 1.0,
        })]
        return {
            'product_id': lot.product_id.id,
            'product_name': lot.product_id.display_name,
            'lot_name': lot.name,
            'scanned_qty': 1.0,
            'theoretical_qty': theoretical_qty,
            'location_id': audit.current_scan_location_id.id,
            'location_name': audit.current_scan_location_id.complete_name,
        }

    def _process_product(self, audit, product):
        existing_line = audit.line_ids.filtered(
            lambda l: l.product_id == product
            and not l.lot_id
            and l.location_id == audit.current_scan_location_id
        )
        theoretical_qty = audit._get_theoretical_qty(
            product.id,
            location_id=audit.current_scan_location_id.id,
        )
        if existing_line:
            existing_line.counted_qty += 1.0
            scanned_qty = existing_line.counted_qty
            theoretical_qty = existing_line.theoretical_qty
        else:
            audit.line_ids = [(0, 0, {
                'product_id': product.id,
                'location_id': audit.current_scan_location_id.id,
                'theoretical_qty': theoretical_qty,
                'counted_qty': 1.0,
            })]
            scanned_qty = 1.0
        return {
            'product_id': product.id,
            'product_name': product.display_name,
            'scanned_qty': scanned_qty,
            'theoretical_qty': theoretical_qty,
            'location_id': audit.current_scan_location_id.id,
            'location_name': audit.current_scan_location_id.complete_name,
        }

    def action_cancel_scan(self):
        self.ensure_one()
        if self.state != 'progress':
            raise UserError(_('Solo se puede cancelar una auditoria en progreso.'))
        self.line_ids.unlink()
        self.error_summary_ids.unlink()
        self.location_id = False
        self.current_scan_location_id = False
        self.state = 'draft'

    def action_finish_scan(self):
        self.ensure_one()
        if self.state != 'progress':
            raise UserError(_('La auditoria debe estar en progreso.'))
        if not self.line_ids:
            raise UserError(_('No hay lineas escaneadas para finalizar.'))

        scanned_keys = set()
        for line in self.line_ids:
            key = (
                line.product_id.id,
                line.lot_id.id if line.lot_id else False,
                line.location_id.id,
            )
            scanned_keys.add(key)

        locations = self.line_ids.mapped('location_id')
        quants = self.env['stock.quant'].search([
            ('location_id', 'in', locations.ids),
            ('quantity', '>', 0),
        ])

        new_lines = []
        for quant in quants:
            key = (
                quant.product_id.id,
                quant.lot_id.id if quant.lot_id else False,
                quant.location_id.id,
            )
            if key not in scanned_keys:
                new_lines.append((0, 0, {
                    'product_id': quant.product_id.id,
                    'lot_id': quant.lot_id.id if quant.lot_id else False,
                    'location_id': quant.location_id.id,
                    'theoretical_qty': quant.quantity,
                    'counted_qty': 0.0,
                }))

        if new_lines:
            self.line_ids = new_lines

        self._generate_error_summaries()
        self.state = 'review'
        return True

    def _generate_error_summaries(self):
        self.ensure_one()
        self.error_summary_ids.unlink()
        if not self.line_ids:
            return

        grouped = {}
        for line in self.line_ids:
            if line.difference == 0:
                continue
            key = (line.product_id.id, line.lot_id.id if line.lot_id else False)
            if key not in grouped:
                grouped[key] = {
                    'product_id': line.product_id.id,
                    'lot_id': line.lot_id.id if line.lot_id else False,
                    'theoretical_qty': 0.0,
                    'counted_qty': 0.0,
                    'lines': [],
                }
            grouped[key]['theoretical_qty'] += line.theoretical_qty
            grouped[key]['counted_qty'] += line.counted_qty
            grouped[key]['lines'].append(line)

        summaries = []
        for key, data in grouped.items():
            total_diff = data['counted_qty'] - data['theoretical_qty']
            reason = self._build_summary_reason(data)
            summaries.append((0, 0, {
                'product_id': data['product_id'],
                'lot_id': data['lot_id'] or False,
                'theoretical_qty': data['theoretical_qty'],
                'counted_qty': data['counted_qty'],
                'difference': total_diff,
                'difference_reason': reason,
            }))

        if summaries:
            self.error_summary_ids = summaries

    def _build_summary_reason(self, data):
        lines = data['lines']

        missing = []
        excess = []
        mismatch = []

        for line in lines:
            loc = line.location_id.complete_name
            theo = line.theoretical_qty
            counted = line.counted_qty

            if counted == 0 and theo > 0:
                missing.append((loc, theo))
            elif theo == 0 and counted > 0:
                excess.append((loc, counted))
            elif counted != theo:
                mismatch.append((loc, theo, counted))

        parts = []

        for loc, theo in missing:
            plural = 'unidades' if theo != 1 else 'unidad'
            parts.append(
                'Faltante en [%s]: sistema indica %s %s, no fue escaneado'
                % (loc, theo, plural)
            )

        for loc, counted in excess:
            plural = 'unidades' if counted != 1 else 'unidad'
            parts.append(
                '%s %s en [%s] escaneadas sin registro en sistema'
                % (counted, plural, loc)
            )

        for loc, theo, counted in mismatch:
            diff = counted - theo
            if diff > 0:
                parts.append(
                    'En [%s]: sistema indica %s, contadas %s (sobrante %s)'
                    % (loc, theo, counted, abs(diff))
                )
            else:
                parts.append(
                    'En [%s]: sistema indica %s, contadas %s (faltante %s)'
                    % (loc, theo, counted, abs(diff))
                )

        if not parts:
            return ''

        return '\n'.join(parts)


class StockInventoryAuditErrorSummary(models.Model):
    _name = 'stock.inventory.audit.error.summary'
    _description = 'Resumen de Errores por Producto/Lote'

    audit_id = fields.Many2one(
        'stock.inventory.audit',
        string='Auditoria',
        required=True,
        ondelete='cascade',
    )
    product_id = fields.Many2one(
        'product.product',
        string='Producto',
        required=True,
    )
    lot_id = fields.Many2one(
        'stock.lot',
        string='Lote/Numero de Serie',
    )
    theoretical_qty = fields.Float(
        string='Cantidad Teorica',
    )
    counted_qty = fields.Float(
        string='Cantidad Contada',
    )
    difference = fields.Float(
        string='Diferencia',
    )
    difference_reason = fields.Char(
        string='Motivo de Diferencia',
    )


class StockInventoryAuditLine(models.Model):
    _name = 'stock.inventory.audit.line'
    _description = 'Linea de Auditoria de Inventario'

    audit_id = fields.Many2one(
        'stock.inventory.audit',
        string='Auditoria Padre',
        ondelete='cascade',
    )
    location_id = fields.Many2one(
        'stock.location',
        string='Ubicacion',
        domain=[('usage', '=', 'internal')],
    )
    product_id = fields.Many2one(
        'product.product',
        string='Producto',
        required=True,
    )
    lot_id = fields.Many2one(
        'stock.lot',
        string='Lote/Numero de Serie',
    )
    theoretical_qty = fields.Float(
        string='Cantidad Teorica',
        readonly=True,
        help='Cantidad en sistema.',
    )
    counted_qty = fields.Float(
        string='Cantidad Contada',
        default=0.0,
        help='Cantidad escaneada fisicamente.',
    )
    difference = fields.Float(
        string='Diferencia',
        compute='_compute_difference',
        store=True,
    )
    difference_reason = fields.Char(
        string='Motivo de Diferencia',
        compute='_compute_difference_reason',
        store=True,
    )
    has_error = fields.Boolean(
        string='Tiene Error',
        compute='_compute_has_error',
        store=True,
    )

    @api.depends('theoretical_qty', 'counted_qty')
    def _compute_difference(self):
        for line in self:
            line.difference = line.counted_qty - line.theoretical_qty

    @api.depends('difference')
    def _compute_has_error(self):
        for line in self:
            line.has_error = line.difference != 0

    @api.depends(
        'theoretical_qty', 'counted_qty', 'product_id', 'lot_id', 'location_id',
        'audit_id.line_ids.product_id', 'audit_id.line_ids.lot_id',
        'audit_id.line_ids.location_id', 'audit_id.line_ids.counted_qty',
    )
    def _compute_difference_reason(self):
        for line in self:
            all_lines = line.audit_id.line_ids
            line.difference_reason = line._get_difference_reason(all_lines)

    def _get_difference_reason(self, all_lines):
        self.ensure_one()
        if self.counted_qty == self.theoretical_qty:
            return ''

        if self.counted_qty == 0 and self.theoretical_qty > 0:
            duplicate = all_lines.filtered(
                lambda l: l != self
                and l.product_id == self.product_id
                and l.lot_id == self.lot_id
                and l.counted_qty > 0
            )
            if duplicate:
                locs = duplicate.mapped('location_id.complete_name')
                return (
                    'No encontrado en %s - encontrado en %s'
                    % (self.location_id.complete_name, ', '.join(locs))
                )
            return (
                'No escaneado - sistema indica %s en ubicacion'
                % self.theoretical_qty
            )

        if self.theoretical_qty == 0 and self.counted_qty > 0:
            missing = all_lines.filtered(
                lambda l: l != self
                and l.product_id == self.product_id
                and l.lot_id == self.lot_id
                and l.counted_qty == 0
            )
            if missing:
                locs = missing.mapped('location_id.complete_name')
                return (
                    'Registrado aqui - no encontrado en %s'
                    % ', '.join(locs)
                )
            return 'Producto no registrado en esta ubicacion'

        diff = self.counted_qty - self.theoretical_qty
        return (
            'Contado: %s | Sistema: %s | Diferencia: %s'
            % (self.counted_qty, self.theoretical_qty, diff)
        )

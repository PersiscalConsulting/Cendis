# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class OwnerMoveStock(models.Model):
    _name = 'owner.move.stock'
    _description = 'Movimiento entre Propietarios'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_start desc, id desc'

    # Campos base
    # ------------------------------------------------------------------
    name = fields.Char(
        string='Referencia',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('Nuevo'),
    )
    date_start = fields.Datetime(
        string='Fecha de inicio',
        readonly=True,
        tracking=True,
    )
    date_end = fields.Datetime(
        string='Fecha de fin',
        readonly=True,
        tracking=True,
    )

    # Propietarios y contactos
    # ------------------------------------------------------------------
    owner_id = fields.Many2one(
        'res.partner',
        string='Propietario Origen',
        required=True,
        tracking=True,
    )
    dest_owner_id = fields.Many2one(
        'res.partner',
        string='Propietario Destino',
        required=True,
        tracking=True,
    )
    dest_contact_id = fields.Many2one(
        'res.partner',
        string='Contacto destino',
        domain="[('parent_id', '=', owner_id)]",
        tracking=True,
    )
    dest_supplier_id = fields.Many2one(
        'res.partner',
        string='Proveedor del destino',
        required=True,
        tracking=True,
    )

    # Documentos y pickings asociados
    # ------------------------------------------------------------------
    origin_doc = fields.Char(
        string='Documento de origen',
        tracking=True,
    )
    exit_remito = fields.Char(
        string='Remito de salida',
        tracking=True,
    )
    out_picking_id = fields.Many2one(
        'stock.picking',
        string='Picking de salida',
        readonly=True,
        tracking=True,
    )
    in_picking_id = fields.Many2one(
        'stock.picking',
        string='Picking de ingreso',
        readonly=True,
        tracking=True,
    )

    # Responsable, empresa y etapa (kanban)
    # ------------------------------------------------------------------
    user_id = fields.Many2one(
        'res.users',
        string='Responsable',
        required=True,
        default=lambda self: self.env.user,
        readonly=True,
        tracking=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Empresa',
        required=True,
        default=lambda self: self.env.company,
    )
    stage_id = fields.Many2one(
        'owner.move.stage',
        string='Etapa',
        required=True,
        tracking=True,
        default=lambda self: self._default_stage_id(),
        group_expand='_read_group_stage_ids',
    )
    state = fields.Selection(
        related='stage_id.state',
        string='Estado',
        store=True,
        readonly=True,
        tracking=True,
    )

    # Escaneo: ubicaciones, lineas y detalles
    # ------------------------------------------------------------------
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
    line_ids = fields.One2many(
        'owner.move.stock.line',
        'move_id',
        string='Lineas',
    )
    detail_ids = fields.One2many(
        'owner.move.stock.line.detail',
        'move_id',
        string='Detalles de Escaneo',
    )

    # Contadores y secuencia de empaquetado
    # ------------------------------------------------------------------
    current_pack_seq = fields.Integer(
        string='Secuencia de Empaquetado',
        default=0,
    )
    scanned_qty = fields.Float(
        string='Cantidad Escaneada',
        compute='_compute_scanned_qty',
        store=True,
    )
    package_count = fields.Integer(
        string='Cantidad de Paquetes',
        compute='_compute_package_count',
        store=True,
    )


    @api.model
    def _default_stage_id(self):
        return self.env['owner.move.stage'].search(
            [('state', '=', 'draft')], limit=1
        )

    @api.model
    def _read_group_stage_ids(self, stages, domain, read_group_order=None, access_rights=None):
        """Devuelve todas las etapas para que el kanban las muestre como columnas."""
        return self.env['owner.move.stage'].search([])

    # COMPUTED FIELDS
    # ==================================================================

    @api.depends('current_scan_location_id', 'detail_ids', 'detail_ids.location_id')
    def _compute_scanned_location_ids(self):
        for move in self:
            locations = move.detail_ids.mapped('location_id')
            if move.current_scan_location_id:
                locations |= move.current_scan_location_id
            move.scanned_location_ids = locations

    @api.depends('detail_ids', 'detail_ids.qty')
    def _compute_scanned_qty(self):
        for move in self:
            move.scanned_qty = sum(move.detail_ids.mapped('qty'))

    @api.depends('detail_ids', 'detail_ids.package_id')
    def _compute_package_count(self):
        for move in self:
            move.package_count = len(move.detail_ids.mapped('package_id'))

    # ACCIONES DE ESTADO (botones del form)
    # ==================================================================

    def action_start_scan(self):
        """Pasa de draft a waiting y abre la vista de escaneo OWL."""
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(_('La operacion debe estar en Borrador para iniciar el escaneo.'))
        if not self.name or self.name == _('Nuevo'):
            self.name = self.env['ir.sequence'].next_by_code(
                'owner.move.stock'
            ) or _('Nuevo')
        if not self.date_start:
            self.date_start = fields.Datetime.now()
        waiting = self.env['owner.move.stage'].search(
            [('state', '=', 'waiting')], limit=1
        )
        if not waiting:
            raise UserError(_('No existe una etapa "En espera" configurada.'))
        self.stage_id = waiting.id
        return self._open_scan_action()

    def action_resume_scan(self):
        """Reabre la vista de escaneo OWL si esta en waiting."""
        self.ensure_one()
        if self.state != 'waiting':
            raise UserError(_('La operacion debe estar en espera para continuar el escaneo.'))
        return self._open_scan_action()

    def action_finish_scan(self):
        """Valida datos, genera movimientos intermedios y pasa a available."""
        self.ensure_one()
        if self.state != 'waiting':
            raise UserError(_('La operacion debe estar en espera para finalizar el escaneo.'))
        self._validate_finish()
        self._generate_stock_moves()
        available = self.env['owner.move.stage'].search(
            [('state', '=', 'available')], limit=1
        )
        if not available:
            raise UserError(_('No existe una etapa "Disponible" configurada.'))
        self.stage_id = available.id
        return self._open_form_action()

    def action_cancel_scan(self):
        """Cancela el movimiento y lo marca como rechazado."""
        self.ensure_one()
        if self.state != 'waiting':
            raise UserError(_('La operacion debe estar en espera para poder cancelarla.'))
        cancelled = self.env['owner.move.stage'].search(
            [('state', '=', 'cancelled')], limit=1
        )
        if not cancelled:
            raise UserError(_('No existe una etapa "Rechazado" configurada.'))
        self.stage_id = cancelled.id
        self.date_end = fields.Datetime.now()
        return self._open_form_action()

    def action_validate(self):
        """Ejecuta la accion server que crea los pickings IN/OUT."""
        self.ensure_one()
        action = self.env.ref(
            'custom_owner_stock_change.action_validate_move'
        )
        action.with_context(
            active_id=self.id,
            active_model=self._name,
        ).run()

    # VALIDACION PREVIA AL FINALIZAR
    # ==================================================================

    def _validate_finish(self):
        self.ensure_one()
        errors = []
        get_param = self.env['ir.config_parameter'].sudo().get_param

        if get_param('owner_move_stock.require_packed') == 'True':
            unpicked = self.detail_ids.filtered(lambda d: not d.packed)
            if unpicked:
                lines = ', '.join(
                    '%s%s' % (
                        d.product_id.display_name,
                        ' (Lote %s)' % d.lot_id.name if d.lot_id else '',
                    )
                    for d in unpicked[:5]
                )
                if len(unpicked) > 5:
                    lines += _(', y %s mas.') % (len(unpicked) - 5)
                errors.append(_(
                    'No se puede finalizar: hay %s linea(s) sin empaquetar.\n'
                    '- %s\n'
                    'Empaquete todas las lineas antes de finalizar.'
                ) % (len(unpicked), lines))

        if get_param('owner_move_stock.validate_stock') == 'True':
            stock_errors = self._check_stock_availability(self.detail_ids)
            if stock_errors:
                errors.append(_(
                    'No se puede finalizar: la cantidad escaneada supera las existencias '
                    'disponibles del propietario origen.\n%s'
                ) % '\n'.join('- %s' % error for error in stock_errors))

        if errors:
            raise UserError('\n'.join(errors))

    def _check_stock_availability(self, details):
        self.ensure_one()
        errors = []
        grouped = {}
        for detail in details:
            key = (
                detail.product_id.id,
                detail.location_id.id,
                detail.lot_id.id if detail.lot_id else False,
            )
            grouped.setdefault(key, 0.0)
            grouped[key] += detail.qty
        quant_model = self.env['stock.quant']
        for (product_id, location_id, lot_id), scanned in grouped.items():
            domain = [
                ('product_id', '=', product_id),
                ('location_id', '=', location_id),
                ('quantity', '>', 0),
            ]
            if lot_id:
                domain.append(('lot_id', '=', lot_id))
            if self.owner_id:
                domain.append(('owner_id', '=', self.owner_id.id))
            available = sum(quant_model.search(domain).mapped('quantity'))
            if scanned > available + 0.001:
                product = self.env['product.product'].browse(product_id)
                lot = self.env['stock.lot'].browse(lot_id) if lot_id else False
                product_label = product.display_name
                if lot:
                    product_label += ' (Lote %s)' % lot.name
                errors.append(_(
                    '%s: seleccionado %s, disponible %s en %s'
                ) % (
                    product_label,
                    scanned,
                    available,
                    self.env['stock.location'].browse(location_id).complete_name,
                ))
        return errors

    # GENERACION DE MOVIMIENTOS INTERMEDIOS (al finalizar escaneo)
    # ==================================================================

    def _get_exit_location(self):
        self.ensure_one()
        param = self.env['ir.config_parameter'].sudo().get_param(
            'owner_move_stock.exit_location'
        )
        location_id = False
        if param:
            try:
                location_id = int(param)
            except (TypeError, ValueError):
                location_id = False
        if location_id:
            location = self.env['stock.location'].browse(location_id)
            if location.exists():
                return location
        return self.current_scan_location_id or (
            self.detail_ids[:1].location_id if self.detail_ids else False
        )

    def _generate_stock_moves(self):

        self.ensure_one()
        details = self.detail_ids
        if not details:
            return
        exit_location = self._get_exit_location()
        move_model = self.env['stock.move']
        quant_model = self.env['stock.quant']
        owner_id = self.owner_id.id if self.owner_id else False

        # Agrupar detalles por ubicacion + producto
        grouped = {}
        for detail in details:
            key = (
                detail.location_id.id,
                detail.product_id.id,
            )
            grouped.setdefault(key, self.env['owner.move.stock.line.detail'])
            grouped[key] |= detail

        for (location_id, product_id), group_details in grouped.items():
            origin_location = self.env['stock.location'].browse(location_id)
            product = self.env['product.product'].browse(product_id)
            move_lines = []
            for detail in group_details:
                quant = detail.quant_id
                move_lines.append((0, 0, {
                    'product_id': product.id,
                    'quantity': detail.qty,
                    'product_uom_id': product.uom_id.id,
                    'location_id': origin_location.id,
                    'location_dest_id': exit_location.id,
                    'lot_id': detail.lot_id.id if detail.lot_id else False,
                    'package_id': quant.package_id.id if quant else False,
                    'result_package_id': detail.package_id.id if detail.package_id else False,
                    'owner_id': quant.owner_id.id if quant else owner_id,
                    'company_id': self.company_id.id,
                }))
            move = move_model.create({
                'name': self.name,
                'reference': self.name,
                'product_id': product.id,
                'product_uom_qty': sum(group_details.mapped('qty')),
                'product_uom': product.uom_id.id,
                'location_id': origin_location.id,
                'location_dest_id': exit_location.id,
                'company_id': self.company_id.id,
                'picked': True,
                'move_line_ids': move_lines,
            })
            move._action_confirm()
            move._action_assign()
            move._action_done()

        # Reasignar el quant_id de cada detalle al quant resultante en la
        # ubicacion de salida, para que la validacion lo encuentre correctamente
        for detail in details:
            quant = detail.quant_id
            dest_domain = [
                ('location_id', '=', exit_location.id),
                ('product_id', '=', detail.product_id.id),
                ('package_id', '=', detail.package_id.id if detail.package_id else False),
                ('owner_id', '=', quant.owner_id.id if quant else owner_id),
                ('lot_id', '=', detail.lot_id.id if detail.lot_id else False),
            ]
            dest_quant = quant_model.search(dest_domain, limit=1)
            if dest_quant:
                detail.quant_id = dest_quant.id

    def action_view_packages(self):
        self.ensure_one()
        packages = self.detail_ids.mapped('package_id')
        return {
            'type': 'ir.actions.act_window',
            'name': _('Paquetes'),
            'res_model': 'stock.quant.package',
            'view_mode': 'list,form',
            'domain': [('id', 'in', packages.ids)],
            'context': {'active_id': self.id},
        }

    # NAVEGACION ENTRE VISTAS
    # ==================================================================

    def _open_scan_action(self):
        """Abre la accion client OWL del escaneo de barras."""
        self.ensure_one()
        action = self.env.ref(
            'custom_owner_stock_change.action_owner_move_scan_owl'
        ).read()[0]
        action['context'] = {
            'default_move_id': self.id,
            'active_id': self.id,
        }
        return action

    def _open_form_action(self):
        """Abre el formulario del movimiento actual."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'views': [(False, 'form')],
            'target': 'current',
        }

    # PROCESAMIENTO DE CODIGO DE BARRAS (llamado desde OWL)
    # ==================================================================

    @api.model
    def process_barcode_scan(self, move_id, barcode):
        """Punto de entrada desde el frontend OWL. Resuelve el movimiento y delega."""
        move = self.browse(move_id)
        if not move.exists():
            return {'error': 'Operacion no encontrada.'}
        if move.state != 'waiting':
            return {'error': 'La operacion no esta en estado de escaneo.'}
        return move._process_barcode_scan(barcode)

    def _process_barcode_scan(self, barcode):
        """Intenta resolver el barcode en este orden: ubicacion -> lote -> producto."""
        self.ensure_one()

        # 1) Buscar ubicacion interna por barcode
        location = self.env['stock.location'].search([
            ('barcode', '=', barcode),
            ('usage', '=', 'internal'),
        ], limit=1)
        if location:
            return self._process_location_scan(location)

        if not self.current_scan_location_id:
            return {'error': 'Primero escanee una ubicacion.'}

        # 2) Buscar lote por nombre o referencia
        lot = self.env['stock.lot'].search([
            '|',
            ('name', '=', barcode),
            ('ref', '=', barcode),
        ], limit=1)
        if lot:
            return self._process_lot_scan(lot)

        # 3) Buscar producto por barcode o default_code
        product = self.env['product.product'].search([
            '|',
            ('barcode', '=', barcode),
            ('default_code', '=', barcode),
        ], limit=1)
        if product:
            return self._process_product_scan(product)

        return {'error': 'Codigo no reconocido: %s' % barcode}

    def _process_location_scan(self, location):
        """Cambia la ubicacion activa de escaneo."""
        self.ensure_one()
        self.current_scan_location_id = location.id
        return {
            'message': 'Ubicacion cambiada a: %s' % location.complete_name,
            'location_id': location.id,
            'location_name': location.complete_name,
            'is_location': True,
        }

    def _get_quants(self, product, lot=False):
        """Busca quants disponibles del propietario origen en la ubicacion actual."""
        self.ensure_one()
        if not self.current_scan_location_id:
            return self.env['stock.quant']
        domain = [
            ('location_id', '=', self.current_scan_location_id.id),
            ('product_id', '=', product.id),
            ('quantity', '>', 0),
        ]
        if lot:
            domain.append(('lot_id', '=', lot.id))
        if self.owner_id:
            domain.append(('owner_id', '=', self.owner_id.id))
        return self.env['stock.quant'].search(domain)

    def _get_or_create_line(self, product):
        """Obtiene o crea la linea resumen para un producto."""
        self.ensure_one()
        line = self.line_ids.filtered(lambda l: l.product_id == product)
        if not line:
            line = self.env['owner.move.stock.line'].create({
                'move_id': self.id,
                'product_id': product.id,
            })
        return line

    def _process_lot_scan(self, lot):
        """Procesa el escaneo de un lote: valida stock, crea detalle y retorna resultado."""
        self.ensure_one()
        owner_suffix = ' para el propietario %s' % self.owner_id.name if self.owner_id else ''
        quants = self._get_quants(lot.product_id, lot=lot)
        if not quants:
            return {'error': 'El lote %s no tiene stock en la ubicacion %s%s.' % (
                lot.name,
                self.current_scan_location_id.complete_name,
                owner_suffix,
            )}

        line = self._get_or_create_line(lot.product_id)
        existing_detail = line.detail_ids.filtered(
            lambda d: d.location_id == self.current_scan_location_id
            and d.lot_id == lot
        )
        if existing_detail:
            return {'error': 'El lote %s ya fue escaneado en esta ubicacion.' % lot.name}

        detail = self.env['owner.move.stock.line.detail'].create({
            'move_id': self.id,
            'line_id': line.id,
            'product_id': lot.product_id.id,
            'location_id': self.current_scan_location_id.id,
            'lot_id': lot.id,
            'quant_id': quants[0].id,
            'qty': 1.0,
            'weight': (lot.product_id.weight or 0.0) * 1.0,
            'expiration_date': getattr(lot, 'expiration_date', False),
            'pack_seq': self.current_pack_seq,
        })
        return {
            'detail_id': detail.id,
            'product_id': lot.product_id.id,
            'product_name': lot.product_id.display_name,
            'lot_name': lot.name,
            'scanned_qty': 1.0,
            'location_id': self.current_scan_location_id.id,
            'location_name': self.current_scan_location_id.complete_name,
            'expiration_date': getattr(lot, 'expiration_date', False),
            'package_id': False,
        }

    def _process_product_scan(self, product):
        """Procesa el escaneo de un producto.
        Si el producto no tiene tracking, crea el detalle directamente.
        Si tiene tracking y hay un solo lote en la ubicacion, lo procesa como lote.
        Si hay varios lotes, pide al usuario que escane el lote especifico.
        """
        self.ensure_one()
        owner_suffix = ' para el propietario %s' % self.owner_id.name if self.owner_id else ''
        quants = self._get_quants(product)
        if not quants:
            return {'error': 'El producto %s no tiene stock en la ubicacion %s%s.' % (
                product.display_name,
                self.current_scan_location_id.complete_name,
                owner_suffix,
            )}

        # Sin tracking: crear detalle directo
        if product.tracking == 'none':
            line = self._get_or_create_line(product)
            detail = self.env['owner.move.stock.line.detail'].create({
                'move_id': self.id,
                'line_id': line.id,
                'product_id': product.id,
                'location_id': self.current_scan_location_id.id,
                'quant_id': quants[0].id,
                'qty': 1.0,
                'weight': (product.weight or 0.0) * 1.0,
                'pack_seq': self.current_pack_seq,
            })
            return {
                'detail_id': detail.id,
                'product_id': product.id,
                'product_name': product.display_name,
                'lot_name': False,
                'scanned_qty': 1.0,
                'location_id': self.current_scan_location_id.id,
                'location_name': self.current_scan_location_id.complete_name,
                'package_id': False,
            }

        # Con tracking: resolver lote
        lots_in_location = quants.filtered('lot_id').mapped('lot_id')
        if len(lots_in_location) == 1:
            return self._process_lot_scan(lots_in_location[0])

        if len(lots_in_location) > 1:
            return {
                'error': 'El producto %s tiene varios lotes en %s. Escanee el lote especifico. Disponibles: %s' % (
                    product.display_name,
                    self.current_scan_location_id.complete_name,
                    ', '.join(lots_in_location.mapped('name')),
                ),
            }

        # Tracking activo pero sin lotes en la ubicacion
        line = self._get_or_create_line(product)
        detail = self.env['owner.move.stock.line.detail'].create({
            'move_id': self.id,
            'line_id': line.id,
            'product_id': product.id,
            'location_id': self.current_scan_location_id.id,
            'quant_id': quants[0].id,
            'qty': 1.0,
            'weight': (product.weight or 0.0) * 1.0,
            'pack_seq': self.current_pack_seq,
        })
        return {
            'detail_id': detail.id,
            'product_id': product.id,
            'product_name': product.display_name,
            'lot_name': False,
            'scanned_qty': 1.0,
            'location_id': self.current_scan_location_id.id,
            'location_name': self.current_scan_location_id.complete_name,
            'package_id': False,
        }

    # ACCIONES DEL ESCANEO (empaquetar, cantidad manual, eliminar)
    # ==================================================================

    def pack_selected_lines(self, detail_ids):
        self.ensure_one()
        if self.state != 'waiting':
            raise UserError(_('La operacion no esta en estado de escaneo.'))
        details = self.env['owner.move.stock.line.detail'].browse(detail_ids)
        details = details.filtered(lambda d: d.move_id == self and not d.package_id)
        if not details:
            raise UserError(_('No hay lineas seleccionadas para empaquetar.'))
        if self.env['ir.config_parameter'].sudo().get_param('owner_move_stock.validate_stock') == 'True':
            stock_errors = self._check_stock_availability(details)
            if stock_errors:
                raise UserError(_(
                    'No se puede empaquetar: la cantidad seleccionada supera las existencias '
                    'disponibles del propietario origen.\n%s'
                ) % '\n'.join('- %s' % error for error in stock_errors))
        package = self.env['stock.quant.package'].create({})
        details.write({'package_id': package.id})
        self.current_pack_seq += 1
        return {
            'package_id': package.id,
            'package_name': package.name,
            'detail_ids': details.ids,
        }

    @api.model
    def set_manual_qty(self, detail_id, qty):
        detail = self.env['owner.move.stock.line.detail'].browse(detail_id)
        if not detail.exists():
            return {'error': 'Detalle no encontrado.'}
        if detail.move_id.state != 'waiting':
            return {'error': 'La operacion no esta en estado de escaneo.'}
        if detail.packed:
            return {'error': 'No se puede modificar la cantidad de una linea ya empaquetada.'}
        try:
            detail.qty = float(qty)
        except (TypeError, ValueError):
            return {'error': 'Cantidad invalida.'}
        return {'detail_id': detail.id, 'qty': detail.qty}

    @api.model
    def delete_scan_detail(self, detail_id):
        detail = self.env['owner.move.stock.line.detail'].browse(detail_id)
        if not detail.exists():
            return {'error': 'Detalle no encontrado.'}
        if detail.move_id.state != 'waiting':
            return {'error': 'La operacion no esta en estado de escaneo.'}
        if detail.packed:
            return {'error': 'No se puede eliminar una linea ya empaquetada.'}
        line = detail.line_id
        detail.unlink()
        if line and not line.detail_ids:
            line.unlink()
        return {'success': True}

    @api.model
    def get_scan_settings(self):
        get_param = self.env['ir.config_parameter'].sudo().get_param
        return {
            'manual_qty': get_param('owner_move_stock.manual_qty') == 'True',
            'allow_delete': get_param('owner_move_stock.allow_delete') == 'True',
        }
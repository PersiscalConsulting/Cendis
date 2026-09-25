# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError

from ..services.review_processor import ReviewProcessor
from ..services.config_cache import get_cached_config


class ReviewStockPicking(models.Model):
    """Cabecera del repaso. Nace al validarse un stock.picking de preparacion.

    Relacion 1:1 estricta con el picking de preparacion origen.
    Replica su jerarquia de lineas agrupadas por producto (review.move).

    La logica de negocio (escaneo, validacion, paquetizacion) esta delegada
    a ReviewProcessor en services/review_processor.py.
    """
    _name = 'review.stock.picking'
    _description = 'Repaso de Stock Picking'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name desc, id desc'

    # -- Campos base --
    name = fields.Char(
        string='Referencia',
        required=True,
        copy=False,
        readonly=True,
        default=lambda self: _('Nuevo'),
    )
    preparation_id = fields.Many2one(
        'preparation.stock',
        string='Preparacion de Origen',
        required=True,
        readonly=True,
        ondelete='cascade',
        index=True,
        copy=False,
        help="Preparacion de stock validada que origina este repaso. "
             "Relacion estrictamente 1:1.",
    )
    auto_created = fields.Boolean(
        string='Creado automaticamente',
        default=False,
        copy=False,
        help="True cuando el repaso fue generado por el sistema en la "
             "validacion de una preparacion. Exime a sus lineas del control "
             "de 'repaso manual' para el propietario.",
    )
    company_id = fields.Many2one(
        'res.company',
        string='Empresa',
        required=True,
        default=lambda self: self.env.company,
    )
    user_id = fields.Many2one(
        'res.users',
        string='Responsable',
        required=True,
        default=lambda self: self.env.user,
        tracking=True,
    )
    owner_id = fields.Many2one(
        'res.partner',
        string='Propietario',
        tracking=True,
        help="Propietario heredado del picking de preparacion.",
    )
    partner_id = fields.Many2one(
        'res.partner',
        string='Cliente/Destino',
        tracking=True,
    )
    partner_short_name = fields.Char(
        string='Cliente/Destino (corto)',
        compute='_compute_partner_short_name',
        help="Solo el nombre propio del contacto destino, sin el prefijo "
             "'Propietario, ' que agrega display_name.",
    )

    # -- Etapas / estado --
    stage_id = fields.Many2one(
        'review.stage',
        string='Etapa',
        required=True,
        tracking=False,
        default=lambda self: self._default_stage_id(),
        group_expand='_read_group_stage_ids',
    )
    state = fields.Selection(
        related='stage_id.state',
        string='Estado',
        store=True,
        readonly=True,
        tracking=True,
        index=True,
    )
    active = fields.Boolean(
        string='Activo',
        default=True,
        help="Los repasos archivados se ocultan de las listas. "
             "Solo Administracion/Ajustes puede archivar o reactivar.",
    )

    # -- Fechas --
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

    # -- Lineas y paquetes --
    move_ids = fields.One2many(
        'review.move',
        'review_id',
        string='Lineas de Repaso',
    )
    package_ids = fields.Many2many(
        'stock.quant.package',
        compute='_compute_package_ids',
        string='Paquetes Creados',
    )
    package_count = fields.Integer(
        string='Cantidad de Paquetes',
        compute='_compute_package_count',
    )
    can_manual_packaging = fields.Boolean(
        string='Puede paquetizar manualmente',
        compute='_compute_can_manual_packaging',
        help="Habilita la seleccion/creacion de paquetes en las lineas "
             "segun la configuracion del propietario.",
    )
    can_manual_review = fields.Boolean(
        string='Modo manual activo',
        compute='_compute_can_manual_review',
        help="True si el repaso manual esta habilitado para el propietario.",
    )
    current_scan_location_id = fields.Many2one(
        'stock.location',
        string='Ubicacion Activa de Escaneo',
        readonly=True,
        domain=[('usage', '=', 'internal')],
        help="Ubicacion de separacion escaneada por el operario. "
             "Se debe escanear primero antes de lotes/productos.",
    )
    note = fields.Text(string='Notas')

    # ==================================================================
    # DEFAULTS / COMPUTED
    # ==================================================================

    @api.model
    def _default_stage_id(self):
        return self.env['review.stage'].search(
            [('is_initial', '=', True)], limit=1
        )

    @api.model_create_multi
    def create(self, vals_list):
        """Marca los repasos generados automaticamente (validacion de
        preparacion) para eximirlos del control de 'repaso manual'."""
        if self.env.context.get('review_auto_create'):
            for vals in vals_list:
                vals.setdefault('auto_created', True)
        return super().create(vals_list)

    @api.model
    def _read_group_stage_ids(self, stages, domain):
        """Devuelve todas las etapas para que el kanban las muestre como columnas."""
        return self.env['review.stage'].search([])

    def _compute_package_ids(self):
        for review in self:
            review.package_ids = review.move_ids.mapped(
                'move_line_ids.package_id'
            ).filtered(lambda p: p)

    def _compute_package_count(self):
        for review in self:
            review.package_count = len(review.package_ids)

    def _compute_can_manual_packaging(self):
        for review in self:
            cfg = get_cached_config(self.env, review.company_id.id)
            review.can_manual_packaging = cfg.manual_review_applies(
                review.owner_id
            )

    def _compute_can_manual_review(self):
        for review in self:
            cfg = get_cached_config(self.env, review.company_id.id)
            review.can_manual_review = cfg.manual_review_applies(
                review.owner_id
            )

    @api.depends('partner_id', 'partner_id.name')
    def _compute_partner_short_name(self):
        for review in self:
            review.partner_short_name = review.partner_id.name or False

    # ==================================================================
    # PROCESSOR HELPER
    # ==================================================================

    def _get_processor(self):
        """Retorna una instancia de ReviewProcessor para este repaso."""
        self.ensure_one()
        return ReviewProcessor(self)

    # ==================================================================
    # PACKAGES
    # ==================================================================

    def action_view_packages(self):
        """Abre los paquetes creados en este repaso (vista nativa de stock)."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Paquetes del Repaso',
            'res_model': 'stock.quant.package',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.package_ids.ids)],
        }

    # ==================================================================
    # ARCHIVADO Y PERMISOS DE ELIMINAR
    # ==================================================================

    def _check_can_archive(self):
        """El archivar/reactivar es exclusivo de Administracion/Ajustes."""
        if self.env.su:
            return
        if not self.env.user.has_group('base.group_system'):
            raise UserError(_(
                'Solo Administracion/Ajustes puede archivar o reactivar '
                'repasos.'
            ))

    def write(self, vals):
        if 'active' in vals:
            self._check_can_archive()
        return super().write(vals)

    def toggle_active(self):
        self._check_can_archive()
        return super().toggle_active()

    # ==================================================================
    # TRANSICIONES DE ESTADO
    # ==================================================================

    def action_start(self):
        """Pasa de DISPONIBLE a EN PROCESO."""
        self.ensure_one()
        if self.state != 'available':
            raise UserError(_('El repaso debe estar en estado DISPONIBLE para iniciar.'))
        in_progress = self.env['review.stage'].search(
            [('state', '=', 'in_progress')], limit=1
        )
        if not in_progress:
            raise UserError(_('No existe una etapa "En Proceso" configurada.'))
        if not self.name or self.name == _('Nuevo'):
            self.name = self.env['ir.sequence'].next_by_code(
                'review.stock.picking'
            ) or _('Nuevo')
        self.stage_id = in_progress.id
        if not self.date_start:
            self.date_start = fields.Datetime.now()
        self.message_post(
            body='Repaso iniciado por %s.' % self.env.user.name
        )
        return self._open_form_action()

    def action_finish(self):
        """Pasa de EN PROCESO a FINALIZADO.

        Delega la logica completa (validacion + empaquetado en sitio con
        stock.move de referencia REVS-XXXX, origen = destino = ubicacion
        de separacion, + cambio de etapa) a la server action
        'INVENTARIO REPASO: finalizar repaso', editable desde Odoo web.
        El traslado a la ubicacion de salida ocurre recien al generar el
        picking de salida.
        """
        self.ensure_one()
        server_action = self.env.ref(
            'custom_review_stock_picking.action_finish_review',
            raise_if_not_found=False,
        )
        if not server_action:
            raise UserError(_(
                'No existe la server action de finalizacion del repaso.'
            ))
        # La server action (noupdate) escribe date_end = now al validar;
        # se preserva la fecha de fin de preparacion ya registrada para no
        # perder el tiempo real de repaso.
        prev_date_end = self.date_end
        res = server_action.with_context(active_ids=self.ids).run()
        if prev_date_end and self.date_end != prev_date_end:
            self.date_end = prev_date_end
        return res

    def action_end_preparation(self):
        """Registra el FIN de la preparacion sin pasar a FINALIZADO.

        Solo escribe date_end (fecha fin) manteniendo el repaso en
        EN PROCESO. La transicion a FINALIZADO (cambios de stock) ocurre
        despues, desde el formulario, con el boton 'Validar' que ejecuta
        la server action 'INVENTARIO REPASO: finalizar repaso'.
        """
        self.ensure_one()
        if self.state != 'in_progress':
            raise UserError(_(
                'El repaso debe estar "En Proceso" para finalizar la '
                'preparacion.'
            ))
        if self.date_end:
            raise UserError(_(
                'El repaso ya tiene fecha fin registrada. Use el boton '
                '"Validar" para pasarlo a Finalizado.'
            ))
        self.date_end = fields.Datetime.now()
        self.message_post(
            body='Fin de preparacion del repaso registrado por %s. '
                 'Pendiente de Validar para pasar a Finalizado.'
                 % self.env.user.name
        )
        return True

    def action_cancel(self):
        """Pasa a CANCELADO desde cualquier estado que no sea FINALIZADO."""
        self.ensure_one()
        if self.state == 'done':
            raise UserError(_('No se puede cancelar un repaso ya finalizado.'))
        cancelled = self.env['review.stage'].search(
            [('is_cancel', '=', True)], limit=1
        )
        if not cancelled:
            raise UserError(_('No existe una etapa "Cancelado" configurada.'))
        self.stage_id = cancelled.id
        self.date_end = fields.Datetime.now()
        self.message_post(
            body='Repaso cancelado por %s.' % self.env.user.name
        )
        return self._open_form_action()

    # ==================================================================
    # NAVEGACION
    # ==================================================================

    def _open_form_action(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'views': [(False, 'form')],
            'target': 'current',
        }

    def _open_scan_action(self):
        """Abre la accion client OWL del escaneo de repaso."""
        self.ensure_one()
        action = self.env.ref(
            'custom_review_stock_picking.action_review_scan_owl'
        ).read()[0]
        action['context'] = {
            'default_review_id': self.id,
            'active_id': self.id,
        }
        return action

    def action_open_scan(self):
        """Boton 'Escanear' en el formulario del repaso."""
        self.ensure_one()
        if not self.active:
            raise UserError(_('El repaso esta archivado.'))
        if self.state in ('done', 'cancelled'):
            raise UserError(_('El repaso ya fue finalizado o cancelado.'))
        if self.date_end:
            raise UserError(_(
                'El repaso ya tiene fecha fin registrada. Use el boton '
                '"Validar" para pasarlo a Finalizado.'
            ))
        return self._open_scan_action()

    # ==================================================================
    # SNAPSHOT PARA EL OWL
    # ==================================================================

    @api.model
    def get_review_state(self, review_id):
        """Estado completo para la vista OWL (lineas + flags de configuracion)."""
        review = self.browse(review_id)
        if not review.exists():
            return {'exists': False}
        if not review.active:
            return {'exists': False}
        cfg = get_cached_config(self.env, review.company_id.id)
        flags = cfg.get_flags_for_owner(review.owner_id)
        blind = flags.get('blind_mode', False)
        line_ids = review.move_ids.move_line_ids
        if blind:
            line_ids = line_ids.filtered(lambda l: l.state == 'done')
        line_ids_by_move = {}
        lines_by_package = {}
        for ml in line_ids:
            line_ids_by_move.setdefault(ml.review_move_id, []).append(ml)
            pkg_id = ml.package_id.id if ml.package_id else False
            if pkg_id:
                lines_by_package.setdefault(pkg_id, []).append(ml)

        now = fields.Datetime.now()
        # Los productos (moves/lineas) permanecen ocultos para la vista
        # OWL hasta que el operario escanee la ubicacion de separacion:
        # sin ese escaneo no se envia ningun move al cliente.
        location_scanned = bool(review.current_scan_location_id)
        moves_vals = [
            {
                'id': move.id,
                'product_id': move.product_id.id,
                'product_name': move.product_id.display_name or '',
                'qty_preparation': move.qty_preparation,
                'qty_packaged': move.qty_packaged,
                'qty_remaining': move.qty_remaining,
                'qty_reviewed': sum(
                    ml.qty_reviewed for ml in move.move_line_ids
                    if not ml.package_id
                ),
                'total_qty_reviewed': sum(
                    ml.qty_reviewed for ml in move.move_line_ids
                ),
                'qty_pending_review': sum(
                    ml.qty for ml in move.move_line_ids
                    if not ml.package_id
                ),
                'move_lines': [
                    {
                        'id': ml.id,
                        'lot_name': ml.lot_id.name or '',
                        'lot_id': ml.lot_id.id if ml.lot_id else False,
                        'owner_name': ml.owner_id.name or '',
                        'qty': ml.qty,
                        'qty_reviewed': ml.qty_reviewed,
                        'cant_repasada': ml.qty_reviewed,
                        'package_id': ml.package_id.id if ml.package_id else False,
                        'package_name': ml.package_id.name or '',
                        'expiration_date': ml.expiration_date,
                        'is_excess': ml.is_excess,
                        'is_foreign': ml.is_foreign,
                        'is_expired': bool(
                            ml.expiration_date
                            and ml.expiration_date < now
                        ),
                        'state': ml.state,
                        'weight': ml.weight,
                    }
                    for ml in line_ids_by_move.get(move, [])
                ],
            }
            for move in review.move_ids.sorted('id')
        ] if location_scanned else []
        return {
            'exists': True,
            'id': review.id,
            'name': review.name,
            'state': review.state,
            'preparation_name': review.preparation_id.name or '',
            'owner_name': review.owner_id.name or '',
            'partner_name': review.partner_id.name or '',
            'current_location': (
                review.current_scan_location_id.complete_name or ''
            ) if review.current_scan_location_id else '',
            'separation_location': (
                review.preparation_id.location_dest_id.complete_name or ''
            ) if review.preparation_id.location_dest_id else '',
            'location_scanned': location_scanned,
            'date_start': review.date_start,
            'date_end': review.date_end,
            'package_count': review.package_count,
            'flags': flags,
            'can_manual_review': review.can_manual_review,
            'moves': moves_vals,
            'packages': [
                {
                    'id': pkg.id,
                    'name': pkg.name or '',
                    'shipping_weight': pkg.shipping_weight,
                    'package_type_id': (
                        pkg.package_type_id.id
                        if pkg.package_type_id else False
                    ),
                    'package_type_name': (
                        pkg.package_type_id.name
                        if pkg.package_type_id else ''
                    ),
                    'line_count': len(lines_by_package.get(pkg.id, [])),
                    'total_qty': sum(
                        ml.qty for ml in lines_by_package.get(pkg.id, [])
                    ),
                }
                for pkg in review.package_ids
            ],
        }

    @api.model
    def get_package_types(self):
        """Devuelve los tipos de paquete disponibles para el selector."""
        types = self.env['stock.package.type'].search([])
        return [
            {'id': t.id, 'name': t.name or ''}
            for t in types
        ]

    @api.model
    def get_review_version(self, review_id):
        """Version ligera del repaso para polling incremental.

        Retorna un entero que cambia solo cuando hay modificaciones
        reales (escaneo, paquetizacion, conteo, cambio de etapa).
        Evita que el frontend llame a get_review_state sin cambios.
        """
        review = self.browse(review_id)
        if not review.exists():
            return '0'
        rev_date = review.read(['write_date'])[0]['write_date']
        line_dates = review.move_ids.move_line_ids.mapped('write_date')
        max_line = max(line_dates) if line_dates else rev_date
        version = max(rev_date, max_line)
        return str(int(version.timestamp())) if version else '0'

    # ==================================================================
    # BLOQUEO POR FECHA DE FIN
    # ==================================================================

    def _locked_error(self):
        """Mensaje de error si el repaso ya tiene fecha fin registrada.

        Una vez marcado el FIN de la preparacion (date_end) el repaso
        queda bloqueado para el escaneo y las operaciones del OWL:
        solo queda Validar desde el formulario para pasarlo a FINALIZADO.
        """
        self.ensure_one()
        if self.date_end:
            return (
                'El repaso ya tiene fecha fin registrada. El escaneo esta '
                'bloqueado; valide desde el formulario para pasarlo a '
                'Finalizado.'
            )
        return False

    # ==================================================================
    # BARCODE PROCESSING (delegado a ReviewProcessor)
    # ==================================================================

    @api.model
    def process_review_barcode(self, review_id, barcode):
        """Punto de entrada desde el frontend OWL para escaneo en repaso."""
        review = self.browse(review_id)
        if not review.exists():
            return {'error': 'Repaso no encontrado.'}
        locked = review._locked_error()
        if locked:
            return {'error': locked}
        if review.state != 'in_progress':
            return {'error': 'El repaso no esta en estado de escaneo.'}
        barcode = (barcode or '').strip()
        if not barcode:
            return {'error': 'Codigo vacio.'}
        processor = ReviewProcessor(review)
        try:
            result = processor.process_barcode(barcode)
        except UserError as error:
            self.env.cr.rollback()
            result = {'error': str(error)}
        result.pop('_scan_action', None)
        result.pop('_product_id', None)
        result.pop('_lot_id', None)
        return result

    # ==================================================================
    # MANUAL COUNT (delegado a ReviewProcessor)
    # ==================================================================

    @api.model
    def set_manual_count(self, review_id, move_line_id, qty_reviewed):
        """Fija la cantidad repasada de una linea (conteo manual)."""
        review = self.browse(review_id)
        if not review.exists():
            return {'error': 'Repaso no encontrado.'}
        locked = review._locked_error()
        if locked:
            return {'error': locked}
        processor = ReviewProcessor(review)
        return processor.set_manual_count(move_line_id, qty_reviewed)

    # ==================================================================
    # PACKAGING (delegado a ReviewProcessor)
    # ==================================================================

    @api.model
    def action_create_package(self, review_id, move_line_ids,
                              package_type_id=False, shipping_weight=0.0):
        """Crea un stock.quant.package vacio y asigna las lineas repasadas."""
        review = self.browse(review_id)
        if not review.exists():
            return {'error': 'Repaso no encontrado.'}
        locked = review._locked_error()
        if locked:
            return {'error': locked}
        processor = ReviewProcessor(review)
        return processor.create_package(
            move_line_ids, package_type_id, shipping_weight
        )

    @api.model
    def action_add_to_package(self, review_id, package_id, move_line_ids):
        """Agrega lineas repasadas a un paquete ya existente."""
        review = self.browse(review_id)
        if not review.exists():
            return {'error': 'Repaso no encontrado.'}
        locked = review._locked_error()
        if locked:
            return {'error': locked}
        processor = ReviewProcessor(review)
        return processor.add_to_package(package_id, move_line_ids)

    @api.model
    def action_update_package(self, review_id, package_id,
                              shipping_weight=None, package_type_id=None):
        """Actualiza peso y/o tipo de un paquete existente."""
        review = self.browse(review_id)
        if not review.exists():
            return {'error': 'Repaso no encontrado.'}
        locked = review._locked_error()
        if locked:
            return {'error': locked}
        processor = ReviewProcessor(review)
        return processor.update_package(
            package_id, shipping_weight, package_type_id
        )

    @api.model
    def action_unpackage_lines(self, review_id, move_line_ids):
        """Quita el package_id de las lineas, devolviendolas a 'sin paquetizar'."""
        review = self.browse(review_id)
        if not review.exists():
            return {'error': 'Repaso no encontrado.'}
        locked = review._locked_error()
        if locked:
            return {'error': locked}
        processor = ReviewProcessor(review)
        return processor.unpackage_lines(move_line_ids)

    @api.model
    def action_remove_line_from_package(self, review_id, move_line_id):
        """Saca una linea del paquete y la devuelve a 'sin paquetizar' repasada."""
        review = self.browse(review_id)
        if not review.exists():
            return {'error': 'Repaso no encontrado.'}
        locked = review._locked_error()
        if locked:
            return {'error': locked}
        processor = ReviewProcessor(review)
        return processor.remove_line_from_package(move_line_id)

    @api.model
    def action_delete_review_line(self, review_id, move_line_id):
        """Elimina una linea de exceso o fuera de preparacion (sin paquetizar)."""
        review = self.browse(review_id)
        if not review.exists():
            return {'error': 'Repaso no encontrado.'}
        locked = review._locked_error()
        if locked:
            return {'error': locked}
        processor = ReviewProcessor(review)
        return processor.delete_review_line(move_line_id)

    # ==================================================================
    # VALIDACION INTEGRAL (delegado a ReviewProcessor)
    # ==================================================================

    def _validate_scan(self):
        """Validacion integral del estado del repaso."""
        self.ensure_one()
        processor = ReviewProcessor(self)
        return processor.validate_scan()

    @api.model
    def check_before_finish(self, review_id):
        """Punto de entrada desde OWL antes de Finalizar."""
        review = self.browse(review_id)
        if not review.exists():
            return {'error': 'Repaso no encontrado.'}
        locked = review._locked_error()
        if locked:
            return {'error': locked}
        validation = review._validate_scan()
        return {
            'blocked': not validation['ok'],
            'blocked_issues': validation['issues'],
            'warnings': validation['warnings'],
        }

    # ==================================================================
    # VERIFICAR (informativo)
    # ==================================================================

    def action_verify(self):
        """Verificacion informativa del repaso contra la preparacion original."""
        self.ensure_one()
        if not self.current_scan_location_id:
            raise UserError(_(
                'Escanee la ubicacion de separacion primero.'
            ))
        validation = self._validate_scan()
        unpacked_products = []
        for move in self.move_ids:
            if move.qty_remaining > 0.0001:
                unpacked_products.append({
                    'product_name': move.product_id.display_name,
                    'product_id': move.product_id.id,
                    'qty_remaining': move.qty_remaining,
                    'qty_preparation': move.qty_preparation,
                })
        packages_detail = []
        all_lines = self.move_ids.move_line_ids
        lines_by_package = {}
        for ml in all_lines:
            pkg_id = ml.package_id.id if ml.package_id else False
            if pkg_id:
                lines_by_package.setdefault(pkg_id, []).append(ml)
        for pkg in self.package_ids:
            pkg_lines = lines_by_package.get(pkg.id, [])
            products_in_pkg = {}
            for ml in pkg_lines:
                pname = ml.review_move_id.product_id.display_name
                if pname not in products_in_pkg:
                    products_in_pkg[pname] = {
                        'product_name': pname,
                        'total_qty': 0.0,
                        'lots': [],
                    }
                products_in_pkg[pname]['total_qty'] += ml.qty
                if ml.lot_id:
                    products_in_pkg[pname]['lots'].append(ml.lot_id.name)
            packages_detail.append({
                'id': pkg.id,
                'name': pkg.name or '',
                'shipping_weight': pkg.shipping_weight,
                'package_type_name': (
                    pkg.package_type_id.name if pkg.package_type_id else ''
                ),
                'line_count': len(pkg_lines),
                'total_qty': sum(ml.qty for ml in pkg_lines),
                'products': list(products_in_pkg.values()),
            })
        return {
            'ok': validation['ok'],
            'issues': validation['issues'],
            'warnings': validation['warnings'],
            'unpacked_products': unpacked_products,
            'qty_comparison': validation['summary']['qty_comparison'],
            'packages_summary': packages_detail,
            'total_packages': len(self.package_ids),
        }

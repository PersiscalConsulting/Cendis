# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError

from ..services.barcode_handler import PreparationBarcodeHandler
from ..services.validation_engine import PreparationValidationEngine

EPS = 0.0001


class PreparationStock(models.Model):
    """Preparacion de stock: traslado de mercaderia de un UNICO propietario
    desde ubicaciones internas hacia una ubicacion de separacion.

    El propietario NUNCA cambia (eso lo cubren otras operaciones).
    """
    _name = 'preparation.stock'
    _description = 'Preparacion de Stock'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'name desc, id desc'

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

    # Propietario unico y contactos
    # ------------------------------------------------------------------
    owner_id = fields.Many2one(
        'res.partner',
        string='Propietario',
        required=True,
        tracking=True,
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
    origin_doc = fields.Char(
        string='Documento de origen',
        help="Documento de origen de la preparacion.",
        tracking=True,
    )
    user_id = fields.Many2one(
        'res.users',
        string='Responsable',
        required=True,
        default=lambda self: self.env.user,
        tracking=True,
    )
    company_id = fields.Many2one(
        'res.company',
        string='Empresa',
        required=True,
        default=lambda self: self.env.company,
    )

    # Etapas kanban (patron del modelo de referencia)
    # ------------------------------------------------------------------
    stage_id = fields.Many2one(
        'preparation.stock.stage',
        string='Etapa',
        required=True,
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
        help="Las preparaciones archivadas se ocultan de las listas. "
             "Solo Administracion/Ajustes puede archivar o reactivar.",
    )
    can_import_reception = fields.Boolean(
        string='Puede importar desde recepciones',
        compute='_compute_can_import_reception',
    )

    # Ubicaciones y lineas
    # ------------------------------------------------------------------
    location_dest_id = fields.Many2one(
        'stock.location',
        string='Ubicacion de Separacion',
        domain=[('usage', '=', 'internal')],
        tracking=True,
        help="Destino donde quedan los productos al validar.",
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
    line_ids = fields.One2many(
        'preparation.stock.line',
        'preparation_id',
        string='Lineas Demandadas',
    )
    scan_line_ids = fields.One2many(
        'preparation.stock.scan.line',
        'preparation_id',
        string='Detalles Escaneados',
    )

    # Totales / trazabilidad
    # ------------------------------------------------------------------
    can_edit_in_progress = fields.Boolean(
        compute='_compute_can_edit_in_progress',
        string='Editable en proceso',
    )
    can_add_scan_lines_manual = fields.Boolean(
        compute='_compute_can_add_scan_lines_manual',
        string='Creacion manual habilitada',
    )

    # DEFAULTS / COMPUTED
    # ==================================================================

    @api.model
    def _default_stage_id(self):
        return self.env['preparation.stock.stage'].search(
            [('state', '=', 'draft')], limit=1
        )

    @api.model
    def _read_group_stage_ids(self, stages, domain):
        """Devuelve todas las etapas para que el kanban las muestre como columnas."""
        return self.env['preparation.stock.stage'].search([])

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if 'location_dest_id' in fields_list and not res.get('location_dest_id'):
            cfg = self.env['preparation.stock.config']._get_config()
            res['location_dest_id'] = cfg.default_location_dest_id.id or False
        return res

    @api.depends('current_scan_location_id', 'scan_line_ids', 'scan_line_ids.location_id')
    def _compute_scanned_location_ids(self):
        for prep in self:
            locations = prep.scan_line_ids.mapped('location_id')
            if prep.current_scan_location_id:
                locations |= prep.current_scan_location_id
            prep.scanned_location_ids = locations

    @api.depends('partner_id', 'partner_id.name')
    def _compute_partner_short_name(self):
        for prep in self:
            prep.partner_short_name = prep.partner_id.name or False

    def _compute_can_edit_in_progress(self):
        for rec in self:
            cfg = rec._get_config()
            rec.can_edit_in_progress = cfg.edit_demand_applies(rec.owner_id)

    @api.depends('active', 'state', 'company_id', 'owner_id')
    def _compute_can_import_reception(self):
        for rec in self:
            cfg = rec._get_config()
            rec.can_import_reception = bool(
                rec.active
                and rec.state in ('draft', 'assigned', 'in_progress', 'to_validate')
                and cfg.reception_import_applies()
                and not getattr(rec, 'is_rework', False)
            )

    @api.depends('owner_id')
    def _compute_can_add_scan_lines_manual(self):

        for prep in self:
            cfg = prep._get_config()
            prep.can_add_scan_lines_manual = cfg.manual_scan_create_applies(prep.owner_id)

    def _get_config(self):
        self.ensure_one()
        return self.env['preparation.stock.config']._get_config(self.company_id)

    # SINCRONIZACION AUTOMATICA DE ETAPA (draft <-> assigned)
    # ==================================================================

    def _sync_stage_after_lines_change(self):
        """Pasa a Asignado si hay lineas demandadas; vuelve a Borrador si no."""
        stage_model = self.env['preparation.stock.stage']
        draft_stage = stage_model.search([('state', '=', 'draft')], limit=1)
        assigned_stage = stage_model.search([('state', '=', 'assigned')], limit=1)
        for prep in self:
            if prep.stage_id.state == 'done' or prep.stage_id.state == 'cancelled':
                continue
            has_demand = any(l.qty_demanded > EPS for l in prep.line_ids)
            if has_demand and prep.stage_id.state == 'draft':
                if not prep.name or prep.name == _('Nuevo'):
                    prep.name = self.env['ir.sequence'].next_by_code(
                        'preparation.stock'
                    ) or _('Nuevo')
                if assigned_stage:
                    prep.stage_id = assigned_stage.id
            # Si no hay lineas y esta en assigned, se queda en assigned
            # (el admin puede agregar lineas nuevas en este estado).

    # ARCHIVADO Y PERMISOS DE ELIMINAR
    # ==================================================================

    def _check_can_archive(self):
        """El archivar/reactivar es exclusivo de Administracion/Ajustes."""
        if self.env.su:
            return
        if not self.env.user.has_group('base.group_system'):
            raise UserError(_(
                'Solo Administracion/Ajustes puede archivar o reactivar '
                'preparaciones.'
            ))

    def _release_reservations_on_archive(self):
        """Al archivar, libera las reservas activas para no bloquear stock.
        La reactivacion NO vuelve a reservar."""
        reservations = self.env['stock.quant.reservation'].sudo().search([
            ('preparation_id', 'in', self.ids),
            ('state', '=', 'reserved'),
        ])
        if reservations:
            reservations.write({
                'state': 'released',
                'release_reason': 'archive',
                'release_date': fields.Datetime.now(),
            })

    def write(self, vals):
        if 'active' in vals:
            self._check_can_archive()
        res = super().write(vals)
        if 'active' in vals and not vals['active']:
            self._release_reservations_on_archive()
        return res

    def toggle_active(self):
        self._check_can_archive()
        return super().toggle_active()

    # ACCIONES DE ESTADO (botones del form)
    # ==================================================================

    def action_recalculate_locations(self):
        """Recalcula FEFO manteniendo elecciones manuales."""
        self.ensure_one()
        if self.state not in ('draft', 'assigned'):
            raise UserError(_('Solo se puede recalcular antes de iniciar el escaneo.'))
        self.line_ids._auto_assign_locations(force=False)

    def action_mark_in_progress(self):
        """Pasa de Asignado a En Proceso SIN abrir el OWL (marcador manual)."""
        return self._start_in_progress(open_owl=False)

    def action_start_scan(self):
        """Pasa de Asignado a En Proceso y abre la vista OWL de escaneo."""
        return self._start_in_progress(open_owl=True)

    def _start_in_progress(self, open_owl=False):
        """Transicion comun Asignado -> En Proceso (valida y avanza etapa)."""
        self.ensure_one()
        if self.state != 'assigned':
            raise UserError(_('La preparacion debe estar Asignada para pasar a En Proceso.'))
        if not any(l.qty_demanded > EPS for l in self.line_ids):
            raise UserError(_('Cargue al menos una linea con cantidad demandada.'))
        if not self.date_start:
            self.date_start = fields.Datetime.now()
        in_progress = self.env['preparation.stock.stage'].search(
            [('state', '=', 'in_progress')], limit=1
        )
        if not in_progress:
            raise UserError(_('No existe una etapa "En Proceso" configurada.'))
        self.stage_id = in_progress.id
        self.message_post(
            body=('Iniciado el escaneo (estado "En Proceso") por %s.'
                  if open_owl else 'Marcada como "En Proceso" por %s.')
                 % self.env.user.name
        )
        return self._open_scan_action() if open_owl else self._open_form_action()

    def action_open_scan(self):
        """Boton unificado "Escanear".

        - En 'assigned': valida, pasa a En Proceso y abre el OWL.
        - En 'in_progress'/'to_validate': solo reabre el OWL.
        """
        self.ensure_one()
        if not self.active:
            raise UserError(_('La preparacion esta archivada.'))
        if self.state == 'assigned':
            return self.action_start_scan()
        if self.state in ('in_progress', 'to_validate'):
            return self._open_scan_action()
        raise UserError(_(
            'La preparacion no esta en un estado valido para escanear.'
        ))

    def action_resume_scan(self):
        """Reabre la vista OWL de escaneo. Disponible desde cualquier estado
        que no este finalizado/cancelado (habilita el testing del OWL)."""
        self.ensure_one()
        if not self.active:
            raise UserError(_('La preparacion esta archivada.'))
        if self.state in ('done', 'cancelled'):
            raise UserError(_('La preparacion ya fue finalizada/cancelada.'))
        return self._open_scan_action()

    def action_open_reception_import_wizard(self):
        self.ensure_one()
        if not self.can_import_reception:
            raise UserError(_('Esta preparación no permite importar desde recepciones.'))
        return {
            'type': 'ir.actions.act_window',
            'name': 'Importar desde Recepción',
            'res_model': 'preparation.stock.import.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                **self.env.context,
                'default_preparation_id': self.id,
            },
        }

    def action_cancel(self):
        """Cancela la preparacion (solo antes de Hecho)."""
        self.ensure_one()
        if self.state in ('done', 'cancelled'):
            raise UserError(_('La preparacion ya fue finalizada/cancelada.'))
        # Liberar reservas activas
        reservations = self.env['stock.quant.reservation'].sudo().search([
            ('preparation_id', '=', self.id),
            ('state', '=', 'reserved'),
        ])
        if reservations:
            reservations.write({
                'state': 'released',
                'release_reason': 'cancel',
                'release_date': fields.Datetime.now(),
            })
        cancelled = self.env['preparation.stock.stage'].search(
            [('state', '=', 'cancelled')], limit=1
        )
        if not cancelled:
            raise UserError(_('No existe una etapa "Cancelado" configurada.'))
        self.stage_id = cancelled.id
        self.date_end = fields.Datetime.now()
        self.message_post(
            body='Preparacion cancelada por %s.' % self.env.user.name
        )
        return self._open_form_action()

    def action_mark_to_validate(self):
        """Valida cantidades/estados y transiciona a 'Para Validar'.

        Llamado desde OWL (Finalizar) y desde el boton del form. El OWL permite
        finalizar desde cualquier estado (habilita el testing del boton).
        """
        self.ensure_one()
        if self.state in ('done', 'cancelled'):
            raise UserError(_('La preparacion ya fue finalizada/cancelada.'))
        if not self.line_ids:
            raise UserError(_('La preparacion no tiene lineas.'))
        PreparationValidationEngine.validate_mark_to_validate(self)
        to_validate = self.env['preparation.stock.stage'].search(
            [('state', '=', 'to_validate')], limit=1
        )
        if not to_validate:
            raise UserError(_('No existe una etapa "Para Validar" configurada.'))
        self.stage_id = to_validate.id
        self.message_post(
            body='Marcada como "Para Validar" por %s.' % self.env.user.name
        )
        return self._open_form_action()

    def action_validate(self):
        """Ejecuta la server action para trasladar stock.
        Solo disponible desde estado 'to_validate'."""
        self.ensure_one()
        if self.state != 'to_validate':
            raise UserError(_(
                'Debe marcar la preparacion como "Para Validar" primero.'
            ))
        PreparationValidationEngine.validate_action_validate(self)
        server_action = self.env.ref(
            'custom_preparation_stock.action_validate_preparation'
        )
        server_action.with_context(
            active_id=self.id,
            active_model=self._name,
        ).run()
        return self._open_form_action()

    @api.model
    def check_validation(self, preparation_id):
        """Retorna advertencias antes de validar (excesos/faltantes, vencidos).
        Llamado desde OWL antes de action_validate."""
        prep = self.browse(preparation_id)
        if not prep.exists():
            return {'error': 'Preparacion no encontrada.'}
        if not prep.active:
            return {'error': 'La preparacion esta archivada.'}
        handler = PreparationBarcodeHandler(prep)
        return handler.check_validation()

    # NAVEGACION ENTRE VISTAS
    # ==================================================================

    def _open_scan_action(self):
        """Abre la accion client OWL del escaneo."""
        self.ensure_one()
        action = self.env.ref(
            'custom_preparation_stock.action_preparation_scan_owl'
        ).read()[0]
        action['context'] = {
            'default_preparation_id': self.id,
            'active_id': self.id,
        }
        return action

    def _open_form_action(self):
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
    def process_barcode_scan(self, preparation_id, barcode, selected_line_id=None):
        """Punto de entrada desde el frontend OWL."""
        prep = self.browse(preparation_id)
        if not prep.exists():
            return {'error': 'Preparacion no encontrada.'}
        if not prep.active:
            return {'error': 'La preparacion esta archivada.'}
        if prep.state != 'in_progress':
            return {'error': 'La preparacion no esta en estado de escaneo.'}
        handler = PreparationBarcodeHandler(prep)
        try:
            return handler.process_barcode_scan(barcode, selected_line_id)
        except (UserError, ValueError) as error:
            self.env.cr.rollback()
            return {'error': str(error)}

    @api.model
    def set_scan_qty(self, scan_line_id, qty):
        scan = self.env['preparation.stock.scan.line'].browse(scan_line_id)
        if not scan.exists():
            return {'error': 'Detalle no encontrado.'}
        prep = scan.preparation_id
        handler = PreparationBarcodeHandler(prep)
        return handler.set_scan_qty(scan_line_id, qty)

    @api.model
    def delete_scan_line(self, scan_line_id):
        scan = self.env['preparation.stock.scan.line'].browse(scan_line_id)
        if not scan.exists():
            return {'error': 'Detalle no encontrado.'}
        prep = scan.preparation_id
        handler = PreparationBarcodeHandler(prep)
        return handler.delete_scan_line(scan_line_id)

    # SNAPSHOT PARA EL OWL (polling + refresco)
    # ==================================================================

    @api.model
    def get_preparation_state(self, preparation_id):
        """Estado completo para la vista OWL (lineas + flags de configuracion).

        Refleja EN VIVO los cambios de demanda/ubicaciones hechos por el
        administrativo mientras la preparacion esta En Proceso.
        """
        prep = self.browse(preparation_id)
        if not prep.exists():
            return {'exists': False}
        handler = PreparationBarcodeHandler(prep)
        return handler.get_preparation_state()

    @api.model
    def get_preparation_state_light(self, preparation_id):
        """Check de polling liviano: solo un fingerprint (sin lineas/escaneos).

        El OWL compara el fingerprint contra el ultimo recibido y solo llama a
        get_preparation_state cuando hubo cambios reales.
        """
        prep = self.browse(preparation_id)
        if not prep.exists():
            return {'exists': False}
        return {
            'exists': True,
            'fingerprint': PreparationBarcodeHandler(prep)._state_fingerprint(),
        }
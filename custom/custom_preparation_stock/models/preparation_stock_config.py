# -*- coding: utf-8 -*-
from odoo import api, fields, models


class PreparationStockConfig(models.Model):
    """Configuracion de Preparaciones de Stock (un registro por empresa).

    Se lee SIEMPRE en vivo: los cambios aplican sin importar el estado
    de las preparaciones existentes (sin snapshot).

    Semantica de los selectores de propietarios:
    un selector VACIO significa que el comportamiento aplica a TODOS.
    """
    _name = 'preparation.stock.config'
    _description = 'Configuracion de Preparaciones de Stock'

    company_id = fields.Many2one(
        'res.company',
        string='Empresa',
        required=True,
        default=lambda self: self.env.company,
    )
    default_location_dest_id = fields.Many2one(
        'stock.location',
        string='Ubicacion de separacion por defecto',
        domain=[('usage', '=', 'internal')],
        help="Ubicacion destino donde quedan los productos al validar la preparacion.",
    )

    # -- Comportamiento del escaneo --
    allow_manual_qty = fields.Boolean(
        string='Permitir ingreso manual de cantidad en el escaneo',
        help="Permite modificar manualmente la cantidad de una linea ya escaneada.",
    )
    manual_qty_owner_ids = fields.Many2many(
        'res.partner',
        relation='preparation_config_manual_qty_owner_rel',
        string='Propietarios con ingreso manual',
    )
    allow_delete_scan = fields.Boolean(
        string='Permitir eliminar lineas escaneadas',
        help="Muestra un boton para eliminar lineas ya escaneadas dentro del escaneo OWL.",
    )
    delete_scan_owner_ids = fields.Many2many(
        'res.partner',
        relation='preparation_config_delete_scan_owner_rel',
        string='Propietarios con eliminacion de lineas',
    )
    prevent_duplicate_lot = fields.Boolean(
        string='No permitir escanear dos veces el mismo lote',
        help="Bloquea si el mismo lote del mismo producto ya fue escaneado en la preparacion.",
    )
    duplicate_lot_owner_ids = fields.Many2many(
        'res.partner',
        relation='preparation_config_dup_lot_owner_rel',
        string='Propietarios con control de lote duplicado',
    )
    allow_over_scan = fields.Boolean(
        string='Permitir excesos/faltantes de escaneos',
        help="Permite escanear mas o menos unidades de las demandadas. "
             "Si esta deshabilitado, al validar se exige que las cantidades "
             "demandadas y escaneadas coincidan.",
    )
    control_expired = fields.Boolean(
        string='Controlar mercaderia vencida',
        help="Al escanear un lote vencido se marca en rojo con advertencia. Al validar se pide confirmacion.",
    )

    # -- Validaciones --
    enable_quant_control = fields.Boolean(
        string='Validar existencias al escanear (bloqueante)',
        help="Rechaza el escaneo si la cantidad acumulada supera las existencias "
             "disponibles del propietario en la ubicacion escaneada.",
    )
    quant_control_owner_ids = fields.Many2many(
        'res.partner',
        relation='preparation_config_quant_control_owner_rel',
        string='Propietarios con control de existencias',
    )
    block_validate_over_under = fields.Boolean(
        string='Bloquear validar operación con excedente/faltante',
        help="Al validar (pasar a Hecho) exige que las cantidades demandadas "
             "y escaneadas coincidan exactamente. Es un bloqueante absoluto.",
    )

    # -- Sugerencia de ubicaciones (FEFO) --
    enable_location_suggestion = fields.Boolean(
        string='Sugerir ubicaciones automaticamente (FEFO)',
        default=True,
        help="Calcula sugerencias FEFO al cargar cada producto y permite recalcularlas.",
    )
    location_suggestion_owner_ids = fields.Many2many(
        'res.partner',
        relation='preparation_config_loc_sugg_owner_rel',
        string='Propietarios con sugerencia de ubicaciones',
    )

    # -- Administrativo en proceso --
    edit_demand_in_progress = fields.Boolean(
        string='Permitir editar demanda/ubicaciones en proceso y para validar',
        help="Permite a un usuario administrativo modificar cantidades "
             "demandadas y ubicaciones con la preparacion En Proceso o Para Validar.",
    )
    edit_demand_owner_ids = fields.Many2many(
        'res.partner',
        relation='preparation_config_edit_demand_owner_rel',
        string='Propietarios con edicion en proceso',
    )
    allow_manual_scan_create = fields.Boolean(
        string='Permitir crear lineas de escaneo manualmente',
        help="Habilita el boton 'Agregar una linea' y la creacion manual de los detalles de escaneo desde la vista de linea/preparacion.",
        default=True,
    )
    manual_scan_create_owner_ids = fields.Many2many(
        'res.partner',
        relation='preparation_config_manual_scan_create_owner_rel',
        string='Propietarios con creacion manual',
    )
    enable_reception_import = fields.Boolean(
        string='Permitir importar desde recepciones',
        default=False,
        help='Permite importar mercaderia desde recepciones finalizadas al documento de origen de una preparacion.',
    )
    reception_import_picking_type_id = fields.Many2one(
        'stock.picking.type',
        string='Tipo de picking de recepcion',
        check_company=True,
        help='Tipo de picking cuyos movimientos finalizados se podran importar.',
    )
    enable_reservation = fields.Boolean(
        string='Reservar mercaderia al escanear',
        help="Bloquea que otros operadores usen el mismo producto/lote "
             "mientras se esta preparando esta operacion. La reserva "
             "persiste hasta generar la expedicion.",
    )
    reservation_owner_ids = fields.Many2many(
        'res.partner',
        relation='preparation_config_reservation_owner_rel',
        string='Propietarios con reserva',
    )

    _sql_constraints = [
        (
            'company_uniq',
            'unique(company_id)',
            'Ya existe una configuracion para esta empresa.',
        ),
    ]

    @api.model
    def _get_config(self, company=False):
        """Devuelve (o crea) la configuracion de la empresa indicada."""
        company = company or self.env.company
        config = self.sudo().search([('company_id', '=', company.id)], limit=1)
        if not config:
            config = self.sudo().create({'company_id': company.id})
        return config

    def _flag_for_owner(self, flag_field, owner_field, owner):
        """Un check aplica solo si esta activo Y el owner no esta excluido.

        Selector vacio = aplica a todos los propietarios.
        """
        self.ensure_one()
        if not self[flag_field]:
            return False
        owners = self[owner_field]
        if not owners or not owner:
            return True
        return owner.id in owners.ids

    def get_flags_for_owner(self, owner):
        """Diccionario de flags efectivos para un propietario."""
        self.ensure_one()
        return {
            'manual_qty': self._flag_for_owner('allow_manual_qty', 'manual_qty_owner_ids', owner),
            'allow_delete': self._flag_for_owner('allow_delete_scan', 'delete_scan_owner_ids', owner),
            'quant_control': self._flag_for_owner('enable_quant_control', 'quant_control_owner_ids', owner),
            'location_suggestion': self._flag_for_owner(
                'enable_location_suggestion', 'location_suggestion_owner_ids', owner),
            'edit_demand_in_progress': self._flag_for_owner(
                'edit_demand_in_progress', 'edit_demand_owner_ids', owner),
            'manual_scan_create': self._flag_for_owner(
                'allow_manual_scan_create', 'manual_scan_create_owner_ids', owner),
            'prevent_duplicate_lot': self._flag_for_owner(
                'prevent_duplicate_lot', 'duplicate_lot_owner_ids', owner),
            'reservation': self._flag_for_owner(
                'enable_reservation', 'reservation_owner_ids', owner),
            'allow_over_scan': self.allow_over_scan,
            'control_expired': self.control_expired,
        }

    # Helpers de conveniencia por check
    # ==================================================================

    def manual_qty_applies(self, owner):
        self.ensure_one()
        return self._flag_for_owner('allow_manual_qty', 'manual_qty_owner_ids', owner)

    def delete_scan_applies(self, owner):
        self.ensure_one()
        return self._flag_for_owner('allow_delete_scan', 'delete_scan_owner_ids', owner)

    def quant_control_applies(self, owner):
        self.ensure_one()
        return self._flag_for_owner('enable_quant_control', 'quant_control_owner_ids', owner)

    def location_suggestion_applies(self, owner):
        self.ensure_one()
        return self._flag_for_owner(
            'enable_location_suggestion', 'location_suggestion_owner_ids', owner)

    def edit_demand_applies(self, owner):
        self.ensure_one()
        return self._flag_for_owner(
            'edit_demand_in_progress', 'edit_demand_owner_ids', owner)

    def manual_scan_create_applies(self, owner):
        self.ensure_one()
        return self._flag_for_owner(
            'allow_manual_scan_create', 'manual_scan_create_owner_ids', owner)

    def reception_import_applies(self):
        self.ensure_one()
        return bool(self.enable_reception_import and self.reception_import_picking_type_id)

    def duplicate_lot_applies(self, owner):
        self.ensure_one()
        return self._flag_for_owner(
            'prevent_duplicate_lot', 'duplicate_lot_owner_ids', owner)

    def reservation_applies(self, owner):
        self.ensure_one()
        return self._flag_for_owner(
            'enable_reservation', 'reservation_owner_ids', owner)

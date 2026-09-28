# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ReviewStockConfig(models.Model):
    """Configuracion de Repasos de Stock Picking (un registro por empresa).

    Se lee SIEMPRE en vivo: los cambios aplican sin importar el estado
    de los repasos existentes (sin snapshot).

    Semantica de los selectores de propietarios:
    un selector VACIO significa que el comportamiento aplica a TODOS.
    """
    _name = 'review.stock.config'
    _description = 'Configuracion de Repasos de Stock Picking'

    company_id = fields.Many2one(
        'res.company',
        string='Empresa',
        required=True,
        default=lambda self: self.env.company,
    )
    picking_type_id = fields.Many2one(
        'stock.picking.type',
        string='Tipo de Picking de Salida',
        help="Tipo de picking que se generara al generar el picking de "
             "salida desde repasos finalizados.",
    )
    exit_location_id = fields.Many2one(
        'stock.location',
        string='Ubicacion de Salida OUT',
        domain=[('usage', '=', 'internal')],
        help="Ubicacion a la que se trasladan los paquetes armados al "
             "generar el picking de salida (ej: TOR1/Expedicion). Es el "
             "origen del stock.picking de salida.",
    )

    # -- Modo ciego / visual --
    blind_mode = fields.Boolean(
        string='Modo ciego (ocultar detalle de lineas)',
        help="En modo ciego no se pueden desplegar review.move.line, "
             "solo se ven las lineas agrupadas por producto.",
    )
    blind_mode_owner_ids = fields.Many2many(
        'res.partner',
        relation='review_config_blind_mode_owner_rel',
        string='Propietarios con modo ciego',
    )

    # -- Modo manual (repaso, creacion de lineas, paquetizacion) --
    allow_manual_review = fields.Boolean(
        string='Permitir repaso manual',
        help="Habilita el modo manual del repaso: ingreso de cantidades, "
             "creacion de lineas y paquetizacion. Cuando esta desactivado, "
             "el flujo es exclusivamente por escaneo.",
    )
    manual_review_owner_ids = fields.Many2many(
        'res.partner',
        relation='review_config_manual_review_owner_rel',
        string='Propietarios con repaso manual',
    )

    # -- Lectura de codigo de barras: conteos manuales --
    allow_manual_counts = fields.Boolean(
        string='Permitir conteos manuales',
        help="Habilita el campo/boton de conteo (+1 o cantidad) en las "
             "lineas de la vista de repaso para registrar la cantidad "
             "repasada manualmente.",
    )
    manual_counts_owner_ids = fields.Many2many(
        'res.partner',
        relation='review_config_manual_counts_owner_rel',
        string='Propietarios con conteos manuales',
    )

    # -- Lectura de codigo de barras: desempaquetar --
    allow_unpackaging = fields.Boolean(
        string='Permitir desempaquetar',
        help="Habilita devolver lineas a 'Sin paquetizar': el boton "
             "'Desempaquetar todo' del paquete y el boton para eliminar "
             "una linea individual del paquete.",
    )
    unpackaging_owner_ids = fields.Many2many(
        'res.partner',
        relation='review_config_unpackaging_owner_rel',
        string='Propietarios que pueden desempaquetar',
    )

    # -- Lotes duplicados --
    allow_duplicate_lots = fields.Boolean(
        string='Permitir lotes duplicados',
        help="Si esta deshabilitado, no se permite escanear dos veces "
             "el mismo lote del mismo producto en un repaso.",
    )
    duplicate_lots_owner_ids = fields.Many2many(
        'res.partner',
        relation='review_config_dup_lots_owner_rel',
        string='Propietarios con lotes duplicados',
    )

    # -- Escaneo restringido a la preparacion --
    block_foreign_scans = fields.Boolean(
        string='Bloquear escaneo fuera de la preparacion',
        help="Si esta activado, no se permite escanear lotes que no fueron "
             "preparados en la preparacion de origen de este repaso. Si esta "
             "desactivado, esos escaneos se permiten pero sus lineas se "
             "marcan en rojo como 'Fuera de preparacion'.",
    )
    block_foreign_scans_owner_ids = fields.Many2many(
        'res.partner',
        relation='review_config_block_foreign_owner_rel',
        string='Propietarios con bloqueo de escaneo foraneo',
    )

    # -- Validaciones parametrizables --
    control_qty_differences = fields.Boolean(
        string='Controlar cantidades excedentes y faltantes',
        help="Si esta activado, no se permite escanear cantidades por "
             "encima de lo preparado ni Finalizar si hay diferencia entre "
             "la cantidad preparada y la repasada (excedente o faltante). "
             "Si esta desactivado, los excesos se permiten (marcados en "
             "naranja) y al Finalizar solo se muestra una advertencia.",
    )
    control_qty_differences_owner_ids = fields.Many2many(
        'res.partner',
        relation='review_config_qty_diffs_owner_rel',
        string='Propietarios con control de cantidades',
    )
    control_stock_availability = fields.Boolean(
        string='Control de existencias',
        help="Si esta activado, no se permite Finalizar si se escanearon "
             "mas productos de los que realmente hay en stock en la "
             "ubicacion de separacion.",
    )
    control_stock_availability_owner_ids = fields.Many2many(
        'res.partner',
        relation='review_config_stock_avl_owner_rel',
        string='Propietarios con control de existencias',
    )
    allow_finish_unpacked_owner_ids = fields.Many2many(
        'res.partner',
        relation='review_config_finish_unpacked_owner_rel',
        string='Propietarios que pueden Finalizar sin paquetizar',
        help="Solo estos propietarios quedan EXCEPTUADOS de la "
             "restriccion de no Finalizar sin paquetizar. Selector VACIO "
             "= nadie puede Finalizar con productos sin paquetizar.",
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

    def blind_mode_applies(self, owner):
        self.ensure_one()
        return self._flag_for_owner('blind_mode', 'blind_mode_owner_ids', owner)

    def manual_review_applies(self, owner):
        self.ensure_one()
        return self._flag_for_owner(
            'allow_manual_review', 'manual_review_owner_ids', owner)

    def manual_counts_applies(self, owner):
        self.ensure_one()
        return self._flag_for_owner(
            'allow_manual_counts', 'manual_counts_owner_ids', owner)

    def unpackaging_applies(self, owner):
        self.ensure_one()
        return self._flag_for_owner(
            'allow_unpackaging', 'unpackaging_owner_ids', owner)

    def duplicate_lots_applies(self, owner):
        self.ensure_one()
        return self._flag_for_owner(
            'allow_duplicate_lots', 'duplicate_lots_owner_ids', owner)

    def block_foreign_scans_applies(self, owner):
        self.ensure_one()
        return self._flag_for_owner(
            'block_foreign_scans',
            'block_foreign_scans_owner_ids',
            owner,
        )

    def qty_differences_applies(self, owner):
        self.ensure_one()
        return self._flag_for_owner(
            'control_qty_differences',
            'control_qty_differences_owner_ids',
            owner,
        )

    def stock_availability_applies(self, owner):
        self.ensure_one()
        return self._flag_for_owner(
            'control_stock_availability',
            'control_stock_availability_owner_ids',
            owner,
        )

    def can_finish_without_unpacked(self, owner):
        """Regla por defecto: NO se puede Finalizar sin paquetizar.

        Solo los owners en allow_finish_unpacked_owner_ids quedan
        exceptuados. Selector vacio = nadie.
        """
        self.ensure_one()
        if not owner:
            return False
        return owner.id in self.allow_finish_unpacked_owner_ids.ids

    def get_flags_for_owner(self, owner):
        """Diccionario de flags efectivos para un propietario."""
        self.ensure_one()
        return {
            'blind_mode': self.blind_mode_applies(owner),
            'manual_review': self.manual_review_applies(owner),
            'duplicate_lots': self.duplicate_lots_applies(owner),
            'manual_counts': self.manual_counts_applies(owner),
            'allow_unpackaging': self.unpackaging_applies(owner),
            'block_foreign_scans': self.block_foreign_scans_applies(owner),
            'control_qty_differences': self.qty_differences_applies(owner),
            'control_stock_availability': self.stock_availability_applies(owner),
            'can_finish_without_unpacked': self.can_finish_without_unpacked(owner),
        }

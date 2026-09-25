# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_compare

from ..services.validation_engine import PreparationValidationEngine

EPS = 0.0001


class PreparationStockLine(models.Model):
    """Linea demandada de una preparacion.

    Analogia funcional: preparation.stock.line <-> stock.move.
    """
    _name = 'preparation.stock.line'
    _description = 'Linea de Preparacion de Stock'
    _order = 'id asc'

    preparation_id = fields.Many2one(
        'preparation.stock',
        string='Preparacion',
        required=True,
        ondelete='cascade',
        index=True,
    )
    location_dest_id = fields.Many2one(
        'stock.location',
        related='preparation_id.location_dest_id',
        readonly=True,
        store=False,
    )
    company_id = fields.Many2one(
        related='preparation_id.company_id',
        store=True,
    )
    owner_id = fields.Many2one(
        related='preparation_id.owner_id',
        store=True,
        string='Propietario',
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
    total_weight = fields.Float(
        string='Peso Total',
        compute='_compute_total_weight',
        digits='Stock Weight',
        help="Peso total de la cantidad demandada (qty_demanded * peso del producto).",
    )

    qty_demanded = fields.Float(
        string='Cantidad Demandada',
        default=1.0,
        digits='Product Unit of Measure',
    )
    qty_scanned = fields.Float(
        string='Cantidad Escaneada',
        compute='_compute_qty_scanned',
        store=True,
        digits='Product Unit of Measure',
    )
    remaining_qty = fields.Float(
        string='Cantidad Restante',
        compute='_compute_qty_scanned',
        digits='Product Unit of Measure',
    )
    has_scans = fields.Boolean(
        string='Tiene escaneos',
        compute='_compute_qty_scanned',
    )

    suggested_location_ids = fields.Many2many(
        'stock.location',
        relation='preparation_stock_line_location_rel',
        column1='line_id',
        column2='location_id',
        string='Ubicaciones Sugeridas',
        help="Resultado del calculo FEFO (puede ser mas de una ubicacion).",
    )
    location_ids = fields.Many2many(
        'stock.location',
        relation='preparation_stock_line_chosen_location_rel',
        column1='line_id',
        column2='location_id',
        string='Ubicaciones Elegidas',
        domain=[('usage', '=', 'internal')],
        help='Ubicaciones desde donde se permite escanear esta linea.',
    )
    stock_move_ids = fields.Many2many(
        'stock.move',
        compute='_compute_stock_move_ids',
        string='Movimientos Generados',
        help='Moves creados al validar; uno por linea con sus move.line espejo.',
    )
    location_manual = fields.Boolean(
        string='Ubicacion elegida manualmente',
        default=False,
        copy=False,
        help="Indica que el usuario eligio la ubicacion a mano; el recalcular FEFO no la pisa.",
    )

    state_line = fields.Selection(
        [
            ('pending', 'Pendiente'),
            ('in_progress', 'En Proceso'),
            ('done', 'Completa'),
            ('short', 'Faltante'),
        ],
        string='Estado Linea',
        compute='_compute_state_line',
        store=True,
    )
    is_short = fields.Boolean(
        string='Faltante al validar',
        default=False,
        copy=False,
        help="Marcada automaticamente al validar si quedo cantidad sin escanear.",
    )

    scan_line_ids = fields.One2many(
        'preparation.stock.scan.line',
        'preparation_line_id',
        string='Detalles de Escaneo',
    )
    used_quant_ids = fields.Many2many(
        'stock.quant',
        compute='_compute_used_quant_ids',
        string='Quants ya escaneados',
        help="Quants ya seleccionados en detalles de escaneo; se excluyen "
             "del selector manual para evitar duplicados.",
    )

    # COMPUTED
    # ==================================================================

    @api.depends('scan_line_ids', 'scan_line_ids.qty')
    def _compute_qty_scanned(self):
        for line in self:
            scanned = sum(line.scan_line_ids.mapped('qty'))
            line.qty_scanned = scanned
            line.remaining_qty = max(line.qty_demanded - scanned, 0.0)
            line.has_scans = bool(line.scan_line_ids)

    @api.depends('qty_scanned', 'qty_demanded', 'is_short')
    def _compute_state_line(self):
        for line in self:
            if line.is_short:
                line.state_line = 'short'
            elif (line.qty_demanded > EPS and
                  float_compare(line.qty_scanned, line.qty_demanded,
                                precision_digits=4) == 0):
                line.state_line = 'done'
            elif line.qty_scanned > EPS:
                line.state_line = 'in_progress'
            else:
                line.state_line = 'pending'

    @api.depends('scan_line_ids.scan_weight')
    def _compute_total_weight(self):
        for line in self:
            line.total_weight = sum(line.scan_line_ids.mapped('scan_weight'))

    @api.depends('scan_line_ids.quant_id')
    def _compute_used_quant_ids(self):
        for line in self:
            line.used_quant_ids = line.scan_line_ids.mapped('quant_id')

    # CONSTRAINTS
    # ==================================================================

    @api.constrains('preparation_id', 'product_id')
    def _check_product_unique(self):
        """Un producto solo puede aparecer una vez por preparacion."""
        for line in self:
            duplicated = line.preparation_id.line_ids.filtered(
                lambda l: l.product_id == line.product_id and l.id != line.id
            )
            if duplicated:
                raise UserError(_(
                    'El producto %s ya existe en la preparacion %s.'
                ) % (line.product_id.display_name, line.preparation_id.name))

    @api.constrains('qty_demanded')
    def _check_qty_demanded(self):
        for line in self:
            if float_compare(line.qty_demanded, 0.0, precision_digits=4) < 0:
                raise UserError(_('La cantidad demandada no puede ser negativa.'))
            if line.has_scans and float_compare(
                    line.qty_demanded, line.qty_scanned, precision_digits=4) < 0:
                raise UserError(_(
                    'No se puede reducir la demanda de %s por debajo de lo ya '
                    'escaneado (%s). Elimine primero las cantidades excedentes.'
                ) % (line.product_id.display_name, line.qty_scanned))

    # PERMISOS DE EDICION EN PROCESO / PARA VALIDAR
    # ==================================================================

    def _check_edit_in_progress(self):
        """Editar demanda/ubicacion en In Progress o Para Validar requiere
        configuracion del propietario (sin grupo system)."""
        for line in self:
            prep = line.preparation_id
            if prep.state not in ('in_progress', 'to_validate'):
                continue
            # Bypass para el auto-sync del popup manual-add
            cfg = prep._get_config()
            if self.env.context.get('scan_auto_sync_demand') and cfg.manual_scan_create_applies(prep.owner_id):
                continue
            if self.env.context.get('prep_manual_add') and cfg.manual_scan_create_applies(prep.owner_id):
                continue

            if not cfg.edit_demand_applies(prep.owner_id):
                raise UserError(_(
                    'No está habilitada la edición de demanda/ubicaciones '
                    'para este propietario en esta etapa.'
                ))

    # CRUD
    # ==================================================================

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._auto_assign_locations()
        lines._sync_parent_stage()
        return lines

    def write(self, vals):
        protected_fields = {'qty_demanded', 'location_ids'}
        if protected_fields & set(vals.keys()) \
                and not self.env.context.get('fefo_assign'):
            PreparationValidationEngine.validate_preparation_line_write(self, vals, self.env.context)
        if 'location_ids' in vals:
            target_ids = self._target_locations_from_command(
                vals['location_ids'])
            if target_ids is not None:
                self._check_removed_locations_with_scans(target_ids)
            if not self.env.context.get('fefo_assign'):
                vals['location_manual'] = bool(target_ids or set())
        res = super().write(vals)
        if 'qty_demanded' in vals:
            self._auto_assign_locations()
            self._sync_parent_stage()
        return res

    def unlink(self):
        PreparationValidationEngine.validate_preparation_line_unlink(self)
        preps = self.mapped('preparation_id')
        res = super().unlink()
        for prep in preps:
            prep._sync_stage_after_lines_change()
        return res

    # SUGERENCIA FEFO
    # ==================================================================

    def _auto_assign_locations(self, force=False):
        """Sugerencia FEFO automatica para cada linea segun configuracion."""
        for line in self:
            cfg = line.preparation_id._get_config()
            if not cfg.location_suggestion_applies(line.preparation_id.owner_id):
                continue
            line._assign_fefo(force=force)

    def _assign_fefo(self, force=False):
        """Calcula sugerencias FEFO greedy hasta cubrir lo demandado."""
        self.ensure_one()
        quant_model = self.env['stock.quant']
        domain = [
            ('product_id', '=', self.product_id.id),
            ('location_id.usage', '=', 'internal'),
            ('quantity', '>', 0),
        ]
        if self.owner_id:
            domain.append(('owner_id', '=', self.owner_id.id))
        quants = quant_model.sudo().search(domain, order='removal_date, in_date, id')

        remaining = self.qty_demanded
        locations = self.env['stock.location']
        for quant in quants:
            if float_compare(remaining, 0.0, precision_digits=4) <= 0:
                break
            locations |= quant.location_id
            remaining -= quant.quantity

        write_vals = {'suggested_location_ids': [(6, 0, locations.ids)]}
        if force or not self.location_manual:
            # Fusionar con ubicaciones que ya tienen escaneos: nunca se quita
            # una ubicacion activa al recalcular FEFO (evita el bloqueo de
            # "ubicaciones con escaneos asociados" al ajustar la demanda).
            scanned_locations = self.scan_line_ids.mapped('location_id')
            chosen = locations | scanned_locations
            write_vals['location_ids'] = [(6, 0, chosen.ids)]
        self.with_context(fefo_assign=True).write(write_vals)

    def action_recalculate_locations_line(self):
        """Recalcula la sugerencia de esta linea manteniendo elecciones manuales."""
        self._auto_assign_locations(force=False)

    @api.depends('scan_line_ids.stock_move_id')
    def _compute_stock_move_ids(self):
        for line in self:
            line.stock_move_ids = line.scan_line_ids.mapped('stock_move_id')

    @api.model
    def _target_locations_from_command(self, command):
        """Ids finales de un comando many2many [(6, 0, ids)].

        Devuelve None si el comando no trae reemplazo completo.
        """
        if not command:
            return None
        target = None
        for item in command:
            if isinstance(item, (tuple, list)) and item[0] == 6:
                target = set(item[2] or [])
        return target

    def _check_removed_locations_with_scans(self, target_ids):
        """No permitir quitar ubicaciones elegidas con escaneos asociados."""
        for line in self:
            conflict = line.scan_line_ids.mapped('location_id').filtered(
                lambda l: l.id not in target_ids)
            if conflict:
                names = ', '.join(conflict.mapped('complete_name'))
                raise UserError(_(
                    'No se puede quitar la(s) ubicacion(es) %s de %s '
                    'porque ya tienen escaneos asociados. Elimine primero '
                    'esos escaneos.'
                ) % (names, line.product_id.display_name))

    def action_view_line_form(self):
        """Abre el formulario de la linea en popup (vista emergente)."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Linea: %s' % self.product_id.display_name,
            'res_model': 'preparation.stock.line',
            'res_id': self.id,
            'views': [(False, 'form')],
            'target': 'new',
        }

    # SINCRONIZACION DE ETAPA DEL PADRE
    # ==================================================================

    def _sync_parent_stage(self):
        """Pasa la preparacion a Asignado cuando hay lineas con cantidades."""
        for prep in self.mapped('preparation_id'):
            prep._sync_stage_after_lines_change()
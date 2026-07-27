# -*- coding: utf-8 -*-
from odoo import models, fields, _


class StockInventoryAuditFinishWizard(models.TransientModel):
    _name = 'stock.inventory.audit.finish.wizard'
    _description = 'Wizard para finalizar auditoria de inventario'

    audit_id = fields.Many2one(
        'stock.inventory.audit',
        string='Auditoria',
        required=True,
        ondelete='cascade',
    )
    scanned_qty = fields.Integer(
        related='audit_id.scanned_qty',
        string='Items Escaneados',
    )
    location_names = fields.Char(
        compute='_compute_location_names',
        string='Ubicaciones',
    )

    def _compute_location_names(self):
        for wiz in self:
            locations = wiz.audit_id.scanned_location_ids.mapped('complete_name')
            wiz.location_names = ', '.join(locations) if locations else '-'

    def button_confirm_finish(self):
        self.ensure_one()
        self.audit_id.action_finish_scan()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.inventory.audit',
            'res_id': self.audit_id.id,
            'views': [(False, 'form')],
            'target': 'current',
        }

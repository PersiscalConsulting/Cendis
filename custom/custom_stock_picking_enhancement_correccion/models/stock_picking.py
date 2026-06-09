# -*- coding: utf-8 -*-
from odoo import models, fields, api

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    def action_combine_pickings(self):
        # 1. Ejecutamos la lógica original
        res = super(StockPicking, self).action_combine_pickings()
        
        # 2. Correción
        if res and res.get('res_id'):
            new_picking = self.env['stock.picking'].browse(res['res_id'])
            first_picking = self[0]
            
            # Ajuste Lautaro: Arrastre de destino_id si existe
            if first_picking.destino_id:
                new_picking.write({
                    'destino_id': first_picking.destino_id.id
                })
                
        return res
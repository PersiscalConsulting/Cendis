# -*- coding: utf-8 -*-
{
    'name': 'Re-Proceso de Preparaciones y Repasos',
    'version': '1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Volver a preparar: autorizacion con credenciales, reversa de stock, gestion de excedentes, log de modificaciones y repasos en version.',
    'description': """
Re-proceso (volver a preparar) para el flujo Preparacion -> Repaso -> Expedicion:
  - Boton 'Volver a Preparar' en el repaso (estado Modificado).
  - Boton 'Devolver a Preparacion' en el picking de salida no validado.
  - Wizard de autorizacion con credenciales reales de Odoo y lista de
    usuarios autorizados (review.stock.config.rework_auth_user_ids).
  - Reversion de los movimientos del repaso (Expedicion -> Separacion)
    conservando los paquetes.
  - Gestion de excedentes (wizard): mueve el stock a retirar de Separacion
    hacia una ubicacion con/ sin paquete por producto.
  - Repasos por version (v1, v2, ...) con herencia de cantidades repasadas.
  - Historial de modificaciones en la preparacion (pestana 'Modificaciones').
    """,
    'depends': ['custom_review_stock_picking'],
    'data': [
        'security/ir.model.access.csv',
        'data/review_stage_modified_data.xml',
        'views/review_stock_config_views.xml',
        'views/res_users_views.xml',
        'views/rework_wizard_views.xml',
        'views/preparation_stock_views.xml',
        'views/review_stock_picking_views.xml',
        'views/stock_picking_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
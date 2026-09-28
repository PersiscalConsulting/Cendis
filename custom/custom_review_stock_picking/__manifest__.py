# -*- coding: utf-8 -*-
{
    'name': 'Repaso de Stock Picking',
    'version': '1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Repaso optimizado de preparaciones de stock con escaneo asistido, paquetizacion y generacion de picking de salida.',
    'description': """
Gestion de repasos de stock picking:
  - Revision fisica de preparaciones validadas.
  - Agrupacion de productos en paquetes (stock.quant.package).
  - Generacion del picking de salida (server action) con traslado a la ubicacion de salida.
  - Estados configurables (review.stage) con flags booleanos.
  - Configuracion parametrizable por empresa.
    """,
    'depends': ['stock', 'mail', 'custom_preparation_stock'],
    'data': [
        'security/ir.model.access.csv',
        'data/review_stock_stage_data.xml',
        'data/review_stock_picking_sequence.xml',
        'data/review_stock_actions.xml',
        'data/review_stock_finish_actions.xml',
        'data/review_stock_generate_picking_actions.xml',
        'views/review_stock_stage_views.xml',
        'views/review_stock_config_views.xml',
        'views/review_stock_picking_views.xml',
        'views/review_move_views.xml',
        'views/review_picking_generate_wizard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'custom_review_stock_picking/static/src/js/review_scan_action.js',
            'custom_review_stock_picking/static/src/xml/review_scan_templates.xml',
            'custom_review_stock_picking/static/src/scss/review_scan_style.scss',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}

# -*- coding: utf-8 -*-
{
    'name': 'Movimientos entre Propietarios',
    'version': '1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Preparacion de movimientos de stock entre propietarios con escaneo de codigo de barras.',
    'depends': ['stock', 'stock_barcode', 'barcodes', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'data/owner_move_stock_sequence.xml',
        'data/owner_move_stage_data.xml',
        'data/owner_move_stock_actions.xml',
        'views/owner_move_stage_views.xml',
        'views/owner_move_stock_views.xml',
        'views/owner_move_stock_settings_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'custom_owner_stock_change/static/src/js/owner_move_scan_action.js',
            'custom_owner_stock_change/static/src/xml/owner_move_scan_templates.xml',
            'custom_owner_stock_change/static/src/scss/owner_move_scan_style.scss',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}

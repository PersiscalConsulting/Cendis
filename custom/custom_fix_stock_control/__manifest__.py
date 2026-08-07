# -*- coding: utf-8 -*-
{
    'name': 'Fixes y Mejoras - Control de Inventario',
    'version': '1.0',
    'category': 'Inventory/Inventory',
    'summary': 'Mejoras y arreglos al módulo de auditoría a ciegas con código de barras.',
    'depends': ['custom_stock_control_barcode', 'mail'],
    'data': [
        'views/stock_inventory_audit_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'custom_fix_stock_control/static/src/js/audit_barcode_action.js',
            'custom_fix_stock_control/static/src/xml/audit_barcode_templates.xml',
            'custom_fix_stock_control/static/src/scss/audit_barcode_style.scss',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}

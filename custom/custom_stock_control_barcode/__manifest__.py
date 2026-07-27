# -*- coding: utf-8 -*-
{
    'name': 'Control de Inventario a Ciegas con Codigo de Barras',
    'version': '2.0',
    'category': 'Inventory/Inventory',
    'summary': 'Auditorias de inventario a ciegas utilizando la app de codigo de barras.',
    'depends': ['stock', 'stock_barcode', 'barcodes'],
    'data': [
        'security/ir.model.access.csv',
        'views/stock_inventory_audit_views.xml',
        'views/stock_inventory_audit_wizard_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'custom_stock_control_barcode/static/src/js/audit_barcode_action.js',
            'custom_stock_control_barcode/static/src/xml/audit_barcode_templates.xml',
            'custom_stock_control_barcode/static/src/scss/audit_barcode_style.scss',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}

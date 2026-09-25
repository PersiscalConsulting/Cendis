# -*- coding: utf-8 -*-
{
    'name': 'Preparaciones de Stock',
    'version': '1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Preparaciones de pedidos con escaneo asistido (OWL), sugerencia FEFO y validacion de movimientos reales.',
    'description': """
Gestion de preparaciones de stock:
  - Preparacion de un solo propietario, sin cambio de dueno.
  - Carga manual de lineas demandadas con sugerencia FEFO automatica.
  - Escaneo OWL con control bloqueante de existencias y anti sobre-demanda.
  - Validacion mediante server action editable (INVENTARIO PREPARACION: validar preparacion)
    que ejecuta los stock.move.line espejo de las lineas escaneadas.
    """,
    'depends': ['stock', 'stock_barcode', 'barcodes', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'data/preparation_stock_sequence.xml',
        'data/preparation_stock_stage_data.xml',
        'data/preparation_stock_actions.xml',
        'views/preparation_stock_stage_views.xml',
        'views/preparation_stock_config_views.xml',
        'views/preparation_stock_views.xml',
        'views/preparation_stock_quant_picker_view.xml',
        'views/stock_quant_reservation_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'custom_preparation_stock/static/src/js/preparation_scan_action.js',
            'custom_preparation_stock/static/src/xml/preparation_scan_templates.xml',
            'custom_preparation_stock/static/src/scss/preparation_scan_style.scss',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}

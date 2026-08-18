========================================================================
  MODULO: Movimientos entre Propietarios (custom_owner_stock_change)
========================================================================

DESCRIPCION
-----------
Este modulo permite gestionar la transferencia de mercaderia entre dos
propietarios (res.partner) dentro de la misma instancia de Odoo.

El flujo principal es:
  1. Se crea un movimiento indicando el propietario origen y el destino.
  2. Un operador escanea los productos (via lector de barras o input
     manual) desde la interfaz OWL dedicada.
  3. Los productos escaneados se agrupan en paquetes.
  4. Al finalizar, se generan dos pickings:
       - SALIDA (OUT): mueve la mercaderia desde las ubicaciones de
         escaneo del propietario origen hasta una ubicacion de salida
         (ej: Expedicion).
       - INGRESO (IN): mueve la mercaderia desde la ubicacion de salida
         hasta la ubicacion de entrada del propietario destino, y le
         transfiere la propiedad.

DEPENDENCIAS
------------
  - stock
  - stock_barcode
  - barcodes
  - mail


CONFIGURACION
-------------
Los siguientes parametros se configuran desde:
  Configuracion > Inventario > Movimientos entre Propietarios

  Parametro                          Descripcion
  ---------------------------------- ------------------------------------
  manual_qty                         Permitir modificar cantidades en el
                                     escaneo manualmente.

  allow_delete                       Mostrar boton para eliminar lineas
                                     escaneadas.

  validate_stock                     Validar que las cantidades escaneadas
                                     no superen el stock del propietario
                                     origen (al empaquetar y al finalizar).

  require_packed                     Exigir que todas las lineas tengan
                                     paquete antes de finalizar.

  entry_location                     Ubicacion destino del picking IN.

  exit_location                      Ubicacion de salida para las
                                     mercaderias escaneadas.

  exit_picking_type                  Tipo de operacion (outgoing) para el
                                     picking de salida.

  entry_picking_type                 Tipo de operacion (incoming) para el
                                     picking de ingreso.

ESTRUCTURA DEL MODULO
---------------------
  custom_owner_stock_change/
    __init__.py                     Carga de modelos.
    __manifest__.py                 Dependencias y assets.
    models/
      owner_move_stage.py           Modelo de etapas (Borrador, En espera,
                                    Disponible, Hecho, Rechazado).
      owner_move_stock.py           Modelo principal del movimiento.
                                    Incluye logica de escaneo de barcodes,
                                    generacion de movimientos intermedios,
                                    y validaciones.
      owner_move_stock_line.py      Lineas resumen (por producto) y detalles
                                    individuales de escaneo.
      owner_move_stock_settings.py  Campos de configuracion en
                                    res.config.settings.
    views/
      owner_move_stock_views.xml        Vista tree/form del movimiento y
                                        accion client OWL del escaneo.
      owner_move_stock_settings_views.xml  Bloque de configuracion en
                                           Settings > Inventario.
      owner_move_stage_views.xml        Vista tree/form de etapas.
    data/
      owner_move_stock_sequence.xml     Secuencia numerica (MOVPRPP-XXXXX).
      owner_move_stage_data.xml         Etapas por defecto.
      owner_move_stock_actions.xml      Accion server de validacion
                                        (creacion de pickings OUT/IN).
    security/
      ir.model.access.csv               Permisos de acceso.
    static/src/
      js/owner_move_scan_action.js      Componente OWL de escaneo.
      xml/owner_move_scan_templates.xml Templates OWL.
      scss/owner_move_scan_style.scss   Estilos del escaneo.

NOTAS PARA DESARROLLADORES
---------------------------
- La secuencia se genera con el codigo 'owner.move.stock' y el prefijo
  'MOVPRPP-'.
- El server action de validacion esta protegido con noupdate="1" pero su
  contenido es codigo Python embebido en XML. Cualquier cambio en la logica
  de validacion se debe hacer en data/owner_move_stock_actions.xml.
- El escaneo de barcodes funciona de dos formas simultaneamente:
    a) Via el servicio barcode de Odoo (barcode_scanned event).
    b) Via un buffer de teclado que acumula caracteres y procesa al Enter
- Las computados stored (scanned_qty, package_count, packed, etc.) se
  recalculan automaticamente cuando cambian sus dependencias.

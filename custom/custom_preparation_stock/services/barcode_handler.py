# -*- coding: utf-8 -*-
"""Barcode/OWL processing logic extracted from preparation.stock model.

This service handles all barcode scanning operations, keeping the model clean.
"""
from odoo import _
from odoo.exceptions import UserError
from odoo.tools import float_compare

EPS = 0.0001


class PreparationBarcodeHandler:
    """Handles barcode scanning and OWL communication for preparation.stock."""

    def __init__(self, preparation):
        self.preparation = preparation
        self.env = preparation.env

    def process_barcode_scan(self, barcode, selected_line_id=None):
        """Main entry point from OWL frontend."""
        barcode = (barcode or '').strip()
        if not barcode:
            return {'error': 'Codigo vacio.'}

        # 1) Location by barcode
        location = self.env['stock.location'].sudo().search([
            ('barcode', '=', barcode),
            ('usage', '=', 'internal'),
        ], limit=1)
        if location:
            return self._process_location_scan(location)

        if not self.preparation.current_scan_location_id:
            return {'error': 'Primero escanee una ubicacion.'}

        # 2) Lot by name or ref (restringido al producto de la linea
        #    seleccionada para evitar lotes del mismo codigo en otros productos)
        lot = self._find_lot_for_scan(barcode, selected_line_id)
        if lot:
            line = self._resolve_target_line(lot.product_id, selected_line_id)
            if isinstance(line, dict):
                return line
            return self._add_scan_line(line, lot=lot)

        # 3) Product by barcode or default_code
        product = self.env['product.product'].sudo().search([
            '|', ('barcode', '=', barcode), ('default_code', '=', barcode)
        ], limit=1)
        if product:
            return self._process_product_scan(product, selected_line_id)

        return {'error': 'Codigo no reconocido: %s' % barcode}

    def _process_location_scan(self, location):
        self.preparation.current_scan_location_id = location.id
        return {
            'message': 'Ubicacion cambiada a: %s' % location.complete_name,
            'location_id': location.id,
            'location_name': location.complete_name,
            'is_location': True,
        }

    def _find_lot_for_scan(self, barcode, selected_line_id=None):
        """Busca el lote por name/ref restringido por producto.

        Un mismo codigo de lote puede existir en varios productos: primero se
        intenta dentro del producto de la linea seleccionada en el OWL y, si
        no hay coincidencia (o no hay linea seleccionada), se restringe a los
        productos presentes en las lineas de la preparacion (patron review).
        """
        if selected_line_id:
            selected_line = self.env['preparation.stock.line'].browse(
                selected_line_id
            )
            if selected_line.exists() and selected_line.preparation_id == self.preparation:
                lot = self.env['stock.lot'].sudo().search([
                    '|', ('name', '=', barcode), ('ref', '=', barcode),
                    ('product_id', '=', selected_line.product_id.id),
                ], limit=1)
                if lot:
                    return lot
        product_ids = self.preparation.line_ids.mapped('product_id').ids
        if product_ids:
            lot = self.env['stock.lot'].sudo().search([
                '|', ('name', '=', barcode), ('ref', '=', barcode),
                ('product_id', 'in', product_ids),
            ], limit=1)
            if lot:
                return lot
        return self.env['stock.lot']

    def _resolve_target_line(self, product, selected_line_id=None):
        """Resolve the target demand line for a product."""
        cfg = self.preparation._get_config()

        if selected_line_id:
            selected_line = self.env['preparation.stock.line'].browse(selected_line_id)
            if selected_line.exists() and selected_line.preparation_id == self.preparation:
                if selected_line.product_id != product:
                    return {'error': (
                        'El producto escaneado es %s, pero tiene seleccionada '
                        'la linea de %s. Seleccione la linea correcta antes '
                        'de escanear.'
                    ) % (product.display_name, selected_line.product_id.display_name)}
                if cfg.allow_over_scan:
                    return selected_line
                if selected_line.remaining_qty > EPS:
                    return selected_line
                return {'error': (
                    'El producto %s no tiene demanda pendiente en esta preparacion.'
                ) % product.display_name}

        if cfg.allow_over_scan:
            lines = self.preparation.line_ids.filtered(
                lambda l: l.product_id == product
            ).sorted('id')
        else:
            lines = self.preparation.line_ids.filtered(
                lambda l: l.product_id == product and l.remaining_qty > EPS
            ).sorted('id')
        if not lines:
            return {'error': (
                'El producto %s no tiene demanda pendiente en esta preparacion.'
            ) % product.display_name}
        return lines[0]

    def _get_quants(self, product, lot=False):
        """Available quants for owner in active location (FEFO order)."""
        if not self.preparation.current_scan_location_id:
            return self.env['stock.quant']
        domain = [
            ('location_id', '=', self.preparation.current_scan_location_id.id),
            ('product_id', '=', product.id),
            ('quantity', '>', 0),
        ]
        if lot:
            domain.append(('lot_id', '=', lot.id))
        if self.preparation.owner_id:
            domain.append(('owner_id', '=', self.preparation.owner_id.id))
        return self.env['stock.quant'].sudo().search(domain, order='removal_date, in_date, id')

    def _reservation_conflict(self, line, lot=False, is_rework=False):
        """Conflicto de reserva para escanear 1 unidad mas del lot/producto."""
        StockReservation = self.env['stock.quant.reservation'].sudo()
        already_scanned = sum(
            s.qty for s in line.scan_line_ids
            if s.product_id == line.product_id
            and (s.lot_id.id if s.lot_id else False) == (lot.id if lot else False)
            and (not is_rework or not s.stock_move_id)
        )
        return StockReservation._check_conflict(
            line.product_id.id,
            lot.id if lot else False,
            self.preparation.owner_id.id if self.preparation.owner_id else False,
            self.preparation.id,
            self.preparation.current_scan_location_id.id,
            already_scanned + 1.0,
        )

    def _add_scan_line(self, line, lot=False):
        """Create a scan detail of 1 unit; blocking validations in scan.line create."""
        self.preparation.ensure_one()
        cfg = self.preparation._get_config()
        # En re-proceso los escaneos ya movidos (con stock_move_id) quedaron
        # en la separacion: no deben contar como escaneados de la ubicacion
        # de origen para el chequeo de conflictos de reserva.
        is_rework = getattr(self.preparation, 'is_rework', False)

        # Duplicate lot control
        if lot and cfg.duplicate_lot_applies(self.preparation.owner_id):
            existing = line.scan_line_ids.filtered(
                lambda s, _lot=lot, _prod=line.product_id:
                s.product_id == _prod and s.lot_id == _lot
            )
            if existing:
                total_existing = sum(existing.mapped('qty'))
                return {'error': (
                    'El lote %s del producto %s ya fue escaneado %s vez/veces '
                    '(cantidad total: %s). Modifique la cantidad si es necesario.'
                ) % (lot.name, line.product_id.display_name,
                     len(existing), total_existing)}

        quants = self._get_quants(line.product_id, lot=lot)
        if not quants:
            owner_suffix = ' para el propietario %s' % self.preparation.owner_id.name \
                if self.preparation.owner_id else ''
            if lot:
                other_quants = self._get_quants(line.product_id)
                other_lots = other_quants.filtered('lot_id').mapped('lot_id')
                if other_lots:
                    return {'error': (
                        'El lote %s no se encuentra en %s. '
                        'Lotes disponibles en esta ubicacion: %s.%s'
                    ) % (
                        lot.name,
                        self.preparation.current_scan_location_id.complete_name,
                        ', '.join(other_lots.mapped('name')),
                        owner_suffix,
                    )}
                return {'error': (
                    'El lote %s no tiene stock en %s%s.'
                ) % (
                    lot.name,
                    self.preparation.current_scan_location_id.complete_name,
                    owner_suffix,
                )}
            return {'error': 'No hay stock de %s en %s%s.' % (
                line.product_id.display_name,
                self.preparation.current_scan_location_id.complete_name,
                owner_suffix,
            )}

        quant = quants[0]
        existing = line.scan_line_ids.filtered(
            lambda s: s.quant_id and s.quant_id.id == quant.id
            and (not is_rework or not s.stock_move_id)
        )
        if existing:
            if cfg.duplicate_lot_applies(self.preparation.owner_id):
                return {'error': _(
                    'El quant %s ya fue agregado como escaneo en la linea de %s.'
                ) % (quant.display_name, line.product_id.display_name)}
            if cfg.reservation_applies(self.preparation.owner_id):
                conflict = self._reservation_conflict(line, lot, is_rework)
                if conflict:
                    return {'error': conflict}
            existing = existing[0]
            existing.write({'qty': existing.qty + 1.0})
            return self._build_scan_response(existing, line)

        if cfg.reservation_applies(self.preparation.owner_id):
            conflict = self._reservation_conflict(line, lot, is_rework)
            if conflict:
                return {'error': conflict}

        scan = self.env['preparation.stock.scan.line'].create({
            'preparation_line_id': line.id,
            'product_id': line.product_id.id,
            'location_id': self.preparation.current_scan_location_id.id,
            'lot_id': lot.id if lot else False,
            'package_id': quant.package_id.id if quant.package_id else False,
            'quant_id': quant.id,
            'qty': 1.0,
        })

        return self._build_scan_response(scan, line)

    def _build_scan_response(self, scan, line):
        return {
            'scan_id': scan.id,
            'line_id': line.id,
            'product_name': line.product_id.display_name,
            'lot_name': scan.lot_id.name if scan.lot_id else '',
            'qty': scan.qty,
            'scanned_qty': line.qty_scanned,
            'qty_demanded': line.qty_demanded,
            'remaining_qty': line.remaining_qty,
            'location_id': scan.location_id.id,
            'location_name': scan.location_id.complete_name,
            'state_line': line.state_line,
        }

    def _process_product_scan(self, product, selected_line_id=None):
        """Product without tracking: direct scan. With tracking: resolve lot."""
        line = self._resolve_target_line(product, selected_line_id)
        if isinstance(line, dict):
            return line

        if product.tracking == 'none':
            return self._add_scan_line(line)

        lots_in_location = self._get_quants(product).filtered('lot_id').mapped('lot_id')
        if len(lots_in_location) == 1:
            return self._add_scan_line(line, lot=lots_in_location[0])
        if len(lots_in_location) > 1:
            return {'error': (
                'El producto %s tiene varios lotes en %s. Escanee el lote '
                'especifico. Disponibles: %s'
            ) % (
                product.display_name,
                self.preparation.current_scan_location_id.complete_name,
                ', '.join(lots_in_location.mapped('name')),
            )}
        return {'error': (
            'El producto %s requiere lote pero no hay lotes con stock en %s.'
        ) % (product.display_name, self.preparation.current_scan_location_id.complete_name)}

    def _qty_response(self, scan):
        return {
            'scan_id': scan.id,
            'qty': scan.qty,
            'scanned_qty': scan.preparation_line_id.qty_scanned,
            'remaining_qty': scan.preparation_line_id.remaining_qty,
            'state_line': scan.preparation_line_id.state_line,
        }

    def set_scan_qty(self, scan_line_id, qty):
        scan = self.env['preparation.stock.scan.line'].browse(scan_line_id)
        if not scan.exists():
            return {'error': 'Detalle no encontrado.'}
        prep = scan.preparation_id
        cfg = prep._get_config()
        if prep.state != 'in_progress':
            return {'error': 'La preparacion no esta en estado de escaneo.'}
        if not cfg.manual_qty_applies(prep.owner_id):
            return {'error': 'El ingreso manual de cantidad no esta permitido.'}
        try:
            value = float(qty)
        except (TypeError, ValueError):
            return {'error': 'Cantidad invalida.'}
        if value <= 0:
            return {'error': 'Cantidad invalida.'}
        # En rework: si el renglón ya fue movido a separación y se pide
        # una cantidad MAYOR, registrar el excedente en un renglón nuevo
        # para que la validación lo traslade a separación.
        is_rework = getattr(prep, 'is_rework', False)
        if is_rework and scan.stock_move_id and value > scan.qty:
            delta = round(value - scan.qty, 4)
            if delta > 0:
                new_scan = scan._create_delta_scan(
                    delta,
                    location=(self.preparation.current_scan_location_id
                              or scan.location_id),
                )
                return self._qty_response(new_scan)
        # En rework, un renglon ya trasladado a separacion representa stock
        # fisico alli; bajar o re-escribir su cantidad directo dejaria stock
        # huerfano (excepto escribir el mismo valor, que es un no-op).
        if is_rework and scan.stock_move_id and value < scan.qty:
            return {'error': (
                'Este detalle ya fue trasladado a la separacion y su cantidad '
                'no puede reducirse directamente. Para retirar unidades, baje '
                'la cantidad demandada y use el boton "Gestionar Excedentes".'
            )}
        if is_rework and scan.stock_move_id and float_compare(
                value, scan.qty, precision_digits=4) == 0:
            return self._qty_response(scan)
        try:
            scan.qty = value
        except UserError as error:
            self.env.cr.rollback()
            return {'error': str(error)}
        return self._qty_response(scan)

    def delete_scan_line(self, scan_line_id):
        scan = self.env['preparation.stock.scan.line'].browse(scan_line_id)
        if not scan.exists():
            return {'error': 'Detalle no encontrado.'}
        prep = scan.preparation_id
        cfg = prep._get_config()
        if prep.state != 'in_progress':
            return {'error': 'La preparacion no esta en estado de escaneo.'}
        if not cfg.delete_scan_applies(prep.owner_id):
            return {'error': 'Eliminar lineas escaneadas no esta permitido.'}
        if getattr(prep, 'is_rework', False) and scan.stock_move_id:
            return {'error': (
                'Este detalle ya fue trasladado a la separacion y no puede '
                'eliminarse directamente. Para retirar unidades, baje la '
                'cantidad demandada y use el boton "Gestionar Excedentes".'
            )}
        scan.unlink()
        return {'success': True}

    def get_preparation_state(self):
        """Full state for OWL view (lines + config flags)."""
        prep = self.preparation
        cfg = prep._get_config()
        flags = cfg.get_flags_for_owner(prep.owner_id)
        flags['can_edit_demand'] = flags['edit_demand_in_progress']
        return {
            'exists': True,
            'id': prep.id,
            'name': prep.name,
            'state': prep.state,
            'owner_name': prep.owner_id.name or '',
            'partner_name': prep.partner_id.name or '',
            'dest_location': (
                prep.location_dest_id.complete_name or ''
            ) if prep.location_dest_id else '',
            'current_location': (
                prep.current_scan_location_id.complete_name or ''
            ) if prep.current_scan_location_id else '',
            'is_rework': bool(getattr(prep, 'is_rework', False)),
            'version': getattr(prep, 'version', 1) or 1,
            'fingerprint': self._state_fingerprint(),
            'flags': flags,
            'lines': [
                {
                    'id': line.id,
                    'product_name': line.product_id.display_name,
                    'qty_demanded': line.qty_demanded,
                    'qty_scanned': line.qty_scanned,
                    'remaining_qty': line.remaining_qty,
                    'state_line': line.state_line,
                    'uom_name': line.uom_id.name or '',
                    'locations': [
                        loc.complete_name for loc in line.location_ids
                    ],
                    'scan_lines': [
                        {
                            'id': s.id,
                            'location_name': s.location_id.complete_name,
                            'current_location_name': (
                                getattr(s, 'current_location_name', '')
                                or s.location_id.complete_name
                            ),
                            'moved_to_separation': bool(
                                getattr(s, 'moved_to_separation', False)
                            ),
                            'lot_name': s.lot_id.name or '',
                            'package_name': s.package_id.name or '',
                            'qty': s.qty,
                            'is_expired': s.is_expired,
                        } for s in line.scan_line_ids.sorted('id')
                    ],
                }
                for line in prep.line_ids.sorted('id')
            ],
        }

    def _state_fingerprint(self):
        """Fingerprint estable del estado para polling ligero (sin lineas).

        Cubre lineas, escaneos, movimientos del admin (write_date/location) y
        flags de configuracion, para que el OWL recargue ante cualquier cambio
        real que pueda reflejarse en pantalla.
        """
        prep = self.preparation
        cfg = prep._get_config()
        flags = cfg.get_flags_for_owner(prep.owner_id)
        line_fp = self.env['preparation.stock.line']._read_group(
            [('preparation_id', '=', prep.id)], [],
            ['id:max', 'write_date:max'])
        scan_fp = self.env['preparation.stock.scan.line']._read_group(
            [('preparation_id', '=', prep.id)], [],
            ['id:max', 'write_date:max'])
        return '|'.join([
            str(prep.write_date),
            prep.state or '',
            str(prep.current_scan_location_id.id or 0),
            ';'.join('%s=%s' % (k, v) for k, v in sorted(flags.items())),
            repr(line_fp[0]) if line_fp else '',
            repr(scan_fp[0]) if scan_fp else '',
        ])

    def check_validation(self):
        """Warnings before validate (over/under, expired lots)."""
        prep = self.preparation
        cfg = prep._get_config()
        warnings = []
        if cfg.allow_over_scan:
            for line in prep.line_ids:
                diff = line.qty_demanded - line.qty_scanned
                if float_compare(diff, 0.0, precision_digits=4) > 0:
                    warnings.append(
                        'Faltante en %s: demandado %s, escaneado %s '
                        '(faltan %s).'
                        % (line.product_id.display_name, line.qty_demanded,
                           line.qty_scanned, round(diff, 4))
                    )
                elif float_compare(diff, 0.0, precision_digits=4) < 0:
                    excess = line.qty_scanned - line.qty_demanded
                    warnings.append(
                        'Exceso en %s: escaneado %s, demandado %s '
                        '(exceso: %s).'
                        % (line.product_id.display_name, line.qty_scanned,
                           line.qty_demanded, round(excess, 4))
                    )
        if cfg.control_expired:
            for scan in prep.scan_line_ids.filtered('is_expired'):
                lot_name = scan.lot_id.name if scan.lot_id else '-'
                warnings.append(
                    'Lote vencido: %s (%s) en %s.'
                    % (lot_name, scan.product_id.display_name,
                       scan.location_id.complete_name)
                )
        return {'warnings': warnings}
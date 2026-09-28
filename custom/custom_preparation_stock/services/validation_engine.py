# -*- coding: utf-8 -*-
"""Consolidated validation engine for preparation stock operations.

Centralizes all blocking validations to avoid duplication across models.
"""
from odoo import _
from odoo.exceptions import UserError
from odoo.tools import float_compare


class PreparationValidationEngine:
    """Centralized validation logic for preparation stock."""

    @staticmethod
    def validate_scan_line_create(scan, vals, context=None):
        """Validations on scan line creation.

        Called from @api.model_create_multi where 'scan' is an empty recordset.
        Must get preparation from vals, not from scan.preparation_id.
        """
        context = context or {}
        if not context.get('scan_auto_sync_demand'):
            return
        line_id = vals.get('preparation_line_id')
        if not line_id:
            return
        line = scan.env['preparation.stock.line'].browse(line_id)
        if not line.exists():
            return
        cfg = line.preparation_id._get_config()
        if not cfg.manual_scan_create_applies(line.preparation_id.owner_id):
            raise UserError(_('La creacion manual de lineas de escaneo no esta habilitada para este propietario.'))

    @staticmethod
    def validate_scan_line_write(scan, vals, context=None):
        """Validations on scan line write."""
        context = context or {}
        prep = scan.preparation_id
        cfg = prep._get_config()

        if context.get('scan_auto_sync_demand'):
            if not cfg.manual_scan_create_applies(prep.owner_id):
                raise UserError(_('La creacion manual de lineas de escaneo no esta habilitada para este propietario.'))

        # Durante un re-proceso, un renglon con stock_move_id representa stock
        # ya trasladado fisicamente a la separacion: queda inmutable fuera del
        # wizard de excedentes (unico flujo que reduce/elimina el renglon y
        # mueve el stock fisico a la vez).
        for item in scan:
            if getattr(item.preparation_id, 'is_rework', False) \
                    and item.stock_move_id and not context.get('excess_wizard'):
                raise UserError(_(
                    'El detalle de %s ya fue trasladado a la separacion; su '
                    'cantidad y datos no pueden modificarse directamente. Para '
                    'retirar o reducir unidades, baje la cantidad demandada y '
                    'use el boton "Gestionar Excedentes".'
                ) % (item.product_id.display_name or '-'))

    @staticmethod
    def validate_scan_line_operations(scans):
        """Blocking validations for scan lines (create/write)."""
        for scan in scans:
            prep = scan.preparation_id
            cfg = prep._get_config()

            import_context = scan._context.get('prep_import_reception', False)
            if prep.state in ('done', 'cancelled') or not prep.active:
                raise UserError(_(
                    'La preparacion esta finalizada, cancelada o archivada; no se '
                    'pueden agregar ni modificar escaneos.'
                ))
            if import_context:
                if getattr(prep, 'is_rework', False):
                    raise UserError(_(
                        'No se permite importar desde recepciones en una preparacion de reproceso.'
                    ))
                if prep.state not in ('draft', 'assigned', 'in_progress', 'to_validate'):
                    raise UserError(_('La preparacion no permite importar escaneos en su estado actual.'))
            else:
                if prep.state not in ('in_progress', 'to_validate') \
                        and not scan._manual_test_allowed():
                    raise UserError(_('La preparacion no esta en estado de escaneo.'))

                if prep.state == 'to_validate' and not scan._manual_test_allowed():
                    if not getattr(prep, 'is_rework', False):
                        if not prep.can_edit_in_progress:
                            raise UserError(_(
                                'La preparacion esta Para Validar: solo un '
                                'administrativo puede modificar los detalles escaneados.'
                            ))
                        if not prep.can_add_scan_lines_manual and isinstance(scan.id, type(scan.env['preparation.stock.scan.line'].new().id)):
                            raise UserError(_(
                                'La creacion manual de lineas de escaneo '
                                'no esta habilitada para esta preparacion.'
                            ))

            line = scan.preparation_line_id

            # Duplicate quant validation
            if 'quant_id' in (scan._fields if hasattr(scan, '_fields') else {}) and scan.quant_id \
                    and cfg.duplicate_lot_applies(prep.owner_id):
                # En rework los renglones con stock_move_id representan stock
                # ya trasladado a separacion (con un cuant de origen "stale"):
                # no deben bloquear la captura de nuevas unidades del mismo
                # cuant/lote desde la ubicacion de origen.
                is_rework = getattr(prep, 'is_rework', False)
                dupe = line.scan_line_ids.filtered(
                    lambda s: s.id != scan.id and s.quant_id
                    and s.quant_id.id == scan.quant_id.id
                    and (not is_rework or not s.stock_move_id)
                    and (
                        not import_context
                        or (
                            s.reception_move_line_id
                            and scan.reception_move_line_id
                            and s.reception_move_line_id == scan.reception_move_line_id
                        )
                    )
                )
                if dupe:
                    raise UserError(_(
                        'El quant elegido ya fue agregado como escaneo en la '
                        'linea de %s.'
                    ) % line.product_id.display_name)

            # Location allowed validation
            if line.location_ids and scan.location_id not in line.location_ids:
                raise UserError(_(
                    'La ubicacion %s no esta entre las ubicaciones elegidas '
                    'para %s.'
                ) % (scan.location_id.complete_name, line.product_id.display_name))

    @staticmethod
    def check_quant_availability_optimized(scans):
        """Optimized stock availability check using _read_group.

        Replaces N+1 queries with a single grouped query.
        Also considers reservations from other preparations.

        During rework (is_rework=True), scan lines that already have a
        stock_move_id are excluded from the check.  Those lines represent
        stock that was physically moved during the original validation and
        is no longer at the source location referenced by the scan line.
        Only new scan lines (without stock_move_id) are checked against
        source-location availability.
        """
        grouped_cache = {}
        for scan in scans:
            prep = scan.preparation_id
            cfg = prep._get_config()
            if not cfg.quant_control_applies(prep.owner_id):
                continue
            if prep.id not in grouped_cache:
                # Build aggregated query for all scan lines in this preparation
                if not prep.scan_line_ids:
                    grouped_cache[prep.id] = {}
                    continue

                # Prepare data for _read_group
                # En re-proceso, excluir escaneos ya movidos (con
                # stock_move_id): su stock fisico ya no esta en la
                # ubicacion de origen, sino en la separacion.
                domain = [
                    ('preparation_id', '=', prep.id),
                ]
                if getattr(prep, 'is_rework', False):
                    domain.append(('stock_move_id', '=', False))

                groupby = ['product_id', 'location_id', 'lot_id']
                fields = ['qty:sum']
                grouped_data = prep.scan_line_ids._read_group(domain, groupby, fields)

                grouped = {}
                for product, location, lot, qty_sum in grouped_data:
                    key = (product.id, location.id, lot.id if lot else False)
                    grouped[key] = qty_sum
                grouped_cache[prep.id] = grouped

            grouped = grouped_cache[prep.id]

            # Check availability using bulk quant search
            for key, scanned_total in grouped.items():
                product_id, location_id, lot_id = key
                quant_domain = [
                    ('product_id', '=', product_id),
                    ('location_id', '=', location_id),
                    ('quantity', '>', 0),
                ]
                if lot_id:
                    quant_domain.append(('lot_id', '=', lot_id))
                else:
                    quant_domain.append(('lot_id', '=', False))
                if prep.owner_id:
                    quant_domain.append(('owner_id', '=', prep.owner_id.id))

                available = sum(
                    scan.env['stock.quant'].sudo().search(quant_domain).mapped('quantity')
                )

                # Restar reservas de OTRAS preparaciones para el mismo producto/lote/owner
                reservation_domain = [
                    ('product_id', '=', product_id),
                    ('lot_id', '=', lot_id or False),
                    ('owner_id', '=', prep.owner_id.id if prep.owner_id else False),
                    ('state', '=', 'reserved'),
                    ('preparation_id', '!=', prep.id),
                ]
                other_reservations = sum(
                    scan.env['stock.quant.reservation'].sudo().search(
                        reservation_domain
                    ).mapped('qty')
                )
                available -= other_reservations
                available = max(available, 0.0)

                if float_compare(scanned_total, available, precision_digits=4) > 0:
                    product = scan.env['product.product'].browse(product_id)
                    location = scan.env['stock.location'].browse(location_id)
                    label = product.display_name
                    if lot_id:
                        label += ' (Lote %s)' % scan.env['stock.lot'].sudo().browse(lot_id).name
                    raise UserError(_(
                        'Existencias insuficientes para %s en %s.\n'
                        'Escaneado/acumulado: %s | Disponible: %s.\n'
                        '(Incluye descuento por reservas de otras preparaciones: %s)'
                    ) % (label, location.complete_name, scanned_total, available,
                         other_reservations))

    @staticmethod
    def validate_preparation_line_write(line, vals, context=None):
        """Validations on preparation line write."""
        context = context or {}
        protected_fields = {'qty_demanded', 'location_ids'}
        if protected_fields & set(vals.keys()) and not context.get('fefo_assign'):
            # Durante un re-proceso (is_rework) la edicion de demanda/ubicaciones
            # queda abierta para poder volver a preparar, sin pasar por
            # edit_demand_in_progress. La autorizacion de reapertura es el gate.
            if getattr(line.preparation_id, 'is_rework', False):
                return
            line._check_edit_in_progress()

    @staticmethod
    def validate_preparation_line_unlink(lines):
        """Validations on preparation line deletion."""
        preps = lines.mapped('preparation_id')
        for prep in preps:
            if prep.state in ('done', 'cancelled'):
                raise UserError(_(
                    'No se pueden eliminar lineas de una preparacion finalizada.'
                ))

    @staticmethod
    def validate_scan_line_unlink(scans, context=None):
        """Validations on scan line deletion."""
        context = context or {}
        for scan in scans:
            prep = scan.preparation_id
            cfg = prep._get_config()
            if prep.state not in ('in_progress', 'to_validate') \
                    and not scan._manual_test_allowed():
                raise UserError(_(
                    'Solo se pueden eliminar lineas escaneadas con la '
                    'preparacion En Proceso o Para Validar.'
                ))
            if prep.state == 'to_validate' and not scan._manual_test_allowed():
                if not getattr(prep, 'is_rework', False):
                    if not cfg.edit_demand_applies(prep.owner_id):
                        raise UserError(_(
                            'La preparacion esta Para Validar: solo un '
                            'administrativo puede eliminar detalles escaneados.'
                        ))
            # En re-proceso, no se puede eliminar un renglon ya trasladado a
            # la separacion: quedaria stock fisico huerfano. Usar el wizard.
            if getattr(prep, 'is_rework', False) \
                    and scan.stock_move_id and not context.get('excess_wizard'):
                raise UserError(_(
                    'El detalle de %s ya fue trasladado a la separacion y no '
                    'puede eliminarse directamente. Para retirar esas '
                    'unidades, baje la cantidad demandada y use el boton '
                    '"Gestionar Excedentes".'
                ) % (scan.product_id.display_name or '-'))

    @staticmethod
    def validate_mark_to_validate(preparation):
        """Validations for action_mark_to_validate."""
        cfg = preparation._get_config()
        eps = 0.0001

        # Validacion universal: siempre exigir que las lineas con demanda
        # tengan al menos algo escaneado, sin importar allow_over_scan.
        no_scan_diffs = []
        for line in preparation.line_ids:
            if float_compare(line.qty_demanded, 0.0, precision_digits=4) > 0:
                if float_compare(line.qty_scanned, 0.0, precision_digits=4) <= 0:
                    no_scan_diffs.append(
                        '%s: demandado %s, escaneado 0'
                        % (line.product_id.display_name, line.qty_demanded)
                    )
        if no_scan_diffs:
            raise UserError(
                'Existen lineas con demanda pendiente sin escanear.\n'
                + '\n'.join(no_scan_diffs)
                + '\n\nEscanee las cantidades antes de continuar.'
            )

        diffs = []
        if not cfg.allow_over_scan:
            for line in preparation.line_ids:
                diff = line.qty_demanded - line.qty_scanned
                if abs(diff) > eps:
                    diffs.append(
                        '%s: demandado %s, escaneado %s (diferencia: %s)'
                        % (line.product_id.display_name,
                           line.qty_demanded, line.qty_scanned, round(diff, 4))
                    )
        if diffs:
            raise UserError(
                'Las cantidades demandadas y escaneadas no coinciden.\n'
                + '\n'.join(diffs)
                + '\n\nCorrija las cantidades antes de continuar.'
            )

    @staticmethod
    def validate_action_validate(preparation):
        """Validations for action_validate (server action)."""
        cfg = preparation._get_config()
        if cfg.block_validate_over_under:
            eps = 0.0001
            diffs = []
            for line in preparation.line_ids:
                diff = line.qty_demanded - line.qty_scanned
                if abs(diff) > eps:
                    diffs.append(
                        '%s: demandado %s, escaneado %s (diferencia: %s)'
                        % (line.product_id.display_name,
                           line.qty_demanded, line.qty_scanned, round(diff, 4))
                    )
            if diffs:
                raise UserError(
                    'Las cantidades demandadas y escaneadas no coinciden.\n'
                    + '\n'.join(diffs)
                    + '\n\nCorrija las cantidades antes de validar.'
                )
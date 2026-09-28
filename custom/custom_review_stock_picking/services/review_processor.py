# -*- coding: utf-8 -*-
"""Barcode and scan processing logic extracted from review.stock.picking.

This service handles all barcode scanning operations, manual counts,
packaging, and validation, keeping the model clean.
"""
from odoo import fields, _
from odoo.exceptions import UserError
from odoo.tools import float_compare

from .config_cache import get_cached_config

EPS = 0.0001


class ReviewProcessor:
    """Handles barcode scanning, manual counts, packaging and OWL communication."""

    def __init__(self, review):
        self.review = review
        self.env = review.env

    # ==================================================================
    # BARCODE PROCESSING
    # ==================================================================

    def process_barcode(self, barcode):
        """Main entry point: resolve location -> lot -> product."""
        barcode = (barcode or '').strip()
        if not barcode:
            return {'error': 'Codigo vacio.'}

        location = self.env['stock.location'].sudo().search([
            ('barcode', '=', barcode),
            ('usage', '=', 'internal'),
        ], limit=1)
        if location:
            return self._process_location_scan(location)

        if not self.review.current_scan_location_id:
            return {'error': 'Primero escanee la ubicacion de separacion.'}

        review_product_ids = self.review.move_ids.mapped('product_id').ids
        lot = self.env['stock.lot'].sudo().search([
            '|',
            ('name', '=', barcode),
            ('ref', '=', barcode),
            ('product_id', 'in', review_product_ids),
        ], limit=1)
        if lot:
            return self._scan_lot_reviewed(lot)

        product = self.env['product.product'].sudo().search([
            '|',
            ('barcode', '=', barcode),
            ('default_code', '=', barcode),
        ], limit=1)
        if product:
            return self._scan_product_reviewed(product)

        return {'error': 'Codigo no reconocido: %s' % barcode}

    # ==================================================================
    # LOCATION SCANNING
    # ==================================================================

    def _process_location_scan(self, location):
        """Validate and assign scan location."""
        expected = self.review.preparation_id.location_dest_id
        if expected and location.id != expected.id:
            return {
                'error': 'La ubicacion %s no coincide con la '
                'ubicacion de separacion esperada (%s).'
                % (location.complete_name, expected.complete_name),
                '_scan_action': 'location',
            }
        self.review.current_scan_location_id = location.id
        return {
            'message': 'Ubicacion verificada: %s' % location.complete_name,
            'location_id': location.id,
            'location_name': location.complete_name,
            'is_location': True,
            '_scan_action': 'location',
        }

    # ==================================================================
    # LOT SCANNING
    # ==================================================================

    def _preparation_lot_ids_by_product(self):
        """Map {product_id: set(lot_ids)} from preparation scan lines."""
        res = {}
        for scan in self.review.preparation_id.scan_line_ids:
            if scan.lot_id:
                res.setdefault(scan.product_id.id, set()).add(scan.lot_id.id)
        return res

    def _find_scan_line(self, target_move, lot=False, is_foreign=False):
        """Find or create a normal (non-excess) move line for a lot/product."""
        if lot:
            existing = target_move.move_line_ids.filtered(
                lambda l: l.lot_id == lot
                and not l.package_id and not l.is_excess
            )
            if not existing:
                existing = target_move.move_line_ids.filtered(
                    lambda l: l.lot_id == lot and not l.is_excess
                )
        else:
            existing = target_move.move_line_ids.filtered(
                lambda l: not l.lot_id and not l.package_id and not l.is_excess
            )
            if not existing:
                existing = target_move.move_line_ids.filtered(
                    lambda l: not l.lot_id and not l.is_excess
                )
        if existing:
            return existing[0]
        vals = {
            'review_move_id': target_move.id,
            'owner_id': self.review.owner_id.id if self.review.owner_id else False,
            'qty': 0.0,
            'qty_reviewed': 0.0,
            'state': 'done',
            'is_foreign': is_foreign,
        }
        if lot:
            vals['lot_id'] = lot.id
        return self.env['review.move.line'].with_context(
            review_auto_create=True
        ).create(vals)

    def _scan_lot_reviewed(self, lot, qty_step=1.0):
        """Accumulate reviewed quantity for a lot in its move line."""
        self.review.ensure_one()
        cfg = get_cached_config(self.env, self.review.company_id.id)
        target_move = self.review.move_ids.filtered(
            lambda m: m.product_id == lot.product_id
        )
        if not target_move:
            return {
                'error': 'El producto del lote %s no pertenece a este repaso.'
                % lot.name,
                '_scan_action': 'lot',
                '_lot_id': lot.id,
            }
        target_move = target_move[0]

        allowed_lots = self._preparation_lot_ids_by_product()
        is_foreign = lot.id not in allowed_lots.get(lot.product_id.id, set())
        if is_foreign and cfg.block_foreign_scans_applies(self.review.owner_id):
            return {
                'error': 'El lote %s del producto %s no pertenece a la '
                'preparacion de este repaso.'
                % (lot.name, target_move.product_id.display_name),
                '_scan_action': 'lot',
                '_lot_id': lot.id,
                '_product_id': target_move.product_id.id,
            }

        if not cfg.duplicate_lots_applies(self.review.owner_id):
            already_scanned = target_move.move_line_ids.filtered(
                lambda l: l.lot_id == lot
                and float_compare(l.qty_reviewed, 0.0, precision_digits=4) > 0
            )
            if already_scanned:
                return {
                    'error': 'El lote %s del producto %s ya fue escaneado. '
                    'Lotes duplicados no estan permitidos.'
                    % (lot.name, target_move.product_id.display_name),
                    '_scan_action': 'lot',
                    '_lot_id': lot.id,
                    '_product_id': target_move.product_id.id,
                }

        already_reviewed = sum(
            ml.qty_reviewed for ml in target_move.move_line_ids
        )
        expected = target_move.qty_preparation
        would_be = already_reviewed + qty_step
        if (cfg.qty_differences_applies(self.review.owner_id)
                and float_compare(would_be, expected, precision_digits=4) > 0):
            return {
                'error': 'El producto %s superaria la cantidad preparada '
                '(%s). El control de diferencias esta activado.'
                % (target_move.product_id.display_name, expected),
                '_scan_action': 'lot',
                '_lot_id': lot.id,
                '_product_id': target_move.product_id.id,
            }

        normal_add = max(min(qty_step, expected - already_reviewed), 0.0)
        excess_add = qty_step - normal_add

        if cfg.stock_availability_applies(self.review.owner_id):
            stock_err = self._stock_availability_error(
                target_move, {lot.id: qty_step}
            )
            if stock_err:
                return {
                    'error': stock_err,
                    '_scan_action': 'lot',
                    '_lot_id': lot.id,
                    '_product_id': target_move.product_id.id,
                }

        dest = self.review.preparation_id.location_dest_id
        if dest:
            res_err = self.reservation_availability_error(
                self.review, target_move, lot.id, qty_step
            )
            if res_err:
                return {
                    'error': res_err,
                    '_scan_action': 'lot',
                    '_lot_id': lot.id,
                    '_product_id': target_move.product_id.id,
                }

        ml = self.env['review.move.line']
        if float_compare(normal_add, 0.0, precision_digits=4) > 0:
            ml = self._find_scan_line(target_move, lot=lot, is_foreign=is_foreign)
            ml.with_context(review_auto_create=True).write({
                'qty_reviewed': ml.qty_reviewed + normal_add,
                'state': 'done',
            })
        if float_compare(excess_add, 0.0, precision_digits=4) > 0:
            excess_line = target_move.move_line_ids.filtered(
                lambda l: l.lot_id == lot and l.is_excess
            )
            if not excess_line:
                excess_line = self.env['review.move.line'].with_context(
                    review_auto_create=True
                ).create({
                    'review_move_id': target_move.id,
                    'lot_id': lot.id,
                    'owner_id': (
                        self.review.owner_id.id
                        if self.review.owner_id else False
                    ),
                    'qty': 0.0,
                    'qty_reviewed': 0.0,
                    'state': 'done',
                    'is_excess': True,
                    'is_foreign': is_foreign,
                })
            else:
                excess_line = excess_line[0]
            excess_line.with_context(review_auto_create=True).write({
                'qty_reviewed': excess_line.qty_reviewed + excess_add,
                'state': 'done',
            })
            ml = excess_line

        move_reviewed = sum(
            l.qty_reviewed for l in target_move.move_line_ids
        )
        warning = ''
        if is_foreign:
            warning = 'Producto fuera de la preparacion: %s (lote %s).' % (
                target_move.product_id.display_name, lot.name)
        elif excess_add > EPS:
            warning = (
                'Exceso en %s: se repaso %s por encima de la cantidad '
                'preparada.'
            ) % (target_move.product_id.display_name, round(excess_add, 4))

        return {
            'product_name': target_move.product_id.display_name,
            'lot_name': lot.name,
            'qty': ml.qty if ml else 0.0,
            'qty_reviewed': move_reviewed,
            'remaining_qty': max(expected - move_reviewed, 0.0),
            'warning': warning,
            'move_id': target_move.id,
            '_scan_action': 'lot',
            '_lot_id': lot.id,
            '_product_id': target_move.product_id.id,
        }

    # ==================================================================
    # PRODUCT SCANNING
    # ==================================================================

    def _scan_product_reviewed(self, product, qty_step=1.0):
        """Accumulate reviewed quantity for a product (no lot)."""
        self.review.ensure_one()
        cfg = get_cached_config(self.env, self.review.company_id.id)
        target_move = self.review.move_ids.filtered(
            lambda m: m.product_id == product
        )
        if not target_move:
            return {
                'error': 'El producto %s no pertenece a este repaso.'
                % product.display_name,
                '_scan_action': 'product',
                '_product_id': product.id,
            }
        target_move = target_move[0]

        if not cfg.duplicate_lots_applies(self.review.owner_id):
            already_scanned = target_move.move_line_ids.filtered(
                lambda l: not l.lot_id
                and float_compare(l.qty_reviewed, 0.0, precision_digits=4) > 0
            )
            if already_scanned:
                return {
                    'error': 'El producto %s ya fue escaneado. '
                    'Lotes duplicados no estan permitidos.'
                    % product.display_name,
                    '_scan_action': 'product',
                    '_product_id': product.id,
                }

        already_reviewed = sum(
            ml.qty_reviewed for ml in target_move.move_line_ids
        )
        expected = target_move.qty_preparation
        would_be = already_reviewed + qty_step
        if (cfg.qty_differences_applies(self.review.owner_id)
                and float_compare(would_be, expected, precision_digits=4) > 0):
            return {
                'error': 'El producto %s superaria la cantidad preparada '
                '(%s). El control de diferencias esta activado.'
                % (product.display_name, expected),
                '_scan_action': 'product',
                '_product_id': product.id,
            }

        normal_add = max(min(qty_step, expected - already_reviewed), 0.0)
        excess_add = qty_step - normal_add

        if cfg.stock_availability_applies(self.review.owner_id):
            stock_err = self._stock_availability_error(
                target_move, {False: qty_step}
            )
            if stock_err:
                return {
                    'error': stock_err,
                    '_scan_action': 'product',
                    '_product_id': product.id,
                }

        dest = self.review.preparation_id.location_dest_id
        if dest:
            available = self.env['stock.quant.reservation'].sudo() \
                ._get_available_for_prep(
                    product.id, False,
                    self.review.owner_id.id if self.review.owner_id else False,
                    self.review.preparation_id.id, dest.id,
                )
            if float_compare(
                already_reviewed + qty_step, available, precision_digits=4
            ) > 0:
                return {
                    'error': (
                        'No hay stock suficiente de %s disponible '
                        'para esta preparacion.\n'
                        'Disponible: %s | Intenta repasar: %s.\n'
                        'El resto esta reservado por otras preparaciones.'
                    ) % (
                        product.display_name,
                        round(available, 4),
                        round(already_reviewed + qty_step, 4),
                    ),
                    '_scan_action': 'product',
                    '_product_id': product.id,
                }

        ml = self.env['review.move.line']
        if float_compare(normal_add, 0.0, precision_digits=4) > 0:
            ml = self._find_scan_line(target_move, lot=False)
            ml.with_context(review_auto_create=True).write({
                'qty_reviewed': ml.qty_reviewed + normal_add,
                'state': 'done',
            })
        if float_compare(excess_add, 0.0, precision_digits=4) > 0:
            excess_line = target_move.move_line_ids.filtered(
                lambda l: not l.lot_id and l.is_excess
            )
            if not excess_line:
                excess_line = self.env['review.move.line'].with_context(
                    review_auto_create=True
                ).create({
                    'review_move_id': target_move.id,
                    'owner_id': (
                        self.review.owner_id.id
                        if self.review.owner_id else False
                    ),
                    'qty': 0.0,
                    'qty_reviewed': 0.0,
                    'state': 'done',
                    'is_excess': True,
                })
            else:
                excess_line = excess_line[0]
            excess_line.with_context(review_auto_create=True).write({
                'qty_reviewed': excess_line.qty_reviewed + excess_add,
                'state': 'done',
            })
            ml = excess_line

        move_reviewed = sum(
            l.qty_reviewed for l in target_move.move_line_ids
        )
        warning = ''
        if excess_add > EPS:
            warning = (
                'Exceso en %s: se repaso %s por encima de la cantidad '
                'preparada.'
            ) % (product.display_name, round(excess_add, 4))

        return {
            'product_name': target_move.product_id.display_name,
            'lot_name': '',
            'qty': ml.qty if ml else 0.0,
            'qty_reviewed': move_reviewed,
            'remaining_qty': max(expected - move_reviewed, 0.0),
            'warning': warning,
            'move_id': target_move.id,
            '_scan_action': 'product',
            '_product_id': product.id,
        }

    # ==================================================================
    # STOCK AVAILABILITY CHECKS
    # ==================================================================

    def _stock_availability_error(self, target_move, extra_by_lot):
        """Return error string if total reviewed exceeds available stock."""
        self.review.ensure_one()
        dest = self.review.preparation_id.location_dest_id
        if not dest:
            return ''
        product = target_move.product_id
        totals_by_lot = {}
        for ml in target_move.move_line_ids:
            key = ml.lot_id.id if ml.lot_id else False
            totals_by_lot[key] = totals_by_lot.get(key, 0.0) + ml.qty_reviewed
        for key, extra in extra_by_lot.items():
            totals_by_lot[key] = totals_by_lot.get(key, 0.0) + extra
        for key, total in totals_by_lot.items():
            if float_compare(total, 0.0, precision_digits=4) <= 0:
                continue
            lot = self.env['stock.lot'].browse(key)
            available = self._quantity_available_at(product, dest, lot=lot)
            if float_compare(total, available, precision_digits=4) > 0:
                if lot:
                    label = '%s (lote %s)' % (product.display_name, lot.name)
                else:
                    label = product.display_name
                return (
                    'Existencias insuficientes para %s en %s: se repasaria '
                    '%s pero solo hay %s disponible.'
                    % (
                        label, dest.complete_name,
                        round(total, 4), round(available, 4),
                    )
                )
        return ''

    def _quantity_available_at(self, product, dest, lot=False):
        """Stock disponible en la ubicacion de separacion para el producto/lote.

        Cuenta quants sueltos Y empaquetados (tras el re-proceso la reversa
        preserva los paquetes en destino) y descuenta la cantidad reservada
        nativa mas las reservas logicas de OTRAS preparaciones.
        """
        quant_model = self.env['stock.quant'].sudo()
        q_domain = [
            ('product_id', '=', product.id),
            ('location_id', '=', dest.id),
            ('quantity', '!=', 0.0),
        ]
        if lot:
            q_domain.append(('lot_id', '=', lot.id))
        else:
            q_domain.append(('lot_id', '=', False))
        if self.review.owner_id:
            q_domain.append(('owner_id', '=', self.review.owner_id.id))
        quants = quant_model.search(q_domain)
        available = (
            sum(quants.mapped('quantity'))
            - sum(quants.mapped('reserved_quantity'))
        )
        res_domain = [
            ('product_id', '=', product.id),
            ('lot_id', '=', lot.id if lot else False),
            ('owner_id', '=', (
                self.review.owner_id.id if self.review.owner_id else False
            )),
            ('state', '=', 'reserved'),
            ('preparation_id', '!=', self.review.preparation_id.id),
        ]
        other_res = sum(
            self.env['stock.quant.reservation'].sudo().search(
                res_domain
            ).mapped('qty')
        )
        return max(available - other_res, 0.0)

    @staticmethod
    def reservation_availability_error(review, move, lot_key, extra):
        """Check if total reviewed exceeds available for this preparation."""
        dest = review.preparation_id.location_dest_id
        if not dest:
            return ''
        lot_key = lot_key or False
        available = review.env['stock.quant.reservation'].sudo() \
            ._get_available_for_prep(
                move.product_id.id,
                lot_key,
                review.owner_id.id if review.owner_id else False,
                review.preparation_id.id,
                dest.id,
            )
        lot_total = sum(
            ml.qty_reviewed for ml in move.move_line_ids
            if (ml.lot_id.id if ml.lot_id else False) == lot_key
        ) + extra
        if float_compare(lot_total, available, precision_digits=4) <= 0:
            return ''
        lot = review.env['stock.lot'].browse(lot_key) if lot_key else False
        if lot:
            label = '%s (lote %s)' % (move.product_id.display_name, lot.name)
        else:
            label = move.product_id.display_name
        return (
            'No hay stock suficiente de %s disponible '
            'para esta preparacion.\n'
            'Disponible: %s | Intentado: %s.\n'
            'El resto esta reservado por otras preparaciones.'
            % (label, round(available, 4), round(lot_total, 4))
        )

    # ==================================================================
    # MANUAL COUNT
    # ==================================================================

    def set_manual_count(self, move_line_id, qty_reviewed):
        """Set reviewed quantity for a move line (manual count)."""
        review = self.review
        cfg = get_cached_config(self.env, review.company_id.id)
        if not cfg.manual_counts_applies(review.owner_id):
            return {'error': 'Los conteos manuales no estan habilitados para este propietario.'}

        ml = self.env['review.move.line'].browse(move_line_id)
        if not ml.exists() or ml.review_move_id.review_id.id != review.id:
            return {'error': 'La linea no pertenece a este repaso.'}
        try:
            qty = float(qty_reviewed or 0.0)
        except (TypeError, ValueError):
            return {'error': 'Cantidad invalida.'}
        if float_compare(qty, 0.0, precision_digits=4) < 0:
            return {'error': 'La cantidad repasada no puede ser negativa.'}

        move = ml.review_move_id
        expected = move.qty_preparation

        if ml.is_excess or ml.is_foreign:
            lot_key = ml.lot_id.id if ml.lot_id else False
            delta = qty - ml.qty_reviewed
            if float_compare(delta, 0.0, precision_digits=4) > 0:
                if cfg.stock_availability_applies(review.owner_id):
                    stock_err = self._stock_availability_error(
                        move, {lot_key: delta}
                    )
                    if stock_err:
                        return {'error': stock_err}
                res_err = self.reservation_availability_error(
                    review, move, lot_key, delta
                )
                if res_err:
                    return {'error': res_err}
            ml.with_context(review_auto_create=True).write({
                'qty_reviewed': qty,
                'state': 'done'
                if float_compare(qty, 0.0, precision_digits=4) > 0
                else 'pending',
            })
        else:
            other_normal = sum(
                l.qty_reviewed for l in move.move_line_ids
                if not l.is_excess and not l.is_foreign and l.id != ml.id
            )
            new_normal = max(min(qty, expected - other_normal), 0.0)
            excess_qty = max(qty - new_normal, 0.0)
            if (float_compare(excess_qty, 0.0, precision_digits=4) > 0
                    and cfg.qty_differences_applies(review.owner_id)):
                return {
                    'error': 'La cantidad repasada supera la esperada (%s).'
                    % expected
                }

            lot_key = ml.lot_id.id if ml.lot_id else False
            excess_candidates = move.move_line_ids.filtered(
                lambda l: l.lot_id == ml.lot_id
                and l.is_excess and not l.is_foreign
            )
            excess_current = sum(excess_candidates.mapped('qty_reviewed'))
            lot_delta = (new_normal - ml.qty_reviewed) \
                + (excess_qty - excess_current)

            if float_compare(lot_delta, 0.0, precision_digits=4) > 0:
                if cfg.stock_availability_applies(review.owner_id):
                    stock_err = self._stock_availability_error(
                        move, {lot_key: lot_delta}
                    )
                    if stock_err:
                        return {'error': stock_err}
                res_err = self.reservation_availability_error(
                    review, move, lot_key, lot_delta
                )
                if res_err:
                    return {'error': res_err}

            ml.with_context(review_auto_create=True).write({
                'qty_reviewed': new_normal,
                'state': 'done'
                if float_compare(new_normal, 0.0, precision_digits=4) > 0
                else 'pending',
                'is_excess': False,
            })

            if float_compare(excess_qty, 0.0, precision_digits=4) > 0:
                if not excess_candidates:
                    excess_candidates = self.env['review.move.line'].with_context(
                        review_auto_create=True
                    ).create({
                        'review_move_id': move.id,
                        'lot_id': ml.lot_id.id,
                        'owner_id': ml.owner_id.id if ml.owner_id else False,
                        'qty': 0.0,
                        'qty_reviewed': 0.0,
                        'state': 'done',
                        'is_excess': True,
                        'is_foreign': False,
                    })
                else:
                    excess_candidates = excess_candidates[0]
                excess_candidates.with_context(review_auto_create=True).write({
                    'qty_reviewed': excess_qty,
                    'state': 'done',
                })
            elif excess_candidates:
                excess_candidates.with_context(review_auto_create=True).write({
                    'qty_reviewed': 0.0,
                    'state': 'pending',
                })

        move_reviewed = sum(
            l.qty_reviewed for l in move.move_line_ids
        )
        return {
            'product_name': move.product_id.display_name,
            'qty_reviewed': move_reviewed,
            'remaining_qty': max(expected - move_reviewed, 0.0),
        }

    # ==================================================================
    # PACKAGING
    # ==================================================================

    def _package_dict(self, package):
        return {
            'id': package.id,
            'name': package.name or '',
            'shipping_weight': package.shipping_weight,
            'package_type_id': (
                package.package_type_id.id
                if package.package_type_id else False
            ),
            'package_type_name': (
                package.package_type_id.name
                if package.package_type_id else ''
            ),
        }

    def _assign_lines_to_package(self, lines, package):
        """Assign reviewed lines to a package, splitting remainders."""
        for line in lines.sudo():
            original_qty = line.qty
            reviewed_qty = line.qty_reviewed
            line.with_context(review_auto_create=True).write({
                'package_id': package.id,
                'qty': reviewed_qty,
                'state': 'done',
            })
            remainder = original_qty - reviewed_qty
            if remainder > EPS:
                self.env['review.move.line'].with_context(
                    review_auto_create=True
                ).create({
                    'review_move_id': line.review_move_id.id,
                    'stock_move_line_id': line.stock_move_line_id.id,
                    'lot_id': line.lot_id.id,
                    'owner_id': line.owner_id.id,
                    'qty': remainder,
                    'qty_reviewed': 0,
                    'state': 'pending',
                    'is_excess': line.is_excess,
                    'is_foreign': line.is_foreign,
                })

    def create_package(self, move_line_ids, package_type_id=False,
                       shipping_weight=0.0):
        """Create a new package and assign reviewed lines."""
        review = self.review
        from .validation_engine import ReviewValidationEngine
        lines, error = ReviewValidationEngine.validate_package_target(
            review, move_line_ids
        )
        if error:
            return {'error': error}

        pkg_vals = {}
        if package_type_id:
            pkg_vals['package_type_id'] = package_type_id
        if shipping_weight is not None:
            pkg_vals['shipping_weight'] = shipping_weight
        package = self.env['stock.quant.package'].sudo().create(pkg_vals)
        self._assign_lines_to_package(lines, package)
        return {'package': self._package_dict(package)}

    def add_to_package(self, package_id, move_line_ids):
        """Add reviewed lines to an existing package."""
        review = self.review
        from .validation_engine import ReviewValidationEngine
        lines, error = ReviewValidationEngine.validate_package_target(
            review, move_line_ids, package_id
        )
        if error:
            return {'error': error}

        pkg = self.env['stock.quant.package'].browse(package_id)
        self._assign_lines_to_package(lines, pkg)
        return {'package': self._package_dict(pkg)}

    def update_package(self, package_id, shipping_weight=None,
                       package_type_id=None):
        """Update weight and/or type of an existing package."""
        pkg = self.env['stock.quant.package'].browse(package_id)
        if not pkg.exists():
            return {'error': 'Paquete no encontrado.'}
        vals = {}
        if shipping_weight is not None:
            vals['shipping_weight'] = shipping_weight
        if package_type_id is not None:
            vals['package_type_id'] = package_type_id or False
        if vals:
            pkg.sudo().write(vals)
        return {'package': self._package_dict(pkg)}

    def unpackage_lines(self, move_line_ids):
        """Remove package_id from lines, returning them to unpackaged."""
        review = self.review
        from .validation_engine import ReviewValidationEngine
        if not move_line_ids:
            return {'error': 'Seleccione al menos una linea.'}
        try:
            ReviewValidationEngine.validate_unpackaging(review)
        except UserError as e:
            return {'error': str(e)}

        lines = self.env['review.move.line'].browse(move_line_ids)
        invalid = lines.filtered(
            lambda l: l.review_move_id.review_id.id != review.id
        )
        if invalid:
            return {'error': 'Algunas lineas no pertenecen a este repaso.'}

        lines.sudo().with_context(review_auto_create=True).write({
            'package_id': False,
            'state': 'done',
        })

        zombie = lines.filtered(
            lambda l: float_compare(l.qty, 0.0, precision_digits=4) <= 0
            and float_compare(l.qty_reviewed, 0.0, precision_digits=4) <= 0
        )
        if zombie:
            zombie.sudo().with_context(review_auto_create=True).unlink()

        self._merge_unpackaged_lines(lines)
        return {'success': True}

    def remove_line_from_package(self, move_line_id):
        """Remove a single line from its package."""
        review = self.review
        from .validation_engine import ReviewValidationEngine
        try:
            ReviewValidationEngine.validate_unpackaging(review)
        except UserError as e:
            return {'error': str(e)}

        ml = self.env['review.move.line'].browse(move_line_id)
        if not ml.exists() or ml.review_move_id.review_id.id != review.id:
            return {'error': 'La linea no pertenece a este repaso.'}

        ml.sudo().with_context(review_auto_create=True).write({
            'package_id': False,
            'state': 'done',
        })
        self._merge_unpackaged_lines(ml)
        return {'success': True}

    def _merge_unpackaged_lines(self, lines):
        """Merge unpackaged lines of the same lot/owner into one."""
        unpackaged = lines.filtered(lambda l: not l.package_id)
        if not unpackaged:
            return
        move_ids = list(set(unpackaged.mapped('review_move_id').ids))
        to_process = self.env['review.move.line'].search([
            ('review_move_id', 'in', move_ids),
            ('package_id', '=', False),
        ])
        if not to_process:
            return

        groups = {}
        for line in to_process:
            key = (
                line.review_move_id.id,
                line.lot_id.id or 0,
                line.owner_id.id or 0,
                line.is_excess,
                line.is_foreign,
            )
            groups.setdefault(key, self.env['review.move.line'])
            groups[key] |= line

        for group in groups.values():
            if len(group) <= 1:
                continue
            keep = group[0]
            merged = group[1:]
            total_qty = sum(group.mapped('qty'))
            total_reviewed = sum(group.mapped('qty_reviewed'))
            keep.with_context(review_auto_create=True).write({
                'qty': total_qty,
                'qty_reviewed': total_reviewed,
                'state': (
                    'done' if float_compare(
                        total_reviewed, 0.0, precision_digits=4
                    ) > 0 else 'pending'
                ),
            })
            merged.sudo().with_context(review_auto_create=True).unlink()

    # ==================================================================
    # DELETE REVIEW LINE
    # ==================================================================

    def delete_review_line(self, move_line_id):
        """Delete an excess or foreign line (not packaged)."""
        review = self.review
        if review.state != 'in_progress':
            return {'error': 'El repaso debe estar En Proceso para eliminar lineas.'}
        ml = self.env['review.move.line'].browse(move_line_id)
        if not ml.exists() or ml.review_move_id.review_id.id != review.id:
            return {'error': 'La linea no pertenece a este repaso.'}
        if ml.package_id:
            return {'error': 'Desempaquete la linea antes de eliminarla.'}
        if not (ml.is_excess or ml.is_foreign):
            return {'error': 'Solo pueden eliminarse lineas de exceso o fuera de la preparacion.'}
        ml.sudo().with_context(review_auto_create=True).unlink()
        return {'success': True}

    # ==================================================================
    # VALIDATION (pre-finish)
    # ==================================================================

    def validate_scan(self):
        """Integral validation of review state before finishing.

        Returns: {'ok': bool, 'issues': [str], 'warnings': [str],
                  'summary': dict}
        """
        review = self.review
        review.ensure_one()
        issues = []
        warnings = []
        cfg = get_cached_config(self.env, review.company_id.id)
        owner = review.owner_id
        now = fields.Datetime.now()

        if not review.current_scan_location_id:
            issues.append('No se escaneo la ubicacion de separacion.')

        dest = review.preparation_id.location_dest_id
        unpacked_lines = []
        qty_comparison = []

        for move in review.move_ids:
            total_reviewed = sum(
                ml.qty_reviewed for ml in move.move_line_ids
            )
            diff = total_reviewed - move.qty_preparation
            has_diff = float_compare(diff, 0.0, precision_digits=4) != 0
            if has_diff:
                if float_compare(diff, 0.0, precision_digits=4) > 0:
                    label = 'exceso'
                else:
                    label = 'faltante'
                diff_msg = (
                    'Producto %s: repasado %s, esperado %s (%s: %s).'
                    % (
                        move.product_id.display_name, total_reviewed,
                        move.qty_preparation, label, round(diff, 4),
                    )
                )
                if cfg.qty_differences_applies(owner):
                    issues.append(diff_msg)
                else:
                    warnings.append(diff_msg)

            for ml in move.move_line_ids:
                if (not cfg.can_finish_without_unpacked(owner)
                        and not ml.package_id
                        and float_compare(ml.qty_reviewed, 0.0, precision_digits=4) > 0):
                    unpacked_lines.append(ml)
                if (ml.is_foreign
                        and float_compare(ml.qty_reviewed, 0.0, precision_digits=4) > 0):
                    lot_name = ml.lot_id.name if ml.lot_id else ''
                    warnings.append(
                        'Producto fuera de la preparacion: %s%s.'
                        % (
                            move.product_id.display_name,
                            ' (lote %s)' % lot_name if lot_name else '',
                        )
                    )
                if (ml.lot_id and ml.lot_id.expiration_date
                        and ml.lot_id.expiration_date < now
                        and float_compare(ml.qty_reviewed, 0.0, precision_digits=4) > 0):
                    warnings.append(
                        'Lote vencido: %s (%s).'
                        % (ml.lot_id.name, move.product_id.display_name)
                    )

            qty_comparison.append({
                'product_name': move.product_id.display_name,
                'product_id': move.product_id.id,
                'qty_preparation': move.qty_preparation,
                'qty_reviewed': total_reviewed,
                'qty_packaged': move.qty_packaged,
                'qty_remaining': move.qty_remaining,
                'difference': diff,
                'has_diff': has_diff,
            })

        if unpacked_lines:
            names = ', '.join(set(
                ml.review_move_id.product_id.display_name
                for ml in unpacked_lines
            ))
            issues.append(
                'Hay productos repasados sin paquetizar: %s.' % names
            )

        if (cfg.stock_availability_applies(owner) and dest):
            avail_cache = {}
            for move in review.move_ids:
                for ml in move.move_line_ids:
                    if float_compare(ml.qty_reviewed, 0.0, precision_digits=4) <= 0:
                        continue
                    cache_key = (move.product_id.id, ml.lot_id.id or False)
                    if cache_key not in avail_cache:
                        avail_cache[cache_key] = self._quantity_available_at(
                            move.product_id, dest, lot=ml.lot_id
                        )
                    available = avail_cache[cache_key]
                    if float_compare(ml.qty_reviewed, available, precision_digits=4) > 0:
                        issues.append(
                            'Producto %s%s: repasado %s, disponible en '
                            'stock %s.'
                            % (
                                move.product_id.display_name,
                                ' (lote %s)' % ml.lot_id.name
                                if ml.lot_id else '',
                                ml.qty_reviewed,
                                available,
                            )
                        )

        packages_summary = []
        all_lines = review.move_ids.move_line_ids
        lines_by_package = {}
        for l in all_lines:
            if l.package_id:
                lines_by_package.setdefault(l.package_id.id, []).append(l)
        for pkg in review.package_ids:
            pkg_lines = lines_by_package.get(pkg.id, [])
            packages_summary.append({
                'id': pkg.id,
                'name': pkg.name or '',
                'shipping_weight': pkg.shipping_weight,
                'package_type_name': (
                    pkg.package_type_id.name if pkg.package_type_id else ''
                ),
                'line_count': len(pkg_lines),
                'total_qty': sum(ml.qty for ml in pkg_lines),
                'products': list(set(
                    ml.review_move_id.product_id.display_name
                    for ml in pkg_lines
                )),
            })

        return {
            'ok': len(issues) == 0,
            'issues': issues,
            'warnings': warnings,
            'summary': {
                'qty_comparison': qty_comparison,
                'packages_summary': packages_summary,
                'total_packages': len(review.package_ids),
                'total_moves': len(review.move_ids),
            },
        }

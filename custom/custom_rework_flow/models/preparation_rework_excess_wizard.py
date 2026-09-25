# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_compare


class PreparationReworkExcessLine(models.TransientModel):
    _name = 'preparation.rework.excess.line'
    _description = 'Linea de Excedente del Re-Proceso'

    wizard_id = fields.Many2one(
        'preparation.rework.excess.wizard',
        string='Wizard',
        required=True,
        ondelete='cascade',
    )
    preparation_line_id = fields.Many2one(
        'preparation.stock.line',
        string='Linea de Preparacion',
        ondelete='cascade',
    )
    product_id = fields.Many2one(
        'product.product',
        string='Producto',
        readonly=True,
    )
    qty_scanned = fields.Float(
        string='Escaneado',
        readonly=True,
    )
    qty_demanded = fields.Float(
        string='Demandado',
        readonly=True,
    )
    qty_to_remove = fields.Float(
        string='Cantidad a Retirar',
        digits='Product Unit of Measure',
    )
    location_dest_id = fields.Many2one(
        'stock.location',
        string='Ubicacion Destino',
        domain=[('usage', '=', 'internal')],
        help="Ubicacion de destino para el stock excedente. Generalmente "
             "una ubicacion de origen o rechazo.",
    )
    package_dest_id = fields.Many2one(
        'stock.quant.package',
        string='Paquete Destino',
        domain="[('location_id', '=', location_dest_id), "
               "('quant_ids', 'any', [('quantity', '>', 0)])]",
        help="Paquete existente en la ubicacion destino al que se agregara "
             "el stock excedente. Si se indica, no se crea un paquete nuevo. "
             "Al elegirlo, se autocompleta la ubicacion destino.",
    )
    create_package = fields.Boolean(
        string='Armar paquete',
        default=False,
        help="Si se marca y no se eligio un paquete destino, se creara un "
             "paquete por producto con todo el stock retirado. Por defecto "
             "se devuelve al paquete existente del que salio el stock.",
    )

    @api.onchange('location_dest_id')
    def _onchange_location_dest_id(self):
        """Al cambiar la ubicacion destino, descarta el paquete si ya no
        pertenece a esa ubicacion."""
        if self.package_dest_id and self.package_dest_id.location_id \
                and self.package_dest_id.location_id.id != (self.location_dest_id.id or False):
            self.package_dest_id = False

    @api.onchange('package_dest_id')
    def _onchange_package_dest_id(self):
        """Si se elige un paquete, autocompleta la ubicacion destino con la
        ubicacion actual del paquete (relacion bidireccional)."""
        if self.package_dest_id and self.package_dest_id.location_id:
            self.location_dest_id = self.package_dest_id.location_id


class PreparationReworkExcessWizard(models.TransientModel):
    """Wizard para gestionar el excedente en la ubicacion de separacion
    cuando se reduce la demanda durante un re-proceso.

    Mueve fisicamente el stock de la ubicacion de separacion hacia la
    ubicacion elegida por producto, con o sin paquete, y elimina los
    escaneos correspondientes para que scanned == demanded.
    """
    _name = 'preparation.rework.excess.wizard'
    _description = 'Gestion de Excedentes del Re-Proceso'

    preparation_id = fields.Many2one(
        'preparation.stock',
        string='Preparacion',
        required=True,
        readonly=True,
    )
    line_ids = fields.One2many(
        'preparation.rework.excess.line',
        'wizard_id',
        string='Excedentes a retirar',
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        prep_id = res.get('preparation_id') or self.env.context.get('default_preparation_id')
        if not prep_id:
            return res
        prep = self.env['preparation.stock'].browse(prep_id)
        excess_lines = self._excess_line_vals(prep)
        if excess_lines:
            res['line_ids'] = excess_lines
        return res

    @api.model
    def _excess_line_vals(self, prep):
        """Comandos (0, 0, {...}) de las lineas de excedente de la preparacion,
        en el orden de prep.line_ids. La usa default_get y la pre-creacion del
        wizard desde el boton 'Gestionar Excedentes' (para abrir con res_id y
        que el cliente conserve la identidad de las lineas en el flush)."""
        excess_lines = []
        for line in prep.line_ids:
            scanned_effective = line.qty_scanned
            excess = scanned_effective - line.qty_demanded
            if float_compare(excess, 0.0, precision_digits=4) <= 0:
                continue
            excess_lines.append((0, 0, {
                'preparation_line_id': line.id,
                'product_id': line.product_id.id,
                'qty_scanned': scanned_effective,
                'qty_demanded': line.qty_demanded,
                'qty_to_remove': excess,
            }))
        return excess_lines

    @api.model
    def _get_original_package(self, line):
        """Recupera el paquete existente al que se devolvera el stock de una
        linea.

        Prioridad: el paquete fisico actual del stock (quan vigente del
        producto + lote + owner), que en re-proceso puede diferir del paquete
        grabado en el escaneo (iteracion anterior, con la ubicacion y paquete
        de origen); si no se identifica, el paquete del escaneo y luego el del
        stock move de la primera validacion (stock_move_line_id). Elige el
        paquete dominante por cantidad escaneada (puede haber varios en la
        linea). Retorna la instancia, o False si no hay ninguno con stock
        disponible.
        """
        counts = {}
        for scan in line.scan_line_ids:
            package = self._find_current_package(scan)
            if not package and scan.package_id:
                package = scan.package_id
            if not package and scan.stock_move_line_id:
                package = scan.stock_move_line_id.package_id
            if not package:
                continue
            counts[package.id] = counts.get(package.id, 0.0) + (scan.qty or 0.0)
        for package_id in list(counts.keys()):
            package = self.env['stock.quant.package'].browse(package_id)
            has_stock = any(
                float_compare(q.quantity, 0.0, precision_digits=4) > 0
                for q in package.quant_ids
                if q.location_id.usage == 'internal'
            )
            if not has_stock:
                counts.pop(package_id)
        if not counts:
            return False
        best_id = max(counts, key=counts.get)
        return self.env['stock.quant.package'].browse(best_id)

    @api.model
    def _find_current_package(self, scan):
        """Paquete fisico actual del stock del escaneo, o False si esta suelto
        o no existe quan vigente."""
        prep = scan.preparation_id
        domain = [
            ('product_id', '=', scan.product_id.id),
            ('quantity', '>', 0),
        ]
        if scan.lot_id:
            domain.append(('lot_id', '=', scan.lot_id.id))
        else:
            domain.append(('lot_id', '=', False))
        if prep.owner_id:
            domain.append(('owner_id', '=', prep.owner_id.id))
        quant = self.env['stock.quant'].sudo().search(
            domain, order='in_date, id', limit=1)
        if not quant:
            return False
        return quant.package_id or False

    def confirm(self):
        self.ensure_one()
        prep = self.preparation_id
        if not prep:
            raise UserError(_('No se indico la preparacion.'))
        dest_loc = prep.location_dest_id
        if not dest_loc:
            raise UserError(_('La preparacion no tiene ubicacion de separacion definida.'))

        # 0. Construir los items a procesar. Si las lineas transient no
        #    llegaron a persistir (line_ids vacio), se recalculan directo
        #    desde la preparacion con la misma formula de default_get.
        items = self._collect_items(prep)
        if not items:
            raise UserError(_(
                'No se encontraron lineas de excedente para procesar.\n'
                'Verifique que la preparacion tenga cantidad escaneada por '
                'encima de la demandada.'
            ))

        # 1. Prevalidacion: se resuelven los origenes de TODAS las lineas
        #    antes de mover stock. Si alguna no tiene stock fisico, se
        #    informa con un UserError unico y detallado y NO se mueve nada.
        plans = []
        errors = []
        for line, product, qty, wline in items:
            if float_compare(qty, 0.0, precision_digits=4) <= 0:
                continue
            try:
                plan = self._plan_product_removal(prep, product, line, qty)
            except UserError as exc:
                errors.append('%s: %s' % (product.display_name, exc))
                continue
            plans.append((line, product, qty, wline, plan))
        if errors:
            raise UserError(_(
                'No se pudo retirar el excedente de las siguientes lineas:\n%s'
            ) % ('\n'.join('- %s' % e for e in errors)))
        if not plans:
            raise UserError(_(
                'No se encontraron lineas de excedente para procesar.'
            ))

        # 2. Ejecutar los movimientos ya planificados.
        processed = []
        for line, product, qty, wline, plan in plans:
            self._execute_product_removal(prep, product, line, qty, wline, plan)
            processed.append((product, qty))

        # Log global de excedentes procesados.
        details = []
        for product, qty in processed:
            details.append('%s: -%s' % (product.display_name, qty))
        prep._log_rework_event(
            'excess_moved',
            detail='Excedente retirado de separacion:\n' + '\n'.join(details),
        )

        # 3. Si aun queda excedente, informarlo explicitamente (nunca en
        #    silencio) listando lo que quedo pendiente.
        if prep.has_excess:
            remaining = []
            for l in prep.line_ids:
                exc = l.qty_scanned - l.qty_demanded
                if float_compare(exc, 0.0, precision_digits=4) > 0:
                    remaining.append('%s: %s' % (l.product_id.display_name, exc))
            raise UserError(_(
                'El excedente no pudo retirarse por completo.\n'
                'Queda pendiente:\n%s'
            ) % ('\n'.join('- %s' % r for r in remaining)))

        # 4. Auto-avance: ya no hay excedentes -> marcar a Para Validar.
        return prep.action_mark_to_validate()

    def _collect_items(self, prep):
        """Retorna los items a procesar: [(linea_prep, producto, qty, wline)].

        Prioriza las lineas persistidas del wizard. Como defensa ante un flush
        del cliente que no conserve la identidad (product_id/preparation_line_id
        vacios), las lineas se re-asocian en orden con las de la preparacion
        que tienen excedente (misma secuencia de default_get). Si aun asi no
        hay nada, recalcula el excedente directo desde la preparacion.
        """
        excess_prep_lines = prep.line_ids.filtered(
            lambda l: float_compare(
                l.qty_scanned - l.qty_demanded, 0.0, precision_digits=4) > 0)

        items = []
        for index, wline in enumerate(self.line_ids):
            line = wline.preparation_line_id
            identified = bool(wline.product_id and line)
            if not identified and index < len(excess_prep_lines):
                line = excess_prep_lines[index]
            if not line:
                continue
            qty = wline.qty_to_remove
            if not identified or float_compare(qty, 0.0, precision_digits=4) <= 0:
                qty = line.qty_scanned - line.qty_demanded
            if float_compare(qty, 0.0, precision_digits=4) <= 0:
                continue
            if not identified:
                wline.write({
                    'preparation_line_id': line.id,
                    'product_id': line.product_id.id,
                    'qty_scanned': line.qty_scanned,
                    'qty_demanded': line.qty_demanded,
                    'qty_to_remove': qty,
                })
            items.append((line, line.product_id, qty, wline))

        if items:
            return items

        excess_lines = prep.line_ids.filtered(
            lambda l: float_compare(
                l.qty_scanned - l.qty_demanded, 0.0, precision_digits=4) > 0)
        for line in excess_lines:
            items.append((line, line.product_id, line.qty_scanned - line.qty_demanded, False))
        return items

    def _plan_product_removal(self, prep, product, line, qty_to_remove):
        """Resuelve, sin efectos, el origen fisico real de cada escaneo a
        retirar. Retorna el plan (linea, escaneos y orígenes) o lanza
        UserError si no hay stock fisico."""
        if not line:
            raise UserError(_(
                'No se encontro una linea de preparacion para %s.'
            ) % (product.display_name or 'un producto sin identificar'))
        separation_loc = prep.location_dest_id

        # Distribucion por lote de los escaneos de esta linea. Se priorizan
        # los renglones ya trasladados a la separacion (stock_move_id): el
        # excedente a retirar esta fisicamente alli, no en renglones nuevos
        # escaneados desde la ubicacion de origen durante el re-proceso.
        # Dentro de cada grupo, mas nuevos primero.
        scans = line.scan_line_ids.sorted(
            key=lambda s: (bool(s.stock_move_id) is False, -s.id))
        lot_fifo = []
        remaining = qty_to_remove
        for scan in scans:
            if float_compare(remaining, 0.0, precision_digits=4) <= 0:
                break
            take = min(scan.qty, remaining)
            remaining -= take
            lot_fifo.append((scan, take))

        # Resolver el origen real de cada escaneo. El paquete de origen sale
        # del quan fisico vigente, no del paquete grabado en el escaneo (que
        # en re-proceso corresponde a la iteracion anterior). Un escaneo puede
        # repartirse entre varios paquetes de una misma ubicacion.
        sources = {}
        for scan, take in lot_fifo:
            expected = separation_loc if scan.stock_move_id else scan.location_id
            portions = self._resolve_scan_source(
                prep, product, scan, expected, take)
            for location, pkg, portion in portions:
                sources.setdefault(location.id, []).append(
                    (scan, portion, location, pkg))
        if not sources:
            raise UserError(_(
                'No se encontro stock fisico de %s para retirar el excedente.'
            ) % (product.display_name or '-'))

        return {
            'line': line,
            'lot_fifo': lot_fifo,
            'sources': sources,
        }

    def _execute_product_removal(self, prep, product, line, qty_to_remove, wline, plan):
        lot_fifo = plan['lot_fifo']
        sources = plan['sources']

        # Resolver la ubicacion destino: se exige la elegida en la linea del
        # wizard. Sin ella no se puede mover el excedente (evita terminar con
        # origen == destino).
        if not wline:
            raise UserError(_(
                'No se encontro una linea del wizard para %s. Reabra el '
                'wizard de excedentes y seleccione la ubicacion de destino.'
            ) % (product.display_name or '-'))
        dest_id = wline.location_dest_id.id
        if not dest_id:
            raise UserError(_(
                'Seleccione la ubicacion de destino para el excedente de %s '
                'antes de procesar.'
            ) % (product.display_name or '-'))

        # Resolver el paquete destino: si se eligio uno existente se usa ese
        # como result_package_id; si no, se crea uno nuevo cuando create_package
        # este activo. Sin linea persistida del wizard no se crea paquete.
        package = False
        if wline and wline.package_dest_id:
            package = wline.package_dest_id
        elif wline and wline.create_package:
            package = self.env['stock.quant.package'].sudo().create({
                'package_type_id': False,
            })

        # Crear y ejecutar los movimientos internos Origen -> Destino.
        StockMove = self.env['stock.move'].sudo()
        has_picked = 'picked' in StockMove._fields
        for source_id, items in sources.items():
            # Agrupar por paquete de origen fisico (o stock suelto): un move
            # line por paquete para no duplicar el mismo paquete en el
            # traslado. El paquete ya viene resuelto del quan vigente.
            subgroups = {}
            for scan, portion, _loc, pkg in items:
                key = pkg.id if pkg else 0
                subgroups.setdefault(key, []).append((scan, portion))

            for key, subgroup in subgroups.items():
                src_pkg = self.env['stock.quant.package'].browse(key) if key else False
                move_line_vals = []
                take_total = 0.0
                for scan, portion in subgroup:
                    take_total += portion
                    ml_vals = {
                        'product_id': product.id,
                        'product_uom_id': product.uom_id.id,
                        'location_id': source_id,
                        'location_dest_id': dest_id,
                        'quantity': portion,
                        'company_id': prep.company_id.id,
                        'reference': prep.name,
                    }
                    if scan.lot_id:
                        ml_vals['lot_id'] = scan.lot_id.id
                    if prep.owner_id:
                        ml_vals['owner_id'] = prep.owner_id.id
                    if src_pkg:
                        ml_vals['package_id'] = src_pkg.id
                    if package:
                        ml_vals['result_package_id'] = package.id
                    move_line_vals.append((0, 0, ml_vals))

                move_vals = {
                    'name': '%s - retirar excedente %s%s' % (
                        prep.name, product.display_name,
                        ' (%s)' % src_pkg.name if src_pkg else ''),
                    'reference': prep.name,
                    'origin': 'Re-proceso: retiro de excedente',
                    'product_id': product.id,
                    'product_uom_qty': take_total,
                    'product_uom': product.uom_id.id,
                    'location_id': source_id,
                    'location_dest_id': dest_id,
                    'company_id': prep.company_id.id,
                    'move_line_ids': move_line_vals,
                }
                if has_picked:
                    move_vals['picked'] = True
                move = StockMove.create(move_vals)
                move._action_confirm()
                move._action_assign()
                move._action_done()

                # Verificacion post-movimiento: si algo no se completo, informarlo.
                if move.state != 'done':
                    raise UserError(_(
                        'El retiro de excedente de %s no pudo completarse '
                        '(estado del movimiento: %s).'
                    ) % (product.display_name, move.state))
                moved = sum(move.move_line_ids.sudo().mapped('quantity'))
                if float_compare(moved, take_total, precision_digits=4) < 0:
                    raise UserError(_(
                        'El retiro de excedente de %s no movio la cantidad '
                        'esperada (%s de %s). Verifique el stock en la ubicacion '
                        'origen.'
                    ) % (product.display_name, moved, take_total))

        # Eliminar / reducir los escaneos para que scanned == new demanda.
        # Los renglones con stock_move_id se actualizan con el contexto
        # excess_wizard para evadir la inmutabilidad de re-proceso (este flujo
        # mueve el stock fisico ANTES de tocar los escaneos).
        remaining_to_delete = qty_to_remove
        for scan, take in lot_fifo:
            if float_compare(remaining_to_delete, 0.0, precision_digits=4) <= 0:
                break
            new_qty = scan.qty - take
            if float_compare(new_qty, 0.0, precision_digits=4) <= 0:
                scan.with_context(excess_wizard=True).unlink()
            else:
                scan.with_context(excess_wizard=True).write({'qty': new_qty})
            remaining_to_delete -= take

        # Si la linea quedo sin demanda y sin escaneos, eliminarla.
        if float_compare(line.qty_demanded, 0.0, precision_digits=4) <= 0 \
                and float_compare(line.qty_scanned, 0.0, precision_digits=4) <= 0:
            line.unlink()

    @api.model
    def _resolve_scan_source(self, prep, product, scan, expected_loc, take):
        """Ubica el origen fisico real del stock de un escaneo.

        Busca los quants disponibles (producto + lote + owner) y desplaza el
        movimiento desde la ubicacion donde el stock realmente esta, en lugar
        de asumir siempre la Separacion. El paquete de origen tambien se
        resuelve del quan fisico vigente: el grabado en el escaneo suele
        pertenecer a la iteracion anterior (ubicacion y paquete de origen).

        Retorna una lista de (ubicacion, paquete_o_False, cantidad) que suman
        el take solicitado. Un escaneo puede repartirse entre varios paquetes
        de una misma ubicacion. Lanza UserError si no hay stock disponible.
        """
        Quant = self.env['stock.quant'].sudo()
        domain = [
            ('product_id', '=', product.id),
            ('quantity', '>', 0),
        ]
        if scan.lot_id:
            domain.append(('lot_id', '=', scan.lot_id.id))
        else:
            domain.append(('lot_id', '=', False))
        if prep.owner_id:
            domain.append(('owner_id', '=', prep.owner_id.id))

        quants = Quant.search(domain, order='in_date, id', limit=50)
        if not quants:
            raise UserError(_(
                'No hay existencias fisicas de %s%s en ninguna ubicacion '
                'para retirar el excedente. Verifique el inventario.'
            ) % (
                product.display_name,
                (' (Lote %s)' % scan.lot_id.name) if scan.lot_id else '',
            ))

        def _consume(location):
            """Extrae (ubicacion, paquete, cantidad) por quan fisico de la
            ubicacion, respetando la disponibilidad a nivel paquete y el stock
            propio del quan, hasta cubrir el take. Retorna [] si no lo cubre."""
            remaining = take
            parts = []
            for q in quants.filtered(lambda x: x.location_id.id == location.id):
                available = Quant._get_available_quantity(
                    product,
                    location,
                    lot_id=scan.lot_id,
                    owner_id=prep.owner_id,
                    package_id=q.package_id,
                    strict=True,
                )
                available = min(available, q.quantity or 0.0)
                if float_compare(available, 0.0, precision_digits=4) <= 0:
                    continue
                portion = min(available, remaining)
                parts.append((q.location_id, q.package_id or False, portion))
                remaining -= portion
                if float_compare(remaining, 0.0, precision_digits=4) <= 0:
                    return parts
            return []

        # Preferir la ubicacion esperada si cubre el take solicitado.
        if expected_loc:
            parts = _consume(expected_loc)
            if parts:
                return parts
        # Buscar una ubicacion con stock suficiente para cubrir el take.
        seen = set()
        for q in quants:
            if q.location_id.id in seen:
                continue
            seen.add(q.location_id.id)
            parts = _consume(q.location_id)
            if parts:
                return parts
        # Sin stock suficiente en ninguna ubicacion: informarlo explicitamente.
        raise UserError(_(
            'Stock fisico insuficiente de %s%s para retirar la cantidad '
            'solicitada (%s). Verifique la ubicacion de origen.'
        ) % (
            product.display_name,
            (' (Lote %s)' % scan.lot_id.name) if scan.lot_id else '',
            take,
        ))
# -*- coding: utf-8 -*-
from odoo import _
from odoo.exceptions import UserError
from odoo.tools import float_compare


class ReworkService:
    """Servicio centralizado de re-proceso: devuelve una preparacion
    finalizada a En Proceso tras autorizacion.

    Origenes soportados:
      - review  -> boton 'Volver a Preparar' en el repaso.
      - picking -> boton 'Devolver a Preparacion' en el picking de salida.
    """

    def __init__(self, env):
        self.env = env

    # ======================================================================
    # FLUJOS PRINCIPALES
    # ======================================================================

    def rework_from_review(self, review, auth_user, reason='', authorized_by_id=False):
        """Re-proceso desde el repaso.

        Pasos:
          1. Cancela picking de expedicion ligado (si existe, en draft/assigned).
          2. Devuelve a la separacion el stock del repaso que este en la
             ubicacion de salida (solo si el repaso estaba FINALIZADO).
          3. Marca el repaso como MODIFICADO.
          4. Reabre la preparacion a En Proceso (is_rework=True) y recrea reservas.
          5. Registra en el historial de modificaciones.
        """
        review.ensure_one()
        prep = review.preparation_id
        if prep.state != 'done':
            raise UserError(_(
                'La preparacion %s no esta finalizada; no se puede '
                'volver a preparar desde el repaso.'
            ) % prep.name)

        dest_loc = prep.location_dest_id
        if not dest_loc:
            raise UserError(_('La preparacion de origen no tiene ubicacion de separacion.'))

        # 1. Cancelar pickings de expedicion draft.
        self._cancel_expedition_pickings(review, auth_user, reason)

        # 2. Devolver el stock a la separacion si el repaso estaba finalizado.
        if review.state == 'done':
            self._reverse_review_moves(review, dest_loc)

        # 3. Marcar como Modificado.
        review._mark_modified(auth_user=auth_user, reason=reason)
        log = getattr(prep, '_log_rework_event', None)
        if log:
            log(
                'review_modified',
                review_id=review.id,
                detail='Repaso %s marcado como Modificado (v%s).'
                       % (review.name, review.version or '?'),
                reason=reason,
            )

        # 4. Reabrir preparacion.
        self._reopen_preparation(prep, auth_user, reason, authorized_by_id=authorized_by_id)

        prep.message_post(
            body='Re-proceso solicitado por %s (autorizacion de %s). '
                 'Motivo: %s'
                 % (self.env.user.name, auth_user.name or '-',
                    reason or '-'),
            subtype_xmlid='mail.mt_note',
        )
        return prep

    def rework_from_picking(self, picking, auth_user, reason='', authorized_by_id=False):
        """Re-proceso desde el picking de salida (no validado).

        Cancela el picking, devuelve a la separacion el stock de los
        repasos vinculados que esten FINALIZADOS (en una sola tanda),
        los marca como MODIFICADO y reabre la preparacion.
        """
        picking.ensure_one()

        if picking.state in ('done', 'cancel'):
            raise UserError(_(
                'El picking %s no admite devolucion a preparacion '
                '(estado: %s). Genere primero un picking de retorno si '
                'el stock ya salio con el cliente.'
            ) % (picking.name, picking.state))

        reviews = self.env['review.stock.picking'].search([
            ('expedition_picking_ids', 'in', picking.ids),
        ])
        if not reviews:
            raise UserError(_(
                'No se encontraron repasos vinculados al picking de salida %s.'
            ) % picking.name)

        preps = reviews.mapped('preparation_id')
        if len(preps) > 1:
            raise UserError(_(
                'El picking %s agrupa repasos de varias preparaciones '
                '(%s). Realice la devolucion repaso por repaso.'
            ) % (picking.name, ', '.join(preps.mapped('name'))))

        prep = preps[0]
        if prep.state != 'done':
            raise UserError(_(
                'La preparacion %s no esta finalizada; no se puede '
                'volver a preparar.'
            ) % prep.name)

        dest_loc = prep.location_dest_id
        if not dest_loc:
            raise UserError(_('La preparacion no tiene ubicacion de separacion.'))

        # 1. Cancelar el picking (no esta validado, seguro hacerlo).
        try:
            picking.sudo().action_cancel()
        except Exception:
            raise UserError(_(
                'No se pudo cancelar el picking de salida %s.'
            ) % picking.name)
        log = getattr(prep, '_log_rework_event', None)
        if log:
            log(
                'picking_cancelled',
                detail='Picking de salida cancelado: %s.' % picking.name,
                reason=reason,
            )

        # 2. Devolver a la separacion el stock de todos los repasos
        #    finalizados en una unica tanda de movimientos.
        done_reviews = reviews.filtered(lambda r: r.state == 'done')
        if done_reviews:
            self._reverse_review_moves(done_reviews, dest_loc)

        # 3. Marcar cada repaso como MODIFICADO.
        for review in reviews:
            review._mark_modified(auth_user=auth_user, reason=reason)
            if log:
                log(
                    'review_modified',
                    review_id=review.id,
                    detail='Repaso %s marcado como Modificado (v%s).'
                           % (review.name, review.version or '?'),
                    reason=reason,
                )

        # 4. Reabrir preparacion.
        self._reopen_preparation(prep, auth_user, reason, authorized_by_id=authorized_by_id)

        prep.message_post(
            body='Re-proceso desde picking %s, solicitado por %s '
                 '(autorizacion de %s). Motivo: %s'
                 % (picking.name, self.env.user.name,
                    auth_user.name or '-', reason or '-'),
            subtype_xmlid='mail.mt_note',
        )
        return prep

    # ======================================================================
    # PASOS INTERNOS
    # ======================================================================

    def _cancel_expedition_pickings(self, review, auth_user, reason=''):
        """Cancela los pickings de salida en borrador asignados al repaso."""
        pickings = review.expedition_picking_ids.filtered(
            lambda p: p.state in ('draft', 'waiting', 'confirmed', 'assigned'))
        if not pickings:
            return self.env['stock.picking']
        for picking in pickings:
            try:
                picking.sudo().action_cancel()
            except Exception:
                continue
        prep = review.preparation_id
        log = getattr(prep, '_log_rework_event', None)
        if log:
            log(
                'picking_cancelled',
                review_id=review.id,
                detail='Pickings de salida cancelados: %s.'
                       % ', '.join(pickings.mapped('name')),
                reason=reason,
            )
        return pickings

    def _reverse_review_moves(self, reviews, dest_loc):
        """Devuelve a la separacion el stock REAL de los repasos.

        En lugar de reconstruir la reversa a partir del historial de
        movimientos, se consulta el stock vigente (quants) de los repasos
        en la ubicacion de salida:
          - si el stock sigue en la separacion (expedicion no generada o
            reversion ya aplicada) no hay quants en la salida y no se
            mueve nada;
          - si esta en la ubicacion de salida, se devuelve junto con su
            lote, propietario y paquete.

        Los stock.move se crean y se ejecutan todos juntos: una unica
        tanda de confirm/assign/done en lugar de una por producto.

        La operacion usa sudo() sobre stock: el re-proceso ya esta
        autorizado en el wizard, el usuario de la sesion no tiene por
        que tener permisos de inventario.
        """
        if not reviews:
            return

        exit_locations = self._get_exit_locations(reviews, dest_loc)
        moves_vals = self._plan_reversal_moves(reviews, exit_locations, dest_loc)
        if not moves_vals:
            return

        StockMove = self.env['stock.move'].sudo()
        moves = StockMove.create(moves_vals)
        moves._action_confirm(merge=False)
        moves._action_assign()
        moves._action_done()

        pending = moves.exists().filtered(lambda m: m.state != 'done')
        if pending:
            raise UserError(_(
                'No se pudo devolver a la separacion el stock de %s. '
                'Movimientos sin validar: %s.'
            ) % (
                ', '.join(reviews.mapped('name')),
                ', '.join(pending.mapped('name')),
            ))

    def _plan_reversal_moves(self, reviews, exit_locations, dest_loc):
        """Arma los vals de los stock.move que devuelven a la separacion
        el stock de los repasos que hoy esta en la ubicacion de salida.

        El stock de cada repaso se identifica contra los quants vigentes:
        por (producto, lote, paquete) cuando la linea esta paquetizada y
        por (producto, lote, propietario) cuando esta suelta. Asi solo se
        mueve stock que pertenece a esta preparacion y se respeta el estado
        fisico real, sea cual sea la ubicacion en la que este.
        Retorna [] cuando no hay nada que mover.
        """
        if not exit_locations:
            return []

        expected = {}
        product_ids = []
        seen_products = set()
        for review in reviews:
            for line in review.move_ids.move_line_ids:
                if float_compare(line.qty, 0.0, precision_digits=4) <= 0:
                    continue
                if line.product_id.id not in seen_products:
                    seen_products.add(line.product_id.id)
                    product_ids.append(line.product_id.id)
                key = self._stock_key(
                    line.product_id.id, line.lot_id.id,
                    line.package_id.id, line.owner_id.id)
                info = expected.get(key)
                if info:
                    info['qty'] += line.qty
                else:
                    expected[key] = {'review': review, 'qty': line.qty}
        if not expected:
            return []

        quants = self.env['stock.quant'].sudo().search([
            ('location_id', 'in', exit_locations.ids),
            ('product_id', 'in', product_ids),
            ('quantity', '>', 0),
        ], order='id')

        # Un stock.move por repaso y producto, con todas sus lineas.
        groups = {}
        for quant in quants:
            key = self._stock_key(
                quant.product_id.id, quant.lot_id.id,
                quant.package_id.id, quant.owner_id.id)
            info = expected.get(key)
            if not info:
                continue
            # El paquete vuelve entero (es exclusivo del repaso); el stock
            # suelto solo reclama lo que el repaso tiene, para no tocar
            # stock ajeno que pudiera compartir lote y propietario.
            if quant.package_id:
                take = quant.quantity
            else:
                take = min(quant.quantity, info['qty'])
            if float_compare(take, 0.0, precision_digits=4) <= 0:
                continue
            info['qty'] -= take

            group_key = (info['review'].id, quant.product_id.id,
                         quant.location_id.id)
            group = groups.get(group_key)
            if group is None:
                group = groups[group_key] = {
                    'review': info['review'],
                    'product': quant.product_id,
                    'location': quant.location_id,
                    'qty': 0.0,
                    'lines': [],
                }
            group['qty'] += take
            group['lines'].append(
                self._reversal_line_vals(quant, take, dest_loc, info['review']))

        if not groups:
            return []

        has_picked = 'picked' in self.env['stock.move']._fields
        moves_vals = []
        for group in groups.values():
            review = group['review']
            product = group['product']
            move_vals = {
                'name': '%s - revertir %s' % (review.name, product.display_name),
                'reference': review.name,
                'origin': 'Re-proceso: reversion de stock',
                'product_id': product.id,
                'product_uom_qty': group['qty'],
                'product_uom': product.uom_id.id,
                'location_id': group['location'].id,
                'location_dest_id': dest_loc.id,
                'company_id': review.company_id.id,
                'move_line_ids': group['lines'],
            }
            if has_picked:
                move_vals['picked'] = True
            moves_vals.append(move_vals)
        return moves_vals

    def _reversal_line_vals(self, quant, take, dest_loc, review):
        """vals de la stock.move.line de reversa.

        Sale del quan vigente: lote, propietario, ubicacion y paquete
        reales, conservando el paquete en destino para que los quants
        mantengan el empacado.
        """
        vals = {
            'product_id': quant.product_id.id,
            'product_uom_id': quant.product_id.uom_id.id,
            'location_id': quant.location_id.id,
            'location_dest_id': dest_loc.id,
            'company_id': review.company_id.id,
            'reference': review.name,
        }
        line_fields = self.env['stock.move.line']._fields
        for fname in ('quantity', 'qty_done'):
            if fname in line_fields:
                vals[fname] = take
        if quant.lot_id:
            vals['lot_id'] = quant.lot_id.id
        if quant.owner_id:
            vals['owner_id'] = quant.owner_id.id
        if quant.package_id:
            vals['package_id'] = quant.package_id.id
            vals['result_package_id'] = quant.package_id.id
        return (0, 0, vals)

    def _stock_key(self, product_id, lot_id, package_id, owner_id):
        """Clave de identificacion del stock de un repaso.

        Con paquete alcanza (producto, lote, paquete): el paquete es
        exclusivo del repaso. Sin paquete se agrega el propietario para
        no confundirlo con stock de otra preparacion en la misma
        ubicacion.
        """
        if package_id:
            return ('pkg', product_id, lot_id, package_id)
        return ('loose', product_id, lot_id, owner_id)

    def _reopen_preparation(self, prep, auth_user, reason='', authorized_by_id=False):
        """Pasa una preparacion de Hecho -> En Proceso (re-proceso).

        Tambien re-sincroniza las reservas logicas que habian sido
        liberadas al generar la expedicion.
        """
        prep.ensure_one()
        if prep.state != 'done':
            raise UserError(_(
                'La preparacion %s no esta en estado Hecho.'
            ) % prep.name)

        in_progress = self.env['preparation.stock.stage'].search(
            [('state', '=', 'in_progress')], limit=1)
        if not in_progress:
            raise UserError(_('No existe una etapa "En Proceso" configurada.'))

        log_ctx = dict(self.env.context)
        if authorized_by_id:
            log_ctx['authorized_by_id'] = authorized_by_id
        elif auth_user:
            log_ctx['authorized_by_id'] = auth_user.id

        prep.with_context(log_ctx).write({
            'stage_id': in_progress.id,
            'date_end': False,
            'is_rework': True,
            'version': (prep.version or 1) + 1,
        })

        self._resync_reservations(prep)

        log = getattr(prep, '_log_rework_event', None)
        if log:
            log(
                'reopen',
                detail='Preparacion reabierta para volver a preparar '
                       'por autorizacion de %s.'
                       % (auth_user.name if auth_user else '-'),
                reason=reason,
            )

    def _resync_reservations(self, prep):
        """Recrea las reservas logicas para el stock que se encuentra
        en la ubicacion de separacion despues de reabrir la preparacion.

        Antes de la reapertura, las reservas estaban liberadas con motivo
        'expedition'. Las recrea en estado 'reserved' para que el control
        de disponibilidad vuelva a bloquear stock para otros operarios.
        """
        cfg = self.env['preparation.stock.config']._get_config(prep.company_id)
        if not cfg.reservation_applies(prep.owner_id):
            return

        Reservation = self.env['stock.quant.reservation'].sudo()
        seen = set()
        for scan in prep.scan_line_ids:
            key = (
                scan.product_id.id,
                scan.lot_id.id if scan.lot_id else False,
                prep.owner_id.id if prep.owner_id else False,
            )
            if key in seen:
                continue
            seen.add(key)
            Reservation._sync_reservation(
                prep, scan.product_id.id,
                scan.lot_id.id if scan.lot_id else False,
                prep.owner_id.id if prep.owner_id else False,
            )

    def _get_exit_locations(self, reviews, dest_loc):
        """Ubicaciones en las que puede estar el stock de salida de los
        repasos.

          1. La 'Ubicacion de Salida' de la Configuracion de Repasos.
          2. El destino de los movimientos finalizados de los repasos
             (reference = nombre del repaso): la ubicacion real a la que
             se traslado el stock al generar el picking de salida. La
             separacion se descarta al final, porque los movimientos de
             empaquetado comparten el mismo reference.

        No se asume ninguna ruta fija de ubicacion: con configuracion +
        trazabilidad basta para saber donde buscar, y la decision de mover
        la da el stock realmente existente (quants). Si no hay ninguna de
        las dos, el stock nunca salio de la separacion y no hay que
        revertir nada.
        """
        locations = self.env['stock.location']
        cfg = self.env['review.stock.config']._get_config(reviews[0].company_id)
        if cfg.exit_location_id:
            locations |= cfg.exit_location_id
        if dest_loc:
            transfers = self.env['stock.move'].sudo().search([
                ('reference', 'in', reviews.mapped('name')),
                ('state', '=', 'done'),
            ])
            locations |= transfers.location_dest_id.filtered(
                lambda loc: loc.usage == 'internal')
            locations = locations - dest_loc
        return locations

# -*- coding: utf-8 -*-
from odoo import fields, models, _
from odoo.exceptions import UserError


class StockReworkAuthWizard(models.TransientModel):
    """Wizard de autorizacion para volver a preparar.

    Permite que otro usuario (o el mismo) valide el re-proceso usando su
    PIN de autorizacion. Como el login de la compania es SSO (no hay
    contrasena local), el acceso se valida exclusivamente por PIN.
    """
    _name = 'stock.rework.auth.wizard'
    _description = 'Autorizacion para Volver a Preparar'

    origin = fields.Selection(
        [('review', 'Repaso'), ('picking', 'Picking de Salida')],
        string='Origen',
        required=True,
    )
    preparation_id = fields.Many2one(
        'preparation.stock',
        string='Preparacion',
        readonly=True,
    )
    review_id = fields.Many2one(
        'review.stock.picking',
        string='Repaso',
        readonly=True,
    )
    picking_id = fields.Many2one(
        'stock.picking',
        string='Picking de Salida',
        readonly=True,
    )
    username = fields.Char(
        string='Usuario',
        required=True,
    )
    pin = fields.Char(
        string='PIN de autorizacion',
        placeholder='Ingrese el PIN del usuario que autoriza',
        help="PIN de autorizacion del usuario para validar re-procesos.",
    )
    reason = fields.Text(
        string='Motivo',
        required=True,
        help="Describe brevemente por que es necesario volver a preparar.",
    )

    def _resolve_and_check_authorization(self):
        """Resuelve el res.users por login y valida PIN + pertenencia a la
        lista de usuarios autorizados de la configuracion."""
        self.ensure_one()
        if not self.pin:
            raise UserError(_(
                'Debe ingresar el PIN de autorizacion del usuario que autoriza.'
            ))

        User = self.env['res.users'].sudo()
        user = User.search([
            ('login', '=ilike', self.username.strip()),
            ('active', '=', True),
        ], limit=1)
        if not user:
            raise UserError(_(
                'Credenciales incorrectas o usuario inactivo.'
            ))

        if not user.verify_rework_pin(self.pin):
            raise UserError(_(
                'Credenciales incorrectas o usuario inactivo.'
            ))

        prep = self.preparation_id or (
            self.env['preparation.stock'].browse(self._context.get('default_preparation_id', False))
        )
        company = prep.company_id if prep else self.env.company
        cfg = self.env['review.stock.config']._get_config(company)
        if not cfg.rework_auth_applies(user):
            raise UserError(_(
                'El usuario %s no esta autorizado para esta operacion.'
            ) % user.name)

        return user

    def confirm(self):
        """Ejecuta la devolucion a preparacion tras validar la autorizacion."""
        self.ensure_one()
        auth_user = self._resolve_and_check_authorization()

        from ..services.rework_service import ReworkService
        service = ReworkService(self.env)

        if self.origin == 'review':
            review = self.review_id
            if not review:
                raise UserError(_('Debe indicar el repaso a volver a preparar.'))
            if not self.preparation_id:
                self.preparation_id = review.preparation_id.id
            prep = service.rework_from_review(
                review, auth_user, self.reason,
                authorized_by_id=auth_user.id,
            )
        else:
            picking = self.picking_id
            if not picking:
                raise UserError(_('Debe indicar el picking a devolver.'))
            if not self.preparation_id:
                self.preparation_id = picking.env['preparation.stock'].browse(
                    picking._context.get('default_preparation_id', False)
                ).id
            prep = service.rework_from_picking(
                picking, auth_user, self.reason,
                authorized_by_id=auth_user.id,
            )

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'preparation.stock',
            'res_id': prep.id,
            'views': [(False, 'form')],
            'target': 'current',
        }
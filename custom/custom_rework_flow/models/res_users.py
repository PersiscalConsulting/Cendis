# -*- coding: utf-8 -*-
from passlib.context import CryptContext

from odoo import api, fields, models

_pin_context = CryptContext(schemes=['pbkdf2_sha512'], deprecated=['auto'])


class ResUsers(models.Model):
    """Agrega un PIN de autorizacion para volver a preparar.

    Como muchos logins se validan por SSO (sin hash de contrasena local),
    cada usuario autorizado puede fijarse un PIN corto. Se almacena
    CIFRADO (solo se puede escribir, no se puede leer el valor original).
    """
    _inherit = 'res.users'

    rework_auth_pin = fields.Char(
        string='PIN de autorizacion (Volver a preparar)',
        groups='base.group_user',
        help="PIN que autoriza a este usuario a 'Volver a Preparar' "
             "cuando su login es SSO (no valida la contrasena web). "
             "Se almacena cifrado: escribirlo lo reemplaza; dejarlo "
             "vacio y guardar lo elimina.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('rework_auth_pin'):
                vals['rework_auth_pin'] = _pin_context.hash(
                    str(vals['rework_auth_pin']))
        return super().create(vals_list)

    def write(self, vals):
        if 'rework_auth_pin' in vals:
            if vals.get('rework_auth_pin'):
                vals['rework_auth_pin'] = _pin_context.hash(
                    str(vals['rework_auth_pin']))
            else:
                # Se ingreso vacio: borra el PIN.
                vals['rework_auth_pin'] = False
        return super().write(vals)

    def verify_rework_pin(self, pin):
        """Valida el PIN ingresado contra el hash almacenado.

        Retorna True si coincide; False si no hay PIN o no coincide."""
        self.ensure_one()
        if not self.rework_auth_pin:
            return False
        try:
            return bool(_pin_context.verify(pin or '', self.rework_auth_pin))
        except Exception:
            return False
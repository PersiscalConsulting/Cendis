
from odoo import models, api


class AccountPaymentMethod(models.Model):
    _inherit = 'account.payment.method'

    @api.model
    def _get_payment_method_information(self):
        res = super()._get_payment_method_information()
        res['withholding_suppliers'] = {'mode': 'multi', 'domain': [('type', '=', 'cash')]}
        res['withholding_customers'] = {'mode': 'multi', 'domain': [('type', '=', 'cash')]}
        return res

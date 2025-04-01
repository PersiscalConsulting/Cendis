# © 2016 ADHOC SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import models, fields


class AccountJournal(models.Model):
    _inherit = "account.journal"

    payment_sequence_id = fields.Many2one(
            'ir.sequence',
            'Secuencia dedicada de pagos'
    )

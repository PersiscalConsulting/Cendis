##############################################################################
# For copyright and license notices, see __manifest__.py file in root directory
##############################################################################
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class TransferCreateWizard(models.TransientModel):
    _name = "create.transfer.wizard"
    _description = "create.transfer.wizard"

    journal_id = fields.Many2one(
        'account.journal',
        'Journal',
    )
    destination_journal_id = fields.Many2one(
        'account.journal',
        'Destination Journal',
    )
    amount = fields.Float('Monto')


    def confirm(self):
        self.ensure_one()
        vals = {
                'payment_type': 'outbound',
                'payment_type_copy': 'outbound',
                'journal_id': self.journal_id.id,
                'destination_journal_id': self.destination_journal_id.id,
                'partner_type': 'customer',
                'partner_id': self.journal_id.company_id.partner_id.id,
                'amount': self.amount,
                'is_internal_transfer': True,
                }
        transfer_id = self.env['account.payment'].create(vals)
        transfer_id.write({'destination_journal_id': self.destination_journal_id.id})
        res = {
            'name': _('Transfer'),
            'res_model': 'account.payment',
            'view_mode': 'form',
            'res_id': transfer_id.id,
            'type': 'ir.actions.act_window',
            'target': 'new',
        }
        return res


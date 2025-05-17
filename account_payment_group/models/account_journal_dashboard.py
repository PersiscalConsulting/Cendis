from odoo import models, api, _
from odoo.exceptions import ValidationError

class AccountJournal(models.Model):
    _inherit = "account.journal"

    def open_payments_action(self, payment_type, mode='form'):
        if payment_type == 'transfer':
    #        ctx = self._context.copy()
    #        ctx.update({
    #            'default_payment_type': 'outbound',
    #            'default_journal_id': self.id,
    #            'default_destination_journal_id': self.id,
    #            'default_partner_id': self.company_id.partner_id.id,
    #        })
    #        ctx.pop('group_by', None)
    #        #action_rec = self.env['ir.model.data'].xmlid_to_object(
    #        #    'account_payment_group.action_account_payments_transfer')
    #        action_rec = self.env.ref(
    #            'account_payment_group.action_account_payments_transfer')
    #        action = action_rec.read([])[0]
    #        action['context'] = ctx
    #        action['domain'] = [('journal_id', '=', self.id),
    #                            ('is_internal_transfer', '=', True),
    #                            ('payment_type', '=', 'outbound')]
            vals_wizard = {
                    'journal_id': self.id,
                    }
            wizard_id = self.env['create.transfer.wizard'].create(vals_wizard)
            res = {
                'name': _('Create transfer wizard'),
                'res_model': 'create.transfer.wizard',
                'view_mode': 'form',
                'res_id': wizard_id.id,
                'type': 'ir.actions.act_window',
                'target': 'new',
            }
            return res
        return super(AccountJournal, self).open_payments_action(payment_type,mode)

    #def open_payments_action(self, payment_type, mode='form'):
    #    if payment_type == 'transfer':
    #        ctx = self._context.copy()
    #        ctx.update({
    #            'default_payment_type': 'outbound',
    #            'default_journal_id': self.id,
    #            'default_destination_journal_id': self.id,
    #            'default_partner_id': self.company_id.partner_id.id,
    #        })
    #        ctx.pop('group_by', None)
    #        #action_rec = self.env['ir.model.data'].xmlid_to_object(
    #        #    'account_payment_group.action_account_payments_transfer')
    #        action_rec = self.env.ref(
    #            'account_payment_group.action_account_payments_transfer')
    #        action = action_rec.read([])[0]
    #        action['context'] = ctx
    #        action['domain'] = [('journal_id', '=', self.id),
    #                            ('is_internal_transfer', '=', True),
    #                            ('payment_type', '=', 'outbound')]
    #        return action
    #    return super(AccountJournal, self).open_payments_action(payment_type,mode)

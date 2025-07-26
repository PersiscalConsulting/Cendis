from odoo import models, api

class AccountJournal(models.Model):
    _inherit = 'account.journal'

    @api.model
    def _get_codes_per_journal_type(self, afip_pos_system):
        usual_codes = ['1', '2', '3', '6', '7', '8', '11', '12', '13']
        mipyme_codes = ['201', '202', '203', '206', '207', '208', '211', '212', '213']
        invoice_m_code = ['51', '52', '53']
        receipt_m_code = ['54']
        receipt_codes = ['4', '9', '15']
        expo_codes = ['19', '20', '21']
        zeta_codes = ['80', '83']
        lsg_codes = ['331']
        no_pos_docs = [
            '23', '24', '25', '26', '27', '28', '33', '43', '45', '46', '48', '58', '60', '61', '150', '151', '157',
            '158', '161', '162', '164', '166', '167', '171', '172', '180', '182', '186', '188', '332'
        ]

        codes = []
        if (self.type == 'sale' and not self.l10n_ar_is_pos) or            (self.type == 'purchase' and afip_pos_system in ['II_IM', 'RLI_RLM']):
            codes = no_pos_docs + lsg_codes
        elif self.type == 'purchase' and afip_pos_system == 'RAW_MAW':
            codes = ['60', '61']
        elif self.type == 'purchase':
            custom_exclude = list(no_pos_docs)
            if '180' in custom_exclude:
                custom_exclude.remove('180')
            return [('code', 'not in', custom_exclude)]
        elif afip_pos_system == 'II_IM':
            codes = usual_codes + receipt_codes + expo_codes + invoice_m_code + receipt_m_code
        elif afip_pos_system == 'RAW_MAW':
            codes = usual_codes + receipt_codes + invoice_m_code + receipt_m_code + mipyme_codes
        elif afip_pos_system == 'RLI_RLM':
            codes = usual_codes + receipt_codes + invoice_m_code + receipt_m_code + mipyme_codes + zeta_codes
        elif afip_pos_system in ['CPERCEL', 'CPEWS']:
            codes = usual_codes + invoice_m_code
        elif afip_pos_system in ['BFERCEL', 'BFEWS']:
            codes = usual_codes + mipyme_codes
        elif afip_pos_system in ['FEERCEL', 'FEEWS', 'FEERCELP']:
            codes = expo_codes

        return [('code', 'in', codes)]
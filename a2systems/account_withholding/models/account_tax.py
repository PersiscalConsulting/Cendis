from odoo import models, fields, api


TYPE_TAX_USE = [
    ('sale', 'Sales'),
    ('purchase', 'Purchases'),
    ('customer', 'Pagos a Clientes'),
    ('supplier', 'Pagos a Proveedores'),
    ('none', 'None'),
]

class AccountTax(models.Model):
    """
    We could also use inherits but we should create methods of chart template
    """
    _inherit = "account.tax"

    type_tax_use = fields.Selection(
            TYPE_TAX_USE, 
            string='Tax Type', 
            required=True, 
            default="sale",
            help="Determines where the tax is selectable. Note : 'None' means a tax can't be used by itself, however it can still be used in a group. 'adjustment' is used to perform tax adjustment.")
    amount = fields.Float(
        default=0.0,
    )
    withholding_sequence_id = fields.Many2one(
        'ir.sequence',
        'Secuencia de impuestos',
        copy=False
    )

    @api.model
    def create(self, vals):
        tax = super(AccountTax, self).create(vals)
        if tax.type_tax_use == 'supplier' and not tax.withholding_sequence_id:
            tax.withholding_sequence_id = self.withholding_sequence_id.\
                sudo().create({
                    'name': tax.name,
                    'implementation': 'no_gap',
                    # 'prefix': False,
                    'padding': 8,
                    'number_increment': 1,
                    'code': 'tax.name',
                    'company_id': tax.company_id.id,
                }).id
        return tax

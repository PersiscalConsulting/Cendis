# © 2016 ADHOC SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
{
    "name": "Account Payment with Multiple methods",
    "version": "17.0.1.0.0",
    "category": "Accounting",
    "author": "A2 Systems,ADHOC SA,",
    "license": "AGPL-3",
    "application": False,
    'installable': True,
    "external_dependencies": {
        "python": [],
        "bin": [],
    },
    "depends": [
        "account_financial_amount",
        # for fixes related to domains and company_id field on form view
        "account_payment_fix",
        "l10n_latam_check"
    ],
    "data": [
        'security/security.xml',
        'security/ir.model.access.csv',
        'wizards/account_payment_group_invoice_wizard_view.xml',
        'wizards/transfer_create_view.xml',
        'views/account_payment_view.xml',
        'views/account_journal_view.xml',
        'views/account_move_line_view.xml',
        'views/account_payment_group_view.xml',
        'views/account_payment_receiptbook_view.xml',
    ],
    "demo": [
    ],
}

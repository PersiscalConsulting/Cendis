import logging

from odoo.upgrade import util

_logger = logging.getLogger(__name__)

from odoo.addons.base.models.ir_ui_view import View


def _check_xml(self):
  return True


View._check_xml = _check_xml



def migrate(cr, version):
    env = util.env(cr)
    _logger.info("Eliminamos index para que sean recreados en la migracion a Odoo 17")

   # 2025-06-13 02:40:38,276 127 ERROR persiscal-cendis-stagingv18-21180134 odoo.schema: Table 'l10n_latam_check': unable to set NOT NULL on column 'payment_date'

    env['ir.model.data'].search([('module', '=', 'account_payment_group'), ('name', '=', 'account_see_payment_menu')]).noupdate=False
    env['ir.model.data'].search([('module', '=', 'l10n_ar_ux'), ('model', '=', 'account.account.tag')]).noupdate=True

    env.ref('l10n_ar_ux.view_partner_property_form').active = True
    env.ref('account_withholding.view_account_payment_form').active = True
    env.ref('l10n_ar_currency_update.res_config_settings_view_form').active = True
    env.ref('account_accountant_ux.view_account_change_lock_date').active = True
    env.ref('account_accountant_ux.res_config_settings_view_form').active = True
    env.ref('l10n_ar_ux.custom_header').active = True
    env.ref('l10n_ar_ux.view_account_payment_form').active = True
    env.ref('l10n_ar_ux.res_config_settings_inherit_view_form').active = True
    env.ref('l10n_ar_sale.res_config_settings_view_form').active = True
    env.ref('account_ux.view_res_config_settings').active = True


    cr.execute("UPDATE l10n_latam_check SET payment_date = write_date where payment_date IS NULL")

    cr.execute("DROP TABLE IF EXISTS account_payment_bkp")
    cr.execute("CREATE TABLE account_payment_bkp AS TABLE account_payment")

    cr.execute("DROP INDEX IF EXISTS account_move_unique_name")
    cr.execute("DROP INDEX IF EXISTS account_move_unique_name_latam")
    cr.execute("""UPDATE account_move
                    SET name = CONCAT(account_move.name, ' (', delta_moves.row_number, ')')
                    FROM (SELECT * FROM (
                        SELECT id,name,journal_id,
                        ROW_NUMBER() OVER (PARTITION BY name ORDER BY name) AS row_number
                        FROM account_move
                        WHERE state = 'posted' AND name != '/'
                        AND ( l10n_latam_document_type_id IS NULL OR move_type NOT IN ('in_invoice', 'in_refund', 'in_receipt'))
                    ) as grouped_moves WHERE row_number > 1
                    ) AS delta_moves
                    WHERE account_move.id = delta_moves.id
        """)
    cr.execute("""
                CREATE UNIQUE INDEX account_move_unique_name
                                 ON account_move(name, journal_id)
                              WHERE (state = 'posted' AND name != '/'
                                AND (l10n_latam_document_type_id IS NULL OR move_type NOT IN ('in_invoice', 'in_refund', 'in_receipt')));
                CREATE UNIQUE INDEX account_move_unique_name_latam
                                 ON account_move(name, commercial_partner_id, l10n_latam_document_type_id, company_id)
                              WHERE (state = 'posted' AND name != '/'
                                AND (l10n_latam_document_type_id IS NOT NULL AND move_type IN ('in_invoice', 'in_refund', 'in_receipt')));
    """)

    # if index_exists(cr, "account_move_unique_name"):
    #     drop_index(cr, "account_move_unique_name", "account_move")
    _logger.info("Cambiamos nombre tecnico de modulo account_payment_group por account_payment_pro 1")



    util.update_record_from_xml(cr,'l10n_ar_ux.view_partner_property_form')

    util.remove_view(cr,'account_payment_group.view_move_form')
    util.remove_view(cr,'account_payment_group.view_account_payment_tree')
    util.remove_view(cr,'l10n_ar_account_withholding.view_partner_form')

    # l10n_ar_withholding, l10n_ar_sale

    util.merge_module(cr, "account_payment_group", "account_payment_pro_receiptbook", update_dependers=True)
    util.merge_module(cr, "l10n_ar_account_withholding", "l10n_ar_withholding")

    env['ir.model.data'].search([('module', '=', 'account_payment_pro_receiptbook'), ('name', '=', 'model_account_payment_group')]).noupdate=True
    util.merge_module(cr, "account_withholding", "l10n_ar_tax", update_dependers=True)
    util.merge_module(cr, "account_withholding_automatic", "l10n_ar_tax_backward_compatibility", update_dependers=True)
    util.force_install_module(cr, "l10n_ar_payment_bundle")

    cr.execute("UPDATE l10n_latam_check SET payment_date = write_date where payment_date IS NULL")

    util.force_install_module(cr, "l10n_ar_tax_backward_compatibility")

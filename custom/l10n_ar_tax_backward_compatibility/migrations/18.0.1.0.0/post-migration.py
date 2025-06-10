import logging

from odoo.upgrade import util

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = util.env(cr)
    env.cr.execute("""
        insert into l10n_ar_payment_withholding
            (payment_id,name,tax_id,base_amount,amount,automatic,withholdable_invoiced_amount,withholdable_advanced_amount,
            accumulated_amount,total_amount,withholding_non_taxable_minimum,withholding_non_taxable_amount,withholdable_base_amount,
            period_withholding_amount,previous_withholding_amount,computed_withholding_amount,create_date,create_uid,write_date,write_uid
            )
        select
            ap.id as payment_id,withholding_number,tax_withholding_id,withholding_base_amount,amount,automatic,withholdable_invoiced_amount,withholdable_advanced_amount,
            accumulated_amount,total_amount,withholding_non_taxable_minimum,withholding_non_taxable_amount,withholdable_base_amount,period_withholding_amount,previous_withholding_amount,computed_withholding_amount,
            ap.create_date,ap.create_uid,ap.write_date,ap.write_uid
        from account_payment_bkp as ap
        join account_payment_method apm on apm.id = ap.payment_method_id
        where apm.code = 'withholding';
    """)
    env.cr.execute("""
        update account_payment set is_backward_withholding_payment=true , amount=0 where  id in  (
            select ap.id
            from account_payment_bkp as ap
            join account_payment_method apm on apm.id = ap.payment_method_id
            where apm.code = 'withholding'
        );
    """)

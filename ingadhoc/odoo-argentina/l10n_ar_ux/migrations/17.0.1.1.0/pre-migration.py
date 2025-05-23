import logging
from odoo.upgrade import util

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    _logger.info("Cambiamos nombre tecnico de modulo account_payment_group por account_payment_pro 1")
    env = util.env(cr)
    
    util.merge_module(cr, "account_payment_group", "account_payment_pro", update_dependers=True)
    util.merge_module(cr, "account_withholding", "l10n_ar_account_withholding", update_dependers=True)
    util.merge_module(cr, "account_withholding_automatic", "l10n_ar_withholding_ux", update_dependers=True)
    _logger.info("Removemos el modelo account_payment_group")
    util.models.remove_model(cr, "account.payment.group", drop_table=True, ignore_m2m=())

    """
    Fix duplicated account.move name/journal_id combinations to allow applying
    the new unique index during the migration to Odoo 17.
    """
    cr.execute("""
        SELECT name, journal_id, array_agg(id) AS move_ids
        FROM account_move
        WHERE state = 'posted'
          AND name != '/'
          AND payment_group_id IS NULL
          AND (l10n_latam_document_type_id IS NULL OR move_type NOT IN ('in_invoice', 'in_refund', 'in_receipt'))
        GROUP BY name, journal_id
        HAVING COUNT(*) > 1
    """)

    duplicates = cr.fetchall()
    _logger.info("**** Found %s duplicated account.move names", len(duplicates))

    for name, journal_id, move_ids in duplicates:
        move_ids = list(move_ids)
        move_ids.sort()

        for i, move_id in enumerate(move_ids):
            new_name = f"{name}_{i+1}"
            cr.execute("""
                UPDATE account_move
                SET name = %s
                WHERE id = %s
            """, (new_name, move_id))

        _logger.warning(f"Renamed {len(move_ids)} moves for name='{name}' and journal_id={journal_id}")
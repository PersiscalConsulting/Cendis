import logging
from odoo.upgrade import util

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("DROP INDEX IF EXISTS account_move_unique_name")
    cr.execute("DROP INDEX IF EXISTS account_move_unique_name_latam")
    cr.execute("""UPDATE account_move
                    SET name = CONCAT(account_move.name, ' (', subquery.rn, ')')
                    FROM (SELECT * FROM (
                        SELECTid,name,journal_id,
                        ROW_NUMBER() OVER (PARTITION BY name, journal_id ORDER BY name) AS row_num
                        FROM account_move
                        WHERE state = 'posted AND name != '/'
                        AND ( l10n_latam_document_type_id IS NULL OR move_type NOT IN ('in_invoice', 'in_refund', 'in_receipt'))
                    ) as grouped_moves WHERE row_num > 1
                    ) AS delta_moves
                    WHERE account_move.id = delta_moves.id
        """)
    _logger.info("Cambiamos nombre tecnico de modulo account_payment_group por account_payment_pro 1")
    env = util.env(cr)
    util.rename_module(cr, "account_payment_group", "account_payment_pro")
    _logger.info("Cambiamos nombre tecnico de modulo account_payment_group por account_payment_pro 2")
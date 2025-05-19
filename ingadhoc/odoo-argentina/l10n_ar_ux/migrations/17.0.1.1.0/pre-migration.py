import logging
from odoo.upgrade import util

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    _logger.info("Cambiamos nombre tecnico de modulo account_payment_group por account_payment_pro 1")
    env = util.env(cr)
    
    util.merge_module(cr, "account_payment_group", "account_payment_pro", update_dependers=True)
    _logger.info("Cambiamos nombre tecnico de modulo account_payment_group por account_payment_pro 2")
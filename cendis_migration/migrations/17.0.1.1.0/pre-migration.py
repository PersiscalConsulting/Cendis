import logging
from openupgradelib import openupgrade

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    _logger.info("Cambiamos nombre tecnico de modulo account_payment_group por account_payment_pro 1")
    env = util.env(cr)
    
    util.rename_module(cr, "account_payment_group", "account_payment_pro")
    _logger.info("Cambiamos nombre tecnico de modulo account_payment_group por account_payment_pro 2")
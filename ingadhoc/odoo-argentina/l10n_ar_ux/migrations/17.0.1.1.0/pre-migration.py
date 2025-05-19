import logging
from openupgradelib import openupgrade

logger = logging.getLogger(__name__)


def migrate(cr, version):
    logger.info("Cambiamos nombre tecnico de modulo account_payment_group por account_payment_pro 1")


    openupgrade.rename_module(cr, "account_payment_group", "account_payment_pro")

    logger.info("Cambiamos nombre tecnico de modulo account_payment_group por account_payment_pro 2")
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    cr.execute("UPDATE l10n_latam_check SET payment_date = write_date where payment_date IS NULL")

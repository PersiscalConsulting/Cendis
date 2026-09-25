# -*- coding: utf-8 -*-
"""Configuration cache for review stock picking.

Provides cross-request caching for review.stock.config records
to avoid repeated database queries during scan operations.

IMPORTANT: We cache only the record ID, never the recordset itself.
Odoo recordsets are bound to a specific database cursor. Caching
the full recordset across requests leads to "Cursor already closed"
errors because each request uses a different cursor.
"""
from odoo import models


class ReviewConfigCache(models.AbstractModel):
    """Mixin to provide cached config access for review models."""

    _name = 'review.config.cache.mixin'
    _description = 'Review Config Cache Mixin'

    _config_id_cache = {}

    def _get_config(self):
        """Get config with cross-request caching via record ID.

        Cache key: (dbname, company_id) for process-level isolation.
        """
        self.ensure_one()
        company_id = self.company_id.id if self.company_id else self.env.company.id
        cache_key = (self.env.cr.dbname, company_id)

        config_id = self._config_id_cache.get(cache_key)
        if config_id:
            config = self.env['review.stock.config'].browse(config_id)
            if config.exists():
                return config
            del self._config_id_cache[cache_key]

        config = self.env['review.stock.config']._get_config(self.company_id)
        self._config_id_cache[cache_key] = config.id
        return config

    @classmethod
    def clear_cache(cls):
        cls._config_id_cache.clear()

    @classmethod
    def invalidate_company(cls, company_id):
        keys_to_remove = [k for k in cls._config_id_cache if k[1] == company_id]
        for key in keys_to_remove:
            del cls._config_id_cache[key]


def get_cached_config(env, company_id=None):
    """Standalone function to get cached config without model instance.

    Caches only the record ID. On cache hit, does a fresh browse()
    with the current env so the recordset is bound to the active cursor.
    """
    company_id = company_id or env.company.id
    cache_key = (env.cr.dbname, company_id)

    if not hasattr(get_cached_config, '_cache'):
        get_cached_config._cache = {}

    config_id = get_cached_config._cache.get(cache_key)
    if config_id:
        config = env['review.stock.config'].browse(config_id)
        if config.exists():
            return config
        del get_cached_config._cache[cache_key]

    config = env['review.stock.config']._get_config(
        env['res.company'].browse(company_id)
    )
    get_cached_config._cache[cache_key] = config.id
    return config


def clear_config_cache():
    """Clear the standalone config cache."""
    if hasattr(get_cached_config, '_cache'):
        get_cached_config._cache.clear()

# -*- coding: utf-8 -*-
"""Consolidated validation engine for review stock operations.

Centralizes all blocking validations to avoid duplication across models
and services.
"""
from odoo import _
from odoo.exceptions import UserError
from odoo.tools import float_compare

EPS = 0.0001


class ReviewValidationEngine:
    """Centralized validation logic for review stock operations."""

    @staticmethod
    def validate_move_line_create(move, vals_list, context=None):
        """Validations on move line creation."""
        context = context or {}
        if context.get('review_auto_create'):
            return
        for vals in vals_list:
            move_id = vals.get('review_move_id')
            if not move_id:
                continue
            m = move.env['review.move'].browse(move_id)
            cfg = m._get_config()
            if not cfg.manual_review_applies(m.owner_id):
                raise UserError(_(
                    'No esta habilitada la creacion manual de lineas de '
                    'repaso para este propietario.'
                ))

    @staticmethod
    def validate_move_line_write(line, vals, context=None):
        """Validations on move line write."""
        context = context or {}
        if context.get('review_auto_create'):
            return
        cfg = line._get_config()
        owner = line._get_owner()
        if not cfg.manual_review_applies(owner):
            raise UserError(_(
                'No esta habilitado el repaso manual para este propietario.'
            ))

    @staticmethod
    def validate_package_target(review, move_line_ids, package_id=False):
        """Validate lines before packaging. Returns (lines, error)."""
        if not move_line_ids:
            return (
                review.env['review.move.line'],
                'Seleccione al menos una linea para paquetizar.',
            )
        lines = review.env['review.move.line'].browse(move_line_ids)
        invalid = lines.filtered(
            lambda l: l.review_move_id.review_id.id != review.id
        )
        if invalid:
            return (lines, 'Algunas lineas no pertenecen a este repaso.')
        already_packaged = lines.filtered('package_id')
        if already_packaged:
            return (lines, 'Algunas lineas ya estan asignadas a un paquete.')
        not_reviewed = lines.filtered(
            lambda l: l.state != 'done'
            or float_compare(l.qty_reviewed, 0.0, precision_digits=4) <= 0
        )
        if not_reviewed:
            names = ', '.join(
                not_reviewed.mapped('review_move_id.product_id.display_name')
            )
            return (
                lines,
                'Solo pueden paquetizarse cantidades ya repasadas. '
                'Repase primero: %s' % names,
            )
        if package_id:
            pkg = review.env['stock.quant.package'].browse(package_id)
            if not pkg.exists():
                return (lines, 'El paquete de destino no existe.')
        return (lines, '')

    @staticmethod
    def validate_unpackaging(review):
        """Verify unpackaging is enabled for the owner."""
        cfg = review.env['review.stock.config']._get_config(review.company_id)
        if not cfg.unpackaging_applies(review.owner_id):
            raise UserError(_(
                'El desempaquetado no esta habilitado para este propietario.'
            ))

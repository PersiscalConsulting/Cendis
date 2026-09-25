from odoo import _, fields, models
from odoo.exceptions import UserError


class PreparationStockImportWizard(models.TransientModel):
    _name = 'preparation.stock.import.wizard'
    _description = 'Importar Preparación desde Recepción'

    preparation_id = fields.Many2one(
        'preparation.stock',
        string='Preparación',
        required=True,
        readonly=True,
    )
    origin_doc = fields.Char(
        related='preparation_id.origin_doc',
        string='Documento de origen',
        readonly=True,
    )
    pack_incluido = fields.Boolean(
        string='Incluir mercadería en paquetes',
        default=False,
        help='Si se marca, también se importará el stock que permanece en paquetes.',
    )

    def action_import(self):
        self.ensure_one()
        if not self.preparation_id:
            raise UserError(_('No se indicó la preparación.'))
        if not self.origin_doc or not self.origin_doc.strip():
            raise UserError(_('La preparación no tiene un documento de origen.'))
        action = self.env.ref(
            'custom_preparation_stock.action_import_from_reception',
            raise_if_not_found=False,
        )
        if not action:
            raise UserError(_('No existe la acción de importación desde recepción.'))
        return action.with_context(
            active_model='preparation.stock',
            active_id=self.preparation_id.id,
            active_ids=self.preparation_id.ids,
            reception_import_pack_incluido=self.pack_incluido,
        ).run()

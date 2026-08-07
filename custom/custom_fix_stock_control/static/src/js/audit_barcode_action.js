/** @odoo-module **/

import { registry } from "@web/core/registry";
import { BarcodeAuditAction } from "@custom_stock_control_barcode/js/audit_barcode_action";

class AuditBarcodeActionFix extends BarcodeAuditAction {}

AuditBarcodeActionFix.template = "custom_fix_stock_control.BarcodeAuditAction";

registry.category("actions").add(
    "custom_stock_control_barcode.audit_barcode_action",
    AuditBarcodeActionFix,
    { force: true }
);

export { AuditBarcodeActionFix };

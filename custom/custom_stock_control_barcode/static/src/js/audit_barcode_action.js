/** @odoo-module **/

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

class BarcodeAuditAction extends Component {
    static template = "custom_stock_control_barcode.BarcodeAuditAction";

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");
        this.barcodeService = useService("barcode");
        this.dialog = useService("dialog");

        this.state = useState({
            audit: null,
            lines: [],
            scannedLocations: [],
            currentScanLocation: null,
            locationName: "",
            currentBarcode: "",
            barcodeInput: "",
            message: "Escanee la ubicacion para comenzar",
            messageType: "info",
            loading: true,
            locationScanned: false,
            auditState: "draft",
            isFinished: false,
        });

        this.auditId = this.props.action.context?.active_id
            || this.props.action.params?.audit_id;

        this._barcodeBuffer = "";
        this._processing = false;
        this._onKeyDown = this._onKeyDown.bind(this);

        onWillStart(async () => {
            await this._loadAudit();
        });

        if (this.barcodeService && this.barcodeService.bus) {
            this.barcodeService.bus.addEventListener(
                "barcode_scanned",
                this._onBarcodeScanned
            );
        }

        document.addEventListener("keydown", this._onKeyDown);

        onWillUnmount(() => {
            if (this.barcodeService && this.barcodeService.bus) {
                this.barcodeService.bus.removeEventListener(
                    "barcode_scanned",
                    this._onBarcodeScanned
                );
            }
            document.removeEventListener("keydown", this._onKeyDown);
        });
    }

    _onKeyDown(ev) {
        if (this.state.isFinished) {
            return;
        }
        if (ev.target && (ev.target.tagName === "INPUT" || ev.target.tagName === "TEXTAREA")) {
            return;
        }
        if (ev.key === "Enter" && this._barcodeBuffer.length >= 3) {
            this._processBarcode(this._barcodeBuffer);
            this._barcodeBuffer = "";
        } else if (ev.key.length === 1) {
            this._barcodeBuffer += ev.key;
        }
    }

    async _loadAudit() {
        if (!this.auditId) {
            this.state.message = "No se especifico una auditoria.";
            this.state.messageType = "danger";
            this.state.loading = false;
            return;
        }
        try {
            const audit = await this.orm.call(
                "stock.inventory.audit",
                "search_read",
                [[["id", "=", this.auditId]]],
                {
                    fields: [
                        "id", "name", "location_id", "state",
                        "current_scan_location_id", "scanned_location_ids",
                    ],
                    limit: 1,
                }
            );
            if (audit && audit.length > 0) {
                this.state.audit = audit[0];
                this.state.auditState = audit[0].state;

                if (audit[0].state === "review" || audit[0].state === "done") {
                    this.state.isFinished = true;
                    this.state.message = "";
                    this.state.messageType = "info";
                    await this._loadExistingLines();
                    return;
                }

                if (audit[0].scanned_location_ids && audit[0].scanned_location_ids.length > 0) {
                    await this._loadScannedLocations(audit[0].scanned_location_ids);
                }

                if (audit[0].current_scan_location_id) {
                    this.state.currentScanLocation = {
                        id: audit[0].current_scan_location_id[0],
                        name: audit[0].current_scan_location_id[1] || "",
                    };
                    this.state.locationName = audit[0].current_scan_location_id[1] || "";
                    this.state.locationScanned = true;
                    this.state.message = "Ubicacion cargada. Escanee los productos.";
                    this.state.messageType = "success";
                }
                await this._loadExistingLines();
            } else {
                this.state.message = "Auditoria no encontrada.";
                this.state.messageType = "danger";
            }
        } catch (error) {
            this.state.message = "Error al cargar la auditoria.";
            this.state.messageType = "danger";
        } finally {
            this.state.loading = false;
        }
    }

    async _loadScannedLocations(locationIds) {
        try {
            const locations = await this.orm.call(
                "stock.location",
                "read",
                [locationIds],
                { fields: ["id", "complete_name"] }
            );
            this.state.scannedLocations = locations.map((l) => ({
                id: l.id,
                name: l.complete_name || "",
            }));
        } catch (error) {
            // Silently fail
        }
    }

    async _loadExistingLines() {
        try {
            const lines = await this.orm.call(
                "stock.inventory.audit.line",
                "search_read",
                [[["audit_id", "=", this.auditId]]],
                {
                    fields: [
                        "product_id", "lot_id", "counted_qty", "location_id",
                        "theoretical_qty", "difference", "difference_reason", "has_error",
                    ],
                }
            );
            if (lines && lines.length > 0) {
                this.state.lines = lines.map((l) => ({
                    product_id_db: l.product_id[0],
                    product_name: l.product_id[1] || "",
                    counted_qty: l.counted_qty,
                    theoretical_qty: l.theoretical_qty || 0,
                    lot_name: l.lot_id ? l.lot_id[1] : "",
                    location_id: l.location_id ? l.location_id[0] : null,
                    location_name: l.location_id ? l.location_id[1] : "",
                    difference: l.difference || 0,
                    difference_reason: l.difference_reason || "",
                    has_error: l.has_error || false,
                }));
            }
        } catch (error) {
            // Silently fail
        }
    }

    _onBarcodeScanned = (event) => {
        if (this.state.isFinished) {
            return;
        }
        const barcode = event.detail?.barcode || event.detail || "";
        if (barcode) {
            this._processBarcode(barcode);
        }
    };

    onBarcodeInput(ev) {
        this.state.barcodeInput = ev.target.value;
    }

    onSubmitBarcode() {
        if (this.state.isFinished) {
            return;
        }
        const value = this.state.barcodeInput.trim();
        if (value) {
            this._processBarcode(value);
            this.state.barcodeInput = "";
        }
    }

    onInputKeydown(ev) {
        if (ev.key === "Enter") {
            ev.preventDefault();
            this.onSubmitBarcode();
        }
    }

    async _processBarcode(barcode) {
        if (this._processing || this.state.isFinished) {
            return;
        }
        this._processing = true;
        this.state.currentBarcode = barcode;
        try {
            const result = await this.orm.call(
                "stock.inventory.audit",
                "process_barcode_scan",
                [this.auditId, barcode]
            );
            if (result) {
                if (result.error) {
                    this.state.message = result.error;
                    this.state.messageType = "danger";
                } else if (result.is_location) {
                    const loc = { id: result.location_id, name: result.location_name };
                    this.state.currentScanLocation = loc;
                    this.state.locationName = result.location_name;
                    this.state.locationScanned = true;
                    if (!this.state.scannedLocations.find((l) => l.id === loc.id)) {
                        this.state.scannedLocations = [...this.state.scannedLocations, loc];
                    }
                    this.state.message = result.message;
                    this.state.messageType = "success";
                } else {
                    this.state.message = result.product_name + " - Cantidad: " + result.scanned_qty;
                    this.state.messageType = "success";
                    this._updateLinesLocally(result);
                }
            }
        } catch (error) {
            this.state.message = "Error al procesar el codigo de barras.";
            this.state.messageType = "danger";
        } finally {
            this._processing = false;
        }
    }

    _updateLinesLocally(result) {
        const lotKey = result.lot_name || "";
        const locId = result.location_id || null;
        const existingIdx = this.state.lines.findIndex(
            (l) => l.product_id_db === result.product_id
                && (l.lot_name || "") === lotKey
                && l.location_id === locId
        );
        if (existingIdx >= 0) {
            const lines = [...this.state.lines];
            lines[existingIdx] = {
                ...lines[existingIdx],
                counted_qty: result.scanned_qty,
            };
            this.state.lines = lines;
        } else {
            this.state.lines = [
                ...this.state.lines,
                {
                    product_id_db: result.product_id,
                    product_name: result.product_name,
                    counted_qty: result.scanned_qty || 1,
                    theoretical_qty: result.theoretical_qty || 0,
                    lot_name: result.lot_name || "",
                    location_id: result.location_id || null,
                    location_name: result.location_name || "",
                    difference: (result.scanned_qty || 1) - (result.theoretical_qty || 0),
                    difference_reason: "",
                    has_error: false,
                },
            ];
        }
    }

    onFinish() {
        this.actionService.doAction({
            type: "ir.actions.act_window",
            res_model: "stock.inventory.audit.finish.wizard",
            res_id: false,
            views: [[false, "form"]],
            target: "new",
            context: { default_audit_id: this.auditId },
        });
    }

    onCancel() {
        this.dialog.add(AlertDialog, {
            title: "Cancelar Auditoria",
            body: "Se eliminaran todas las lineas escaneadas. Desea continuar?",
            confirm: async () => {
                try {
                    await this.orm.call(
                        "stock.inventory.audit",
                        "action_cancel_scan",
                        [this.auditId]
                    );
                    this.notification.add("Auditoria cancelada.", {
                        type: "info",
                        title: "Cancelado",
                    });
                    this._navigateBack();
                } catch (error) {
                    this.notification.add("Error al cancelar la auditoria.", {
                        type: "danger",
                        title: "Error",
                    });
                }
            },
        });
    }

    onBack() {
        this._navigateBack();
    }

    _navigateBack() {
        try {
            this.actionService.restore();
        } catch (error) {
            this.actionService.doAction({
                type: "ir.actions.act_window",
                res_model: "stock.inventory.audit",
                res_id: this.auditId,
                views: [[false, "form"]],
                target: "current",
            }, { clear_breadcrumbs: true });
        }
    }
}

registry.category("actions").add(
    "custom_stock_control_barcode.audit_barcode_action",
    BarcodeAuditAction
);

export { BarcodeAuditAction };

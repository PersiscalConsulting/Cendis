/** @odoo-module */

import { registry } from "@web/core/registry";
import { Component, useState, onWillStart, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { CheckBox } from "@web/core/checkbox/checkbox";

class OwnerMoveErrorDialog extends Component {
    static template = "custom_owner_stock_change.OwnerMoveErrorDialog";
    static props = {
        title: { type: String },
        errors: { type: Array, element: String },
        close: { type: Function, optional: true },
    };
}

class OwnerMoveScanAction extends Component {
    static template = "custom_owner_stock_change.OwnerMoveScanAction";
    static components = { CheckBox };

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");
        this.barcodeService = useService("barcode");
        this.dialog = useService("dialog");

        this.state = useState({
            move: null,
            details: [],
            summaryLines: [],
            scannedLocations: [],
            currentScanLocation: null,
            locationName: "",
            currentBarcode: "",
            barcodeInput: "",
            message: "Escanee la ubicacion para comenzar",
            messageType: "info",
            loading: true,
            locationScanned: false,
            isFinished: false,
            manualQtyEnabled: false,
            allowDeleteEnabled: false,
        });

        this.moveId = this.props.action.context?.active_id
            || this.props.action.params?.move_id;

        this._barcodeBuffer = "";
        this._lastBarcode = "";
        this._lastBarcodeTime = 0;
        this._processing = false;
        this._onKeyDown = this._onKeyDown.bind(this);
        this.onChangeQty = this.onChangeQty.bind(this);
        this.onDeleteDetail = this.onDeleteDetail.bind(this);
        this.onToggleSelection = this.onToggleSelection.bind(this);

        onWillStart(async () => {
            await this._loadMove();
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
        if (ev.key === "Enter") {
            const barcode = this._barcodeBuffer;
            this._barcodeBuffer = "";
            if (barcode.length >= 3 && !this._processing) {
                this._processBarcode(barcode);
            }
        } else if (ev.key.length === 1) {
            this._barcodeBuffer += ev.key;
        }
    }

    async _loadMove() {
        if (!this.moveId) {
            this.state.message = "No se especifico una operacion.";
            this.state.messageType = "danger";
            this.state.loading = false;
            return;
        }
        try {
            const move = await this.orm.call(
                "owner.move.stock",
                "search_read",
                [[["id", "=", this.moveId]]],
                {
                    fields: [
                        "id", "name", "state", "owner_id", "dest_owner_id",
                        "current_scan_location_id", "scanned_location_ids",
                        "current_pack_seq",
                    ],
                    limit: 1,
                }
            );
            if (move && move.length > 0) {
                this.state.move = move[0];
                if (move[0].state !== "waiting") {
                    this.state.isFinished = true;
                    this.state.message = "";
                    this.state.messageType = "info";
                    await this._loadDetails();
                    return;
                }
                if (move[0].scanned_location_ids && move[0].scanned_location_ids.length > 0) {
                    this.state.scannedLocations = move[0].scanned_location_ids.map((loc) => ({
                        id: loc[0],
                        name: loc[1] || "",
                    }));
                }
                if (move[0].current_scan_location_id) {
                    this.state.currentScanLocation = {
                        id: move[0].current_scan_location_id[0],
                        name: move[0].current_scan_location_id[1] || "",
                    };
                    this.state.locationName = move[0].current_scan_location_id[1] || "";
                    this.state.locationScanned = true;
                    this.state.message = "Ubicacion cargada. Escanee los productos.";
                    this.state.messageType = "success";
                }
                try {
                    const settings = await this.orm.call(
                        "owner.move.stock", "get_scan_settings", []
                    );
                    this.state.manualQtyEnabled = settings.manual_qty;
                    this.state.allowDeleteEnabled = settings.allow_delete;
                } catch (error) {
                    this.state.manualQtyEnabled = false;
                    this.state.allowDeleteEnabled = false;
                }
                await this._loadDetails();
            } else {
                this.state.message = "Operacion no encontrada.";
                this.state.messageType = "danger";
            }
        } catch (error) {
            this.state.message = "Error al cargar la operacion.";
            this.state.messageType = "danger";
        } finally {
            this.state.loading = false;
        }
    }

    async _loadDetails() {
        const preserved = this._captureSelection();
        let rows = [];
        try {
            const details = await this.orm.call(
                "owner.move.stock.line.detail",
                "search_read",
                [[["move_id", "=", this.moveId]]],
                {
                    fields: [
                        "id", "product_id", "location_id", "lot_id", "qty",
                        "package_id", "packed", "pack_seq", "expiration_date",
                        "uom_id", "weight",
                    ],
                    order: "id asc",
                }
            );
            rows = this._mapDetails(details || []);
        } catch (error) {
            this.state.message = "Error al cargar los detalles.";
            this.state.messageType = "danger";
            return;
        }
        rows = this._applySelection(rows, preserved);
        this.state.details = rows;
        this.state.summaryLines = this._buildSummary(rows);
    }

    _mapDetails(details) {
        return details.map((d) => ({
            id: d.id,
            product_id_db: d.product_id[0],
            product_name: d.product_id[1] || "",
            location_id: d.location_id ? d.location_id[0] : null,
            location_name: d.location_id ? d.location_id[1] : "",
            lot_name: d.lot_id ? d.lot_id[1] : "",
            qty: d.qty,
            uom_name: d.uom_id ? d.uom_id[1] : "",
            package_id: d.package_id ? d.package_id[0] : null,
            package_name: d.package_id ? d.package_id[1] : "",
            packed: d.packed || false,
            pack_seq: d.pack_seq || 0,
            expiration_date: d.expiration_date || "",
            weight: d.weight || 0,
            selected: false,
        }));
    }

    _addDetailLocally(result) {
        const seq = this.state.move ? this.state.move.current_pack_seq : 0;
        if (this.state.details.some((d) => d.id === result.detail_id)) {
            return;
        }
        const rows = [
            ...this.state.details,
            {
                id: result.detail_id,
                product_id_db: result.product_id,
                product_name: result.product_name || "",
                location_id: result.location_id || null,
                location_name: result.location_name || "",
                lot_name: result.lot_name || "",
                qty: result.scanned_qty || 1.0,
                uom_name: "",
                package_id: result.package_id || null,
                package_name: "",
                packed: false,
                pack_seq: seq,
                expiration_date: result.expiration_date || "",
                weight: 0,
                selected: true,
            },
        ];
        this.state.details = rows;
        this.state.summaryLines = this._buildSummary(rows);
    }

    _captureSelection() {
        return new Set(this.state.details.filter((d) => d.selected).map((d) => d.id));
    }

    _applySelection(rows, preserved) {
        const seq = this.state.move ? this.state.move.current_pack_seq : 0;
        return rows.map((d) => {
            let selected = d.packed ? false : preserved.has(d.id);
            if (!selected && !d.packed && d.pack_seq === seq) {
                selected = true;
            }
            return { ...d, selected };
        });
    }

    _buildSummary(rows) {
        const grouped = {};
        for (const d of rows) {
            if (!grouped[d.product_id_db]) {
                grouped[d.product_id_db] = {
                    product_id_db: d.product_id_db,
                    product_name: d.product_name,
                    qty: 0,
                    packed_qty: 0,
                    locations: new Set(),
                    detailCount: 0,
                };
            }
            const g = grouped[d.product_id_db];
            g.qty += d.qty;
            if (d.packed) {
                g.packed_qty += d.qty;
            }
            if (d.location_name) {
                g.locations.add(d.location_name);
            }
            g.detailCount += 1;
        }
        return Object.values(grouped).map((g) => ({
            ...g,
            locations: Array.from(g.locations),
        }));
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
        const now = Date.now();
        if (barcode === this._lastBarcode && now - this._lastBarcodeTime < 1000) {
            return;
        }
        this._lastBarcode = barcode;
        this._lastBarcodeTime = now;
        this._processing = true;
        this.state.currentBarcode = barcode;
        try {
            const result = await this.orm.call(
                "owner.move.stock",
                "process_barcode_scan",
                [this.moveId, barcode]
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
                    if (result.lot_name) {
                        this.state.message += " | Lote: " + result.lot_name;
                    }
                    this.state.messageType = "success";
                    this._addDetailLocally(result);
                }
            }
        } catch (error) {
            this.state.message = "Error al procesar el codigo de barras.";
            this.state.messageType = "danger";
        } finally {
            this._processing = false;
        }
    }

    get selectedCount() {
        return this.state.details.filter((d) => d.selected && !d.packed).length;
    }

    onToggleSelection(detail, checked) {
        if (detail.packed) {
            return;
        }
        detail.selected = !!checked;
    }

    onSelectLatest() {
        const seq = this.state.move ? this.state.move.current_pack_seq : 0;
        this.state.details = this.state.details.map((d) => ({
            ...d,
            selected: !d.packed && d.pack_seq === seq,
        }));
    }

    onDeselectAll() {
        this.state.details = this.state.details.map((d) => ({ ...d, selected: false }));
    }

    onDeleteDetail(detail) {
        if (detail.packed) {
            return;
        }
        this.dialog.add(AlertDialog, {
            title: "Eliminar Linea",
            body: "Se eliminara la linea escaneada de " + (detail.product_name || "") + ". Desea continuar?",
            confirm: async () => {
                try {
                    const result = await this.orm.call(
                        "owner.move.stock",
                        "delete_scan_detail",
                        [detail.id]
                    );
                    if (result && result.error) {
                        this.state.message = result.error;
                        this.state.messageType = "danger";
                        return;
                    }
                    const rows = this.state.details.filter((d) => d.id !== detail.id);
                    this.state.details = rows;
                    this.state.summaryLines = this._buildSummary(rows);
                    this.state.message = "Linea eliminada.";
                    this.state.messageType = "success";
                } catch (error) {
                    this.state.message = "Error al eliminar la linea.";
                    this.state.messageType = "danger";
                }
            },
        });
    }

    async onChangeQty(detail, ev) {
        const value = parseFloat(ev.target.value);
        if (isNaN(value) || value < 0) {
            this.state.message = "Cantidad invalida.";
            this.state.messageType = "danger";
            return;
        }
        try {
            const result = await this.orm.call(
                "owner.move.stock",
                "set_manual_qty",
                [detail.id, value]
            );
            if (result && result.error) {
                this.state.message = result.error;
                this.state.messageType = "danger";
            } else {
                await this._loadDetails();
            }
        } catch (error) {
            this.state.message = "Error al actualizar la cantidad.";
            this.state.messageType = "danger";
        }
    }

    async onPack() {
        const selected = this.state.details.filter((d) => d.selected && !d.packed);
        if (selected.length === 0) {
            this.state.message = "Seleccione al menos una linea para empaquetar.";
            this.state.messageType = "warning";
            return;
        }
        try {
            const result = await this.orm.call(
                "owner.move.stock",
                "pack_selected_lines",
                [this.moveId, selected.map((d) => d.id)]
            );
            this.state.move = {
                ...this.state.move,
                current_pack_seq: (this.state.move.current_pack_seq || 0) + 1,
            };
            this.state.message = "Paquete creado: " + (result.package_name || "");
            this.state.messageType = "success";
            await this._loadDetails();
        } catch (error) {
            this._showServerError(error, "No se pudo empaquetar", "Error al empaquetar las lineas.");
        }
    }

    async onFinish() {
        try {
            const action = await this.orm.call(
                "owner.move.stock",
                "action_finish_scan",
                [this.moveId]
            );
            if (action && action.type) {
                this.actionService.doAction(action, { clear_breadcrumbs: true });
            } else {
                this._navigateBack();
            }
        } catch (error) {
            this._showServerError(error, "No se pudo finalizar", "Error al finalizar la operacion.");
        }
    }

    _showServerError(error, title, fallback) {
        const message = this._getErrorMessage(error, fallback);
        this.state.message = message;
        this.state.messageType = "danger";
        this.dialog.add(OwnerMoveErrorDialog, {
            title,
            errors: message.split("\n").map((line) => line.trim()).filter((line) => line),
        });
    }

    _getErrorMessage(error, fallback) {
        if (error && error.data && Array.isArray(error.data.arguments) && error.data.arguments.length) {
            return String(error.data.arguments[0]);
        }
        if (error && error.data && error.data.message) {
            return String(error.data.message).replace(/^[a-zA-Z0-9_.]+:\s*/, "");
        }
        return (error && error.message) || fallback;
    }

    onCancel() {
        this.dialog.add(AlertDialog, {
            title: "Cancelar Operacion",
            body: "La operacion pasara a estado Rechazado. Desea continuar?",
            confirm: async () => {
                try {
                    await this.orm.call(
                        "owner.move.stock",
                        "action_cancel_scan",
                        [this.moveId]
                    );
                    this.notification.add("Operacion cancelada.", {
                        type: "info",
                        title: "Cancelado",
                    });
                    this._navigateBack();
                } catch (error) {
                    this.notification.add("Error al cancelar la operacion.", {
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
                res_model: "owner.move.stock",
                res_id: this.moveId,
                views: [[false, "form"]],
                target: "current",
            }, { clear_breadcrumbs: true });
        }
    }
}

registry.category("actions").add(
    "custom_owner_stock_change.owner_move_scan_action",
    OwnerMoveScanAction
);

export { OwnerMoveScanAction };

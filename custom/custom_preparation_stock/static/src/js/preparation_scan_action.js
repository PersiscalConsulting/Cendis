/** @odoo-module */

import { registry } from "@web/core/registry";
import { Component, useState, useRef, onMounted, onWillStart, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

const STATE_LINE_LABELS = {
    pending: "Pendiente",
    in_progress: "En Proceso",
    done: "Completa",
    short: "Faltante",
};

class PreparationScanAction extends Component {
    static template = "custom_preparation_stock.PreparationScanAction";
    static props = {
        action: { type: Object, optional: true },
        actionId: { type: Number, optional: true },
        updateActionState: { type: Function, optional: true },
        className: { type: String, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");
        this.barcodeService = useService("barcode");
        this.dialog = useService("dialog");
        this.scanInputRef = useRef("scanInput");

        this.state = useState({
            exists: false,
            lines: [],
            selectedLineId: null,
            destLocation: "",
            currentLocation: "",
            ownerName: "",
            partnerName: "",
            prepName: "",
            isRework: false,
            version: 1,
            barcodeInput: "",
            message: "Escanee una ubicación para comenzar",
            messageType: "info",
            loading: true,
            submitting: false,
            isFinished: false,
            metaExpanded: false,
            manualQtyEnabled: false,
            allowDeleteEnabled: false,
            canEditDemand: false,
            flags: {},
        });

        this.stateLabels = STATE_LINE_LABELS;

        this.preparationId = this.props.action.context?.active_id
            || this.props.action.context?.default_preparation_id
            || this.props.action.params?.preparation_id;

        this._barcodeBuffer = "";
        this._lastBarcode = "";
        this._lastBarcodeTime = 0;
        this._processing = false;
        this._stateLoading = false;
        this._lastFingerprint = null;
        this._pollTimer = null;
        this._onKeyDown = this._onKeyDown.bind(this);
        this._onBarcodeScanned = this._onBarcodeScanned.bind(this);

        onWillStart(async () => {
            await this._loadState();
            this._startPolling();
            document.addEventListener("keydown", this._onKeyDown);
            if (this.barcodeService && this.barcodeService.bus) {
                this.barcodeService.bus.addEventListener(
                    "barcode_scanned",
                    this._onBarcodeScanned
                );
            }
        });

        onMounted(() => {
            this._focusInput();
        });

        onWillUnmount(() => {
            this._stopPolling();
            document.removeEventListener("keydown", this._onKeyDown);
            if (this.barcodeService && this.barcodeService.bus) {
                this.barcodeService.bus.removeEventListener(
                    "barcode_scanned",
                    this._onBarcodeScanned
                );
            }
        });
    }

    // CARGA / POLLING DE ESTADO (refleja ediciones del admin en vivo)
    // ==================================================================

    async _loadState(keepMessage = false) {
        if (this._stateLoading) {
            return;
        }
        if (!this.preparationId) {
            this.state.message = "No se especificó una preparación.";
            this.state.messageType = "danger";
            this.state.loading = false;
            return;
        }
        this._stateLoading = true;
        const previousMessage = this.state.message;
        const previousType = this.state.messageType;
        try {
            const data = await this.orm.call(
                "preparation.stock",
                "get_preparation_state",
                [this.preparationId]
            );
            if (!data || !data.exists) {
                this.state.exists = false;
                this.state.message = "Preparación no encontrada.";
                this.state.messageType = "danger";
                this.state.loading = false;
                return;
            }
            this.state.exists = true;
            this.state.prepName = data.name;
            this.state.ownerName = data.owner_name || "";
            this.state.partnerName = data.partner_name || "";
            this.state.destLocation = data.dest_location || "";
            this.state.currentLocation = data.current_location || "";
            this.state.isRework = !!data.is_rework;
            this.state.version = data.version || 1;
            this.state.lines = data.lines || [];
            this.state.flags = data.flags || {};
            this.state.manualQtyEnabled = !!(data.flags && data.flags.manual_qty);
            this.state.allowDeleteEnabled = !!(data.flags && data.flags.allow_delete);
            this.state.canEditDemand = !!(data.flags && data.flags.can_edit_demand);
            this._lastFingerprint = data.fingerprint;

            if (!["in_progress"].includes(data.state)) {
                this.state.isFinished = true;
                this.state.message = "";
                this.state.messageType = "info";
            } else {
                this.state.isFinished = false;
                if (!keepMessage) {
                    this.state.message = previousMessage;
                    this.state.messageType = previousType;
                }
            }

            if (!this.state.selectedLineId && this.state.lines.length > 0) {
                const active = this.state.lines.find((l) => l.state_line !== "done") ||
                    this.state.lines[0];
                this.state.selectedLineId = active.id;
            }
            // Si la linea seleccionada ya no existe (ej. eliminada por admin), reseleccionar
            if (this.state.selectedLineId &&
                !this.state.lines.some((l) => l.id === this.state.selectedLineId)) {
                this.state.selectedLineId = this.state.lines.length
                    ? this.state.lines[0].id : null;
            }
        } catch (error) {
            this.state.message = "Error al cargar la preparación.";
            this.state.messageType = "danger";
        } finally {
            this._stateLoading = false;
            this.state.loading = false;
        }
    }

    // POLLING LIGERO: solo trae un fingerprint; recarga completa si cambio
    // ==================================================================

    async _checkState() {
        try {
            const data = await this.orm.call(
                "preparation.stock",
                "get_preparation_state_light",
                [this.preparationId]
            );
            if (!data || !data.exists) {
                this.state.exists = false;
                this.state.message = "Preparación no encontrada.";
                this.state.messageType = "danger";
            } else if (data.fingerprint !== this._lastFingerprint) {
                await this._loadState(true);
            }
        } catch (error) {
            // Error transitorio del polling: se ignora y se reintenta.
        }
    }

    _startPolling() {
        this._pollTimer = setInterval(() => {
            if (this._processing || this.state.isFinished || !this.state.exists) {
                return;
            }
            this._checkState();
        }, 10000);
    }

    _stopPolling() {
        if (this._pollTimer) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
        }
    }

    get selectedLine() {
        return this.state.lines.find((l) => l.id === this.state.selectedLineId) || null;
    }

    get totalScanned() {
        return this.state.lines.reduce((acc, l) => acc + (l.qty_scanned || 0), 0);
    }

    get totalDemanded() {
        return this.state.lines.reduce((acc, l) => acc + (l.qty_demanded || 0), 0);
    }

    get totalUomName() {
        const names = new Set(
            this.state.lines.map((l) => l.uom_name).filter(Boolean)
        );
        return names.size === 1 ? this.state.lines[0].uom_name : "";
    }

    // LECTURA DE CODIGOS (servicio barcode + buffer de teclado)
    // ==================================================================

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

    _onBarcodeScanned(event) {
        if (this.state.isFinished) {
            return;
        }
        const barcode = event.detail?.barcode || event.detail || "";
        if (barcode) {
            this._processBarcode(barcode);
        }
    }

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
        try {
            const result = await this.orm.call(
                "preparation.stock",
                "process_barcode_scan",
                [this.preparationId, barcode, this.state.selectedLineId]
            );
            if (result) {
                if (result.error) {
                    this.state.message = result.error;
                    this.state.messageType = "danger";
                } else if (result.is_location) {
                    this.state.message = result.message;
                    this.state.messageType = "success";
                } else {
                    let msg = result.product_name + " - Cantidad: " + result.qty;
                    if (result.lot_name) {
                        msg += " | Lote: " + result.lot_name;
                    }
                    msg += " | Restante: " + result.remaining_qty;
                    this.state.message = msg;
                    this.state.messageType = "success";
                }
            }
            await this._loadState(!result?.error);
        } catch (error) {
            this.state.message = "Error al procesar el código de barras.";
            this.state.messageType = "danger";
        } finally {
            this._processing = false;
            this._focusInput();
        }
    }

    // ACCIONES SOBRE LINEAS ESCANEADAS
    // ==================================================================

    selectLine(lineId) {
        this.state.selectedLineId = lineId;
    }

    onLineCardKeydown(line, ev) {
        if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            this.selectLine(line.id);
        }
    }

    closeMessage() {
        this.state.message = "";
    }

    toggleMeta() {
        this.state.metaExpanded = !this.state.metaExpanded;
    }

    _parseNum(value) {
        if (typeof value === "number") {
            return value;
        }
        const num = parseFloat(String(value).trim().replace(",", "."));
        return isNaN(num) ? null : num;
    }

    _focusInput() {
        if (this.state.exists && !this.state.isFinished && this.scanInputRef.el) {
            window.setTimeout(() => this.scanInputRef.el.focus(), 0);
        }
    }

    async onScanQtyChange(scan, ev) {
        const value = this._parseNum(ev.target.value);
        if (value === null || value <= 0) {
            this.state.message = "Cantidad inválida.";
            this.state.messageType = "danger";
            await this._loadState(true);
            return;
        }
        try {
            const result = await this.orm.call(
                "preparation.stock",
                "set_scan_qty",
                [scan.id, value]
            );
            if (result && result.error) {
                this.state.message = result.error;
                this.state.messageType = "danger";
            } else {
                this.state.message = "Cantidad actualizada.";
                this.state.messageType = "success";
            }
        } catch (error) {
            this._showServerError(error, "No se pudo actualizar", "Error al actualizar la cantidad.");
        }
        await this._loadState(true);
    }

    onDeleteScan(scan) {
        if (!this.state.allowDeleteEnabled) {
            return;
        }
        const line = this.selectedLine;
        const label = line ? line.product_name : "";
        this.dialog.add(AlertDialog, {
            title: "Eliminar Línea Escaneada",
            body: "Se eliminará el escaneo de " + label +
                  " (" + scan.qty + ") en " + scan.location_name + ". ¿Desea continuar?",
            confirm: async () => {
                try {
                    const result = await this.orm.call(
                        "preparation.stock",
                        "delete_scan_line",
                        [scan.id]
                    );
                    if (result && result.error) {
                        this.state.message = result.error;
                        this.state.messageType = "danger";
                        return;
                    }
                    this.state.message = "Línea escaneada eliminada.";
                    this.state.messageType = "success";
                } catch (error) {
                    this.state.message = "Error al eliminar la línea.";
                    this.state.messageType = "danger";
                }
                await this._loadState(true);
            },
        });
    }

    // FINALIZAR / CANCELAR / NAVEGACION
    // ==================================================================

    async onFinish() {
        if (this.state.submitting) {
            return;
        }
        this.state.submitting = true;
        try {
            const check = await this.orm.call(
                "preparation.stock",
                "check_validation",
                [this.preparationId]
            );
            if (check && check.error) {
                this.state.message = check.error;
                this.state.messageType = "danger";
                return;
            }
            if (check && check.warnings && check.warnings.length > 0) {
                this.dialog.add(AlertDialog, {
                    title: "Advertencia de Validación",
                    body: check.warnings.join("\n") +
                          "\n\n¿Desea continuar con la validación?",
                    confirm: async () => {
                        this.state.submitting = true;
                        this.forceUpdate();
                        await this._executeValidation(true);
                    },
                });
            } else {
                await this._executeValidation(false);
            }
        } catch (error) {
            this._showServerError(error, "Error", "Error al validar.");
        } finally {
            this.state.submitting = false;
        }
    }

    async _executeValidation(force) {
        this.state.submitting = true;
        try {
            const action = await this.orm.call(
                "preparation.stock",
                "action_mark_to_validate",
                [this.preparationId],
            );
            if (action && action.type) {
                if (action.res_model !== "preparation.rework.excess.wizard") {
                    this.notification.add("Preparación marcada para validación.", {
                        type: "success",
                        title: "Para Validar",
                    });
                }
                this.actionService.doAction(action, { clear_breadcrumbs: true });
            } else {
                this._navigateBack();
            }
        } catch (error) {
            this._showServerError(error, "No se pudo marcar", "Error al marcar la preparación.");
        } finally {
            this.state.submitting = false;
        }
    }

    onCancel() {
        this.dialog.add(AlertDialog, {
            title: "Cancelar Preparación",
            body: "La preparación pasará a estado Cancelado. Esta acción no se puede deshacer. ¿Desea continuar?",
            confirm: async () => {
                try {
                    await this.orm.call(
                        "preparation.stock",
                        "action_cancel",
                        [this.preparationId]
                    );
                    this.notification.add("Preparación cancelada.", {
                        type: "info",
                        title: "Cancelado",
                    });
                    this._navigateBack();
                } catch (error) {
                    this.notification.add("Error al cancelar la preparación.", {
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
                res_model: "preparation.stock",
                res_id: this.preparationId,
                views: [[false, "form"]],
                target: "current",
            }, { clear_breadcrumbs: true });
        }
    }

    _showServerError(error, title, fallback) {
        const message = this._getErrorMessage(error, fallback);
        this.state.message = message;
        this.state.messageType = "danger";
        this.dialog.add(AlertDialog, {
            title,
            body: message,
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
}

registry.category("actions").add(
    "custom_preparation_stock.preparation_scan_action",
    PreparationScanAction
);

export { PreparationScanAction };

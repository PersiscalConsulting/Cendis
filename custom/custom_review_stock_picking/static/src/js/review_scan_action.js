/** @odoo-module */

import { registry } from "@web/core/registry";
import { Component, useState, useRef, onMounted, onWillStart, onWillUnmount } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { AlertDialog, ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

const STATE_LINE_LABELS = {
    pending: "Pendiente",
    done: "Repasado",
};

const POLL_INTERVAL = 4000;
const BARCODE_DEBOUNCE_MS = 500;

class ReviewScanAction extends Component {
    static template = "custom_review_stock_picking.ReviewScanAction";
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
            loading: true,
            submitting: false,
            isFinished: false,
            metaExpanded: false,
            name: "",
            state: "",
            preparationName: "",
            ownerName: "",
            partnerName: "",
            currentLocation: "",
            separationLocation: "",
            dateStart: null,
            dateEnd: null,
            packageCount: 0,
            flags: {},
            canManualReview: false,
            moves: [],
            packages: [],
            selectedSidebarItem: "unpacked",
            selectedMoveId: null,
            expandedMoves: {},
            expandedLines: {},
            barcodeInput: "",
            message: "Escanee la ubicación de separación para comenzar",
            messageType: "info",
            showPackageWizard: false,
            packageWizardLines: [],
            packageWizardWeight: 0,
            packageWizardTypeInput: 0,
            packageWizardTarget: "new",
            packageWizardTargetId: null,
            packageTypes: [],
            lastScannedMoveId: null,
            locationScanned: false,
            showVerifyModal: false,
            verifyResult: null,
            version: null,
        });

        this.stateLabels = STATE_LINE_LABELS;

        this.reviewId = this.props.action.context?.active_id
            || this.props.action.context?.default_review_id
            || this.props.action.params?.review_id;

        this._barcodeBuffer = "";
        this._lastBarcode = "";
        this._lastBarcodeTime = 0;
        this._processing = false;
        this._pollTimer = null;
        this._onKeyDown = this._onKeyDown.bind(this);
        this._onBarcodeScanned = this._onBarcodeScanned.bind(this);

        onWillStart(async () => {
            await this._loadState();
            await this._loadPackageTypes();
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

    // ==================================================================
    // CARGA / POLLING DE ESTADO
    // ==================================================================

    async _loadState(keepMessage = false) {
        if (!this.reviewId) {
            this.state.message = "No se especifico un repaso.";
            this.state.messageType = "danger";
            this.state.loading = false;
            return;
        }
        const previousMessage = this.state.message;
        const previousType = this.state.messageType;
        try {
            const data = await this.orm.call(
                "review.stock.picking",
                "get_review_state",
                [this.reviewId]
            );
            if (!data || !data.exists) {
                this.state.exists = false;
                this.state.message = "Repaso no encontrado.";
                this.state.messageType = "danger";
                this.state.loading = false;
                return;
            }
            this.state.exists = true;
            this.state.name = data.name;
            this.state.state = data.state;
            this.state.preparationName = data.preparation_name;
            this.state.ownerName = data.owner_name;
            this.state.partnerName = data.partner_name;
            this.state.currentLocation = data.current_location || "";
            this.state.separationLocation = data.separation_location || "";
            this.state.locationScanned = Boolean(data.location_scanned);
            this.state.dateStart = data.date_start;
            this.state.dateEnd = data.date_end;
            this.state.packageCount = data.package_count;
            this.state.flags = data.flags || {};
            this.state.canManualReview = data.can_manual_review;
            this.state.moves = data.moves || [];
            this.state.packages = data.packages || [];
            this._refreshVersion();

            if (
                ["done", "cancelled"].includes(data.state) ||
                Boolean(data.date_end)
            ) {
                this.state.isFinished = true;
                this.state.message = "";
                this.state.messageType = "info";
            } else {
                this.state.isFinished = false;
                if (!keepMessage) {
                    this.state.message = this.state.locationScanned
                        ? ""
                        : previousMessage;
                    this.state.messageType = this.state.locationScanned
                        ? "info"
                        : previousType;
                }
            }

            this._validateSelections();
        } catch (error) {
            this.state.message = "Error al cargar el repaso.";
            this.state.messageType = "danger";
        } finally {
            this.state.loading = false;
        }
    }

    _validateSelections() {
        if (this.state.selectedSidebarItem
                && this.state.selectedSidebarItem !== "unpacked") {
            const pkgExists = this.state.packages.some(
                (p) => p.id === this.state.selectedSidebarItem
            );
            if (!pkgExists) {
                this.state.selectedSidebarItem = "unpacked";
            }
        }

        if (this.state.selectedMoveId) {
            const moveExists = this.state.moves.some(
                (m) => m.id === this.state.selectedMoveId
            );
            if (!moveExists) {
                this.state.selectedMoveId = null;
            }
        }

        if (this.state.lastScannedMoveId) {
            const moveExists = this.state.moves.some(
                (m) => m.id === this.state.lastScannedMoveId
            );
            if (moveExists) {
                this.state.selectedSidebarItem = "unpacked";
                this.state.selectedMoveId = this.state.lastScannedMoveId;
                this.state.expandedMoves[this.state.lastScannedMoveId] = true;
            }
            this.state.lastScannedMoveId = null;
        }
    }

    async _loadPackageTypes() {
        try {
            const types = await this.orm.call(
                "review.stock.picking",
                "get_package_types",
                []
            );
            this.state.packageTypes = types || [];
        } catch (error) {
            // silently fail - not critical
        }
    }

    // ==================================================================
    // POLLING POR VERSION (solo consulta pesada si hubo cambios)
    // ==================================================================

    async _refreshVersion() {
        try {
            const v = await this.orm.call(
                "review.stock.picking",
                "get_review_version",
                [this.reviewId]
            );
            this.state.version = v;
        } catch (e) {
            /* silencioso: se reintenta en el proximo tick */
        }
    }

    _startPolling() {
        this._pollTimer = setInterval(async () => {
            if (this._processing || this.state.isFinished || !this.state.exists) {
                return;
            }
            if (document.hidden) {
                return;
            }
            try {
                const v = await this.orm.call(
                    "review.stock.picking",
                    "get_review_version",
                    [this.reviewId]
                );
                if (v !== this.state.version) {
                    await this._loadState(true);
                }
            } catch (e) {
                /* silencioso */
            }
        }, POLL_INTERVAL);

        this._onVisibility = () => {
            if (!document.hidden && this.state.exists && !this.state.isFinished) {
                this._loadState(true);
            }
        };
        document.addEventListener("visibilitychange", this._onVisibility);
    }

    _stopPolling() {
        if (this._pollTimer) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
        }
        if (this._onVisibility) {
            document.removeEventListener("visibilitychange", this._onVisibility);
            this._onVisibility = null;
        }
    }

    // ==================================================================
    // GETTERS COMPUTADOS
    // ==================================================================

    get unpackedMoves() {
        return this.state.moves;
    }

    get selectedPackage() {
        if (this.state.selectedSidebarItem === "unpacked") {
            return null;
        }
        return this.state.packages.find(
            (p) => p.id === this.state.selectedSidebarItem
        ) || null;
    }

    get selectedPackageLines() {
        if (!this.selectedPackage) {
            return [];
        }
        const pkgId = this.selectedPackage.id;
        const lines = [];
        for (const move of this.state.moves) {
            for (const ml of move.move_lines) {
                if (ml.package_id === pkgId) {
                    lines.push({
                        ...ml,
                        product_name: move.product_name,
                    });
                }
            }
        }
        return lines;
    }

    get hasUnpackagedMoves() {
        return this.packableLines.length > 0;
    }

    get movesWithUnpackagedLines() {
        return this.state.moves.filter(
            (m) => this.moveHasUnpackagedReviewed(m)
        );
    }

    moveHasUnpackagedReviewed(move) {
        return (move.move_lines || []).some(
            (ml) => !ml.package_id && ml.cant_repasada > 0.0001
        );
    }

    get packableLines() {
        const lines = [];
        for (const move of this.state.moves) {
            for (const ml of move.move_lines || []) {
                if (!ml.package_id && ml.cant_repasada > 0.0001) {
                    lines.push({
                        ...ml,
                        product_name: move.product_name,
                    });
                }
            }
        }
        return lines;
    }

    // ==================================================================
    // LECTURA DE CODIGOS
    // ==================================================================

    _onKeyDown(ev) {
        if (ev.key === "Escape") {
            if (this.state.showVerifyModal) {
                this.onCloseVerify();
                return;
            }
            if (this.state.showPackageWizard) {
                this.onCancelPackageWizard();
                return;
            }
        }
        if (ev.key === "Tab" && (this.state.showVerifyModal || this.state.showPackageWizard)) {
            this._trapModalFocus(ev);
            return;
        }
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

    _getOpenModal() {
        if (!this.el) {
            return null;
        }
        return this.el.querySelector(".o_review_modal_overlay");
    }

    _trapModalFocus(ev) {
        const overlay = this._getOpenModal();
        if (!overlay) {
            return;
        }
        const focusables = overlay.querySelectorAll(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        if (!focusables.length) {
            ev.preventDefault();
            overlay.querySelector(".o_review_modal")?.focus?.();
            return;
        }
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const active = document.activeElement;
        if (ev.shiftKey && (active === first || !overlay.contains(active))) {
            ev.preventDefault();
            last.focus();
        } else if (!ev.shiftKey && (active === last || !overlay.contains(active))) {
            ev.preventDefault();
            first.focus();
        }
    }

    _focusModal() {
        const focus = () => {
            const overlay = this._getOpenModal();
            if (!overlay) {
                return;
            }
            const focusable = overlay.querySelector(
                'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
            );
            if (focusable) {
                focusable.focus();
            }
        };
        requestAnimationFrame(() => requestAnimationFrame(focus));
    }

    _restoreFocus() {
        if (this._lastFocused && document.contains(this._lastFocused)) {
            this._lastFocused.focus();
        }
        this._lastFocused = null;
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
        if (barcode === this._lastBarcode && now - this._lastBarcodeTime < BARCODE_DEBOUNCE_MS) {
            return;
        }
        this._lastBarcode = barcode;
        this._lastBarcodeTime = now;
        this._processing = true;
        try {
            const result = await this.orm.call(
                "review.stock.picking",
                "process_review_barcode",
                [this.reviewId, barcode]
            );
            if (result) {
                if (result.error) {
                    this.state.message = result.error;
                    this.state.messageType = "danger";
                } else if (result.is_location) {
                    this.state.locationScanned = true;
                    this.state.message = result.message;
                    this.state.messageType = "success";
                } else {
                    if (result.move_id) {
                        this.state.lastScannedMoveId = result.move_id;
                    }
                    if (result.warning) {
                        this.state.message = result.warning;
                        this.state.messageType = "warning";
                    } else {
                        let msg = result.product_name + " - Repasado: " + result.qty_reviewed;
                        if (result.lot_name) {
                            msg += " | Lote: " + result.lot_name;
                        }
                        if (result.remaining_qty !== undefined) {
                            msg += " | Restante: " + result.remaining_qty;
                        }
                        this.state.message = msg;
                        this.state.messageType = "success";
                    }
                }
            }
            await this._loadState(true);
        } catch (error) {
            this.state.message = "Error al procesar el código de barras.";
            this.state.messageType = "danger";
        } finally {
            this._processing = false;
            this._focusInput();
        }
    }

    // ==================================================================
    // ACCIONES DE SIDEBAR
    // ==================================================================

    selectSidebarItem(itemId) {
        this.state.selectedSidebarItem = itemId;
        this.state.selectedMoveId = null;
    }

    selectMove(moveId) {
        this.state.selectedMoveId = moveId;
        if (this.state.selectedSidebarItem === "unpacked" && !this.state.expandedMoves[moveId]) {
            this.state.expandedMoves[moveId] = true;
        }
    }

    toggleExpandMove(moveId, ev) {
        if (ev) ev.stopPropagation();
        this.state.expandedMoves[moveId] = !this.state.expandedMoves[moveId];
    }

    toggleExpandLine(moveId) {
        this.state.expandedLines[moveId] = !this.state.expandedLines[moveId];
    }

    closeMessage() {
        this.state.message = "";
    }

    onSidebarKeydown(itemId, ev) {
        if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            this.selectSidebarItem(itemId);
        }
    }

    onMoveKeydown(move, ev) {
        if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            this.selectMove(move.id);
        }
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

    // ==================================================================
    // ACCIONES PRINCIPALES
    // ==================================================================

    async onStart() {
        try {
            await this.orm.call(
                "review.stock.picking",
                "action_start",
                [this.reviewId]
            );
            this.notification.add("Repaso iniciado.", {
                type: "success",
                title: "Inicio",
            });
            await this._loadState();
        } catch (error) {
            this._showServerError(error, "Error", "No se pudo iniciar el repaso.");
        }
    }

    async onFinish() {
        if (this.state.isFinished) {
            return;
        }
        if (this.state.submitting) {
            return;
        }
        this.state.submitting = true;
        try {
            const check = await this.orm.call(
                "review.stock.picking",
                "check_before_finish",
                [this.reviewId]
            );
            if (check && check.error) {
                this.state.message = check.error;
                this.state.messageType = "danger";
                return;
            }
            if (check.blocked) {
                this.state.message = "El repaso tiene problemas que impiden finalizar.";
                this.state.messageType = "danger";
                this.dialog.add(AlertDialog, {
                    title: "No se puede finalizar",
                    body: (check.blocked_issues || []).join("\n"),
                });
                return;
            }
            const warnings = check.warnings || [];
            let body =
                "Se registrará la fecha fin de la preparación y volverá al " +
                "formulario. El repaso quedará pendiente de Validar para " +
                "pasarlo a Finalizado.\n\n¿Desea continuar?";
            if (warnings.length > 0) {
                body = warnings.join("\n") + "\n\n" + body;
            }
            this.dialog.add(ConfirmationDialog, {
                title: "Finalizar repaso",
                body: body,
                confirm: async () => {
                    this.state.submitting = true;
                    await this._executeFinish();
                },
                cancel: () => {
                    this.state.submitting = false;
                },
            });
        } catch (error) {
            this._showServerError(error, "Error", "No se pudo finalizar el repaso.");
        } finally {
            this.state.submitting = false;
        }
    }

    async _executeFinish() {
        this.state.submitting = true;
        try {
            await this.orm.call(
                "review.stock.picking",
                "action_end_preparation",
                [this.reviewId]
            );
            this.notification.add("Fecha fin registrada.", {
                type: "success",
                title: "Finalizado",
            });
            this.actionService.doAction(
                {
                    type: "ir.actions.act_window",
                    res_model: "review.stock.picking",
                    res_id: this.reviewId,
                    views: [[false, "form"]],
                    target: "main",
                },
                { clear_breadcrumbs: true }
            );
        } catch (error) {
            this._showServerError(error, "Error", "No se pudo finalizar el repaso.");
        } finally {
            this.state.submitting = false;
        }
    }

    onCancel() {
        if (this.state.isFinished) {
            return;
        }
        this.dialog.add(AlertDialog, {
            title: "Cancelar Repaso",
            body: "El repaso pasará a estado Cancelado. Esta acción no se puede deshacer. ¿Desea continuar?",
            confirm: async () => {
                try {
                    await this.orm.call(
                        "review.stock.picking",
                        "action_cancel",
                        [this.reviewId]
                    );
                    this.notification.add("Repaso cancelado.", {
                        type: "info",
                        title: "Cancelado",
                    });
                    this._navigateBack();
                } catch (error) {
                    this._showServerError(error, "Error", "No se pudo cancelar el repaso.");
                }
            },
        });
    }

    async onVerify() {
        if (this.state.submitting || !this.state.locationScanned) {
            return;
        }
        this.state.submitting = true;
        try {
            const result = await this.orm.call(
                "review.stock.picking",
                "action_verify",
                [this.reviewId]
            );
            if (result) {
                this.state.verifyResult = result;
                this._lastFocused = document.activeElement;
                this.state.showVerifyModal = true;
                this._focusModal();
            }
        } catch (error) {
            this._showServerError(error, "Error", "No se pudo verificar el repaso.");
        } finally {
            this.state.submitting = false;
        }
    }

    onCloseVerify() {
        this.state.showVerifyModal = false;
        this.state.verifyResult = null;
        this._restoreFocus();
    }

    get verifyOk() {
        return this.state.verifyResult?.ok ?? false;
    }

    get verifyIssues() {
        return this.state.verifyResult?.issues ?? [];
    }

    get verifyWarnings() {
        return this.state.verifyResult?.warnings ?? [];
    }

    get verifySummaryLines() {
        if (!this.state.verifyResult) {
            return [];
        }
        return this.state.verifyResult.qty_comparison || [];
    }

    get verifyPackages() {
        if (!this.state.verifyResult) {
            return [];
        }
        return this.state.verifyResult.packages_summary || [];
    }

    get verifyUnpacked() {
        if (!this.state.verifyResult) {
            return [];
        }
        return this.state.verifyResult.unpacked_products || [];
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
                res_model: "review.stock.picking",
                res_id: this.reviewId,
                views: [[false, "form"]],
                target: "current",
            }, { clear_breadcrumbs: true });
        }
    }

    // ==================================================================
    // PAQUETIZACION
    // ==================================================================

    onOpenPackageWizard() {
        if (this.state.isFinished) {
            return;
        }
        const selectedLines = this.packableLines;
        if (selectedLines.length === 0) {
            this.state.message = "No hay líneas repasadas sin paquetizar.";
            this.state.messageType = "warning";
            return;
        }
        this.state.packageWizardLines = selectedLines.map((ml) => ({
            ...ml,
            selected: false,
        }));
        this.state.packageWizardWeight = 0;
        this.state.packageWizardTypeInput = "";
        this.state.packageWizardTarget = "new";
        this.state.packageWizardTargetId = null;
        this._lastFocused = document.activeElement;
        this.state.showPackageWizard = true;
        this._focusModal();
    }

    onPackageWizardTargetChange(target) {
        this.state.packageWizardTarget = target;
        if (target === "new") {
            this.state.packageWizardTargetId = null;
        }
    }

    onPackageWizardTargetPackageChange(pkgId) {
        this.state.packageWizardTargetId = pkgId || null;
    }

    onPackageWizardSelectPackage(ev) {
        const pkgId = parseInt(ev.target.value) || 0;
        this.onPackageWizardTargetPackageChange(pkgId);
    }

    get packageWizardTargetPackages() {
        return this.state.packages;
    }

    onPackageWizardToggleLine(mlId) {
        const line = this.state.packageWizardLines.find((l) => l.id === mlId);
        if (line) {
            line.selected = !line.selected;
        }
    }

    onPackageWizardToggleAll() {
        const allSelected = this.state.packageWizardLines.every((l) => l.selected);
        for (const line of this.state.packageWizardLines) {
            line.selected = !allSelected;
        }
    }

    onPackageWizardWeightChange(ev) {
        const val = this._parseNum(ev.target.value);
        this.state.packageWizardWeight = val === null ? 0 : val;
    }

    onPackageWizardTypeChange(ev) {
        this.state.packageWizardTypeInput = parseInt(ev.target.value) || 0;
    }

    get packageWizardSelectedIds() {
        return this.state.packageWizardLines
            .filter((l) => l.selected)
            .map((l) => l.id);
    }

    get packageWizardSelectedCount() {
        return this.packageWizardSelectedIds.length;
    }

    get packageWizardSummary() {
        const groups = {};
        for (const ml of this.state.packageWizardLines) {
            const key = ml.product_name;
            if (!groups[key]) {
                groups[key] = { product_name: key, total_qty: 0, line_count: 0 };
            }
            groups[key].total_qty += ml.cant_repasada || 0;
            groups[key].line_count += 1;
        }
        return Object.values(groups);
    }

    async onConfirmPackage() {
        if (this.state.isFinished) {
            return;
        }
        if (this.state.submitting) {
            return;
        }
        const ids = this.packageWizardSelectedIds;
        if (ids.length === 0) {
            this.state.message = "Seleccione al menos una línea.";
            this.state.messageType = "warning";
            return;
        }
        this.state.submitting = true;
        try {
            let result;
            if (this.state.packageWizardTarget === "existing") {
                const targetId = this.state.packageWizardTargetId;
                if (!targetId) {
                    this.state.message = "Seleccione el paquete de destino.";
                    this.state.messageType = "warning";
                    return;
                }
                result = await this.orm.call(
                    "review.stock.picking",
                    "action_add_to_package",
                    [this.reviewId, targetId, ids]
                );
            } else {
                result = await this.orm.call(
                    "review.stock.picking",
                    "action_create_package",
                    [this.reviewId, ids],
                    {
                        package_type_id: this.state.packageWizardTypeInput
                            ? parseInt(this.state.packageWizardTypeInput) || false
                            : false,
                        shipping_weight: this.state.packageWizardWeight,
                    }
                );
            }
            if (result && result.error) {
                this.state.message = result.error;
                this.state.messageType = "danger";
                return;
            }
            this.state.showPackageWizard = false;
            this._restoreFocus();
            this.state.message = "Paquete actualizado: " + (result.package?.name || "");
            this.state.messageType = "success";
            await this._loadState(true);
        } catch (error) {
            this._showServerError(error, "Error", "No se pudo actualizar el paquete.");
        } finally {
            this.state.submitting = false;
        }
    }

    onCancelPackageWizard() {
        this.state.showPackageWizard = false;
        this._restoreFocus();
    }

    // ==================================================================
    // CONTEO MANUAL
    // ==================================================================

    async onManualIncrement(mlId) {
        return await this._setManualCount(mlId, null, true);
    }

    async onManualCountChange(mlId, ev) {
        const val = ev.target.value;
        if (val === "") {
            return;
        }
        return await this._setManualCount(mlId, val, false);
    }

    async _setManualCount(mlId, value, increment) {
        if (this.state.isFinished) {
            return false;
        }
        let qty;
        const line = this._findLine(mlId);
        const current = line ? line.cant_repasada : 0;
        if (increment) {
            qty = current + 1;
        } else {
            qty = this._parseNum(value) || 0;
        }
        try {
            const result = await this.orm.call(
                "review.stock.picking",
                "set_manual_count",
                [this.reviewId, mlId],
                { qty_reviewed: qty }
            );
            if (result && result.error) {
                this.state.message = result.error;
                this.state.messageType = "danger";
                return false;
            }
            await this._loadState(true);
            return true;
        } catch (error) {
            this._showServerError(error, "Error", "No se pudo registrar el conteo.");
            return false;
        }
    }

    _findLine(mlId) {
        for (const move of this.state.moves) {
            for (const ml of move.move_lines || []) {
                if (ml.id === mlId) {
                    return ml;
                }
            }
        }
        return null;
    }

    // ==================================================================
    // PAQUETE SELECCIONADO - EDICION
    // ==================================================================

    async onPackageWeightChange(ev) {
        if (this.state.isFinished) {
            return;
        }
        if (!this.selectedPackage) {
            return;
        }
        const val = this._parseNum(ev.target.value);
        const weight = val === null ? 0 : val;
        try {
            const result = await this.orm.call(
                "review.stock.picking",
                "action_update_package",
                [this.reviewId, this.selectedPackage.id],
                { shipping_weight: weight }
            );
            if (result && result.error) {
                this.state.message = result.error;
                this.state.messageType = "danger";
                return;
            }
            const pkg = this.state.packages.find(
                (p) => p.id === this.selectedPackage.id
            );
            if (pkg) {
                pkg.shipping_weight = weight;
            }
        } catch (error) {
            this._showServerError(error, "Error", "No se pudo actualizar el paquete.");
        }
    }

    async onPackageTypeChange(ev) {
        if (this.state.isFinished) {
            return;
        }
        if (!this.selectedPackage) {
            return;
        }
        const typeId = parseInt(ev.target.value) || false;
        try {
            const result = await this.orm.call(
                "review.stock.picking",
                "action_update_package",
                [this.reviewId, this.selectedPackage.id],
                { package_type_id: typeId }
            );
            if (result && result.error) {
                this.state.message = result.error;
                this.state.messageType = "danger";
                return;
            }
            await this._loadState(true);
        } catch (error) {
            this._showServerError(error, "Error", "No se pudo actualizar el paquete.");
        }
    }

    async onUnpackageLines() {
        if (this.state.isFinished) {
            return;
        }
        if (!this.selectedPackage) {
            return;
        }
        if (!this.state.flags?.allow_unpackaging) {
            this.state.message = "El desempaquetado no está habilitado para este propietario.";
            this.state.messageType = "warning";
            return;
        }
        const ids = this.selectedPackageLines.map((l) => l.id);
        if (ids.length === 0) {
            return;
        }
        this.dialog.add(AlertDialog, {
            title: "Desempaquetar líneas",
            body: "Se devolverán " + ids.length +
                  " línea(s) a sin paquetizar. ¿Desea continuar?",
            confirm: async () => {
                try {
                    const result = await this.orm.call(
                        "review.stock.picking",
                        "action_unpackage_lines",
                        [this.reviewId, ids]
                    );
                    if (result && result.error) {
                        this.state.message = result.error;
                        this.state.messageType = "danger";
                        return;
                    }
                    this.state.selectedSidebarItem = "unpacked";
                    this.state.message = "Líneas devueltas a sin paquetizar.";
                    this.state.messageType = "success";
                    await this._loadState(true);
                } catch (error) {
                    this._showServerError(error, "Error", "No se pudo desempaquetar.");
                }
            },
        });
    }

    async onRemoveLineFromPackage(mlId) {
        if (this.state.isFinished) {
            return;
        }
        if (!this.state.flags?.allow_unpackaging) {
            this.state.message = "El desempaquetado no está habilitado para este propietario.";
            this.state.messageType = "warning";
            return;
        }
        try {
            const result = await this.orm.call(
                "review.stock.picking",
                "action_remove_line_from_package",
                [this.reviewId, mlId]
            );
            if (result && result.error) {
                this.state.message = result.error;
                this.state.messageType = "danger";
                return;
            }
            this.state.message = "Línea removida del paquete.";
            this.state.messageType = "success";
            await this._loadState(true);
        } catch (error) {
            this._showServerError(error, "Error", "No se pudo remover la línea.");
        }
    }

    async onDeleteReviewLine(mlId) {
        if (this.state.isFinished) {
            return;
        }
        try {
            const result = await this.orm.call(
                "review.stock.picking",
                "action_delete_review_line",
                [this.reviewId, mlId]
            );
            if (result && result.error) {
                this.state.message = result.error;
                this.state.messageType = "danger";
                return;
            }
            this.state.message = "Línea eliminada.";
            this.state.messageType = "success";
            await this._loadState(true);
        } catch (error) {
            this._showServerError(error, "Error", "No se pudo eliminar la línea.");
        }
    }

    // ==================================================================
    // MANEJO DE ERRORES
    // ==================================================================

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
        if (error?.data?.arguments?.length) {
            return String(error.data.arguments[0]);
        }
        if (error?.data?.message) {
            return String(error.data.message).replace(/^[a-zA-Z0-9_.]+:\s*/, "");
        }
        return error?.message || fallback;
    }
}

registry.category("actions").add(
    "custom_review_stock_picking.review_scan_action",
    ReviewScanAction
);

export { ReviewScanAction };

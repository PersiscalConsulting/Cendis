# Plan de implementación — `review.stock.picking` (Odoo 18)

Este documento es la guía de referencia para construir el módulo de repaso optimizado.
Está pensado para ser leído por OpenCode antes de tocar código, y para acompañarlo como
`PLAN.md` en la raíz del módulo durante toda la implementación.

---

## 1. Resumen funcional

Al validarse un `stock.picking` de preparación nace un `review.stock.picking` (relación **1:1**)
que replica su jerarquía de líneas. El operario repasa físicamente lo separado (primero debe
escanear la **ubicación de separación**: hasta entonces los productos permanecen ocultos en la
vista OWL), lo agrupa en paquetes (`stock.quant.package`) y al finalizar el repaso el stock queda
empacado **en sitio** (ubicación de origen = destino = Separación, sin traslado). El traslado a la
ubicación de salida y la generación del `stock.picking` de salida (Expedición → Clientes) ocurren
recién en la server action **"INVENTARIO REPASO: generar picking de salida"**, que es la que
ejecuta el botón del wizard "Generar expedición".

Flujo de estados:

```
DISPONIBLE  →  EN PROCESO  →  FINALIZADO
                   ↓
               CANCELADO
```

Finalización en dos pasos:

1. **Finalizar** (OWL, con modal de confirmación) → registra `date_end` (fin de la preparación),
   devuelve al formulario y deja el repaso en EN PROCESO. El escáner queda bloqueado a partir de
   ese momento (no se puede reabrir).
2. **Validar** (botón del formulario, con `confirm`) → ejecuta la server action
   **"INVENTARIO REPASO: finalizar repaso"** (validación + cambios de stock) y recién ahí pasa a
   FINALIZADO, preservando el `date_end` ya registrado.

---

## 2. Decisiones de diseño ya cerradas

Estas decisiones **no deben ser reinterpretadas ni "mejoradas" por el agente** — son defaults
explícitos para la v1:

| # | Tema | Decisión |
|---|---|---|
| 1 | Botón "Verificar" | Es **informativo**, no bloquea "Finalizar". Queda abierto para el futuro agregar un modo bloqueante configurable, pero **no implementar eso en v1**. |
| 2 | Paquetes | Se usa `stock.quant.package` real (no un modelo borrador propio). **Por default**, el paquete se crea vacío/sin impacto en stock al confirmarse el wizard de "Paquetizar", y **recién en "Finalizar" se generan los quants reales** para todos los paquetes del repaso. La lógica de "asignar quants a un paquete" debe aislarse en un método propio (ver sección 6) para poder, en una fase futura, llamarlo paquete por paquete en vez de una sola vez al Finalizar — pero **esa fase futura no se implementa ahora**. |
| 3 | Paquetes huérfanos | Si se cancela un repaso, los `stock.quant.package` ya creados (vacíos) **quedan huérfanos sin limpieza automática**. No implementar borrado ni reasignación. |
| 4 | Tipo de picking generado | Un único `picking_type_id` fijo, definido en configuración. No hay reglas condicionales por ahora. |
| 5 | Cardinalidad preparación↔repaso | Estrictamente **1:1**. No contemplar reintentos ni múltiples repasos por preparación en v1. |

Puntos que siguen **abiertos** y habrá que resolver cuando se llegue a esa tarea (no bloquean el arranque):
- Tolerancia de diferencias permitida en "Verificar" (¿0 por defecto o configurable?).
- Si se puede Finalizar con productos aún "sin paquetizar" restantes, o eso debe impedirse.

---

## 3. Modelos de datos

| Modelo | Rol |
|---|---|
| `review.stock.picking` | Cabecera del repaso. 1:1 con el `stock.picking` de preparación origen. |
| `review.move` | Línea agrupada por producto (espejo de `stock.move`), con las 3 cantidades: definida en preparación, paquetizada, restante por repasar. |
| `review.move.line` | Detalle por lote/owner/cantidad (espejo de `stock.move.line`), con lote, fecha de vencimiento, cantidad. |
| `review.stage` | Etapas configurables (DISPONIBLE / EN PROCESO / FINALIZADO / CANCELADO) como registros propios, no un `selection` fijo. |
| `review.stock.config` | Modelo de configuración: toggles de ciego/visual, repaso manual, lotes repetidos, `picking_type_id` a generar, validaciones parametrizables. |
| `review.package.wizard` (`TransientModel`) | Wizard de confirmación al presionar "Paquetizar". |

**Nota sobre `stage_id` vs `state`:** usar un `Many2one` a `review.stage` (no un `selection`
hardcodeado), con flags booleanos en `review.stage` (`is_initial`, `is_final`, `is_cancel`) para
que la lógica de transición no dependa de comparar strings. Si se necesita compatibilidad con el
widget `statusbar` nativo de Odoo, agregar un campo `state` técnico calculado (`compute`) a partir
de esos flags — el widget espera `selection`, no `Many2one`.

**Nota sobre paquetes:** no crear un modelo intermedio de "paquete borrador". El
`stock.quant.package` se crea real desde el wizard, pero permanece sin quants asociados
(vacío de contenido real) hasta Finalizar, según la decisión #2.

---

## 4. Vista OWL — componentes

Referencia obligatoria: los archivos JS/XML de `preparation.stock` (indicar a OpenCode las
rutas concretas antes de esta tarea; mismo patrón de listener de barcode, misma estructura de
componentes).

- **Header**: datos del repaso + botones "+Escanear" / "Finalizar" / "Cancelar" / "Verificar".
- **ScanBar**: input manual + escaneo.
- **Sidebar**:
  - Ítem fijo "SIN PAQUETIZAR" con productos agrupados, expandibles a `review.move.line`.
    **Oculto hasta escanear la ubicación de separación** (el backend no envía `moves` y la vista
    muestra un placeholder; "Verificar" también queda deshabilitado hasta entonces).
  - Lista de paquetes creados, seleccionables.
  - Botón "PAQUETIZAR" en el header de la sidebar.
- **MainSection**: contenido según selección en la sidebar:
  - "Sin paquetizar" → tabla de productos agrupados (cantidad definida / paquetizada / restante),
    expandible a líneas con lote, vencimiento, cantidad, estado visual "seleccionado".
  - Paquete → header con total, input de peso, selector `package_type_id`; debajo, líneas del
    paquete expandibles, con botón "Desempaquetar" para devolver líneas a "Sin paquetizar".
- **PackageWizard**: modal de confirmación al paquetizar.

**Manejo de estado:** la selección de líneas (escaneadas/marcadas) vive como estado local del
componente OWL, no persistido, hasta que se presiona "Paquetizar". Evitar escrituras al backend
por cada escaneo intermedio.

**Modos de configuración que afectan la vista:**
- Ciego vs visual (check + selector de owner): a ciegas no se pueden desplegar `review.move.line`,
  solo se ven los agrupados; visual permite desplegarlas.
- Repaso manual (check + selector de owner): permite ingresar cantidad de repaso a mano en vez de
  escanear. Si es a ciegas, el input va en el agrupado; si es visual, el input va en la línea interna.

---

## 5. Controles y validaciones (parametrizables desde configuración)

1. Escaneo de lotes duplicados: permitir/bloquear (checkbox + selector de owner).
2. Cantidad escaneada no puede superar lo definido en preparación para ese producto/lote.
3. El lote/producto escaneado debe pertenecer a la preparación padre y estar en la ubicación de
   repaso esperada (validar contra la ubicación de Separación).
4. Cualquier escaneo fuera de lo esperado → mensaje de error claro y detallado. Centralizar en un
   método `_validate_scan()` que devuelva excepciones tipadas.
5. Repaso manual solo si el toggle está activo; ubicación del input depende de ciego/visual.
6. "Verificar" compara estado actual del repaso contra el pedido: qué está sin paquete, qué falta
   repasar, cuántos paquetes hay, qué está bien/mal. Es informativo (decisión #1).

Sugeridos a incorporar como configurables (no estaban explícitos en el documento original,
confirmar si se quieren en v1 o quedan para después):
- Tolerancia de diferencia permitida antes de que "Verificar" marque error (0 por defecto).
- Permitir o no Finalizar con productos "sin paquetizar" restantes.
- Registro de auditoría de quién escaneó qué.

---

## 6. Secuencia de tareas para OpenCode

Entregar **una tarea por sesión/prompt**, con contexto acumulativo. No tirar todo el documento
de una sola vez.

### Tarea 1 — Modelos base + máquina de estados
- Crear `review.stock.picking`, `review.move`, `review.move.line`, `review.stage`,
  `review.stock.config`.
- Sin vista OWL todavía; vistas form/tree estándar de Odoo alcanzan para probar.
- La transición de estado se maneja vía `stage_id` (Many2one), no `selection` hardcodeado.
- Incluir los flags booleanos en `review.stage` (`is_initial`, `is_final`, `is_cancel`).

### Tarea 2 — Generación automática del repaso
- Hook en la validación del `stock.picking` padre (preparación) que crea el `review.stock.picking`
  en estado DISPONIBLE con `review.move` prellenados a partir de las líneas resultantes de
  preparación.
- Relación 1:1 estricta: agregar constraint que impida crear más de un repaso por preparación.
- Apartado de configuración con check para habilitar agregar líneas manualmente (deshabilitado
  por default).

### Tarea 3 — Paquetización backend (sin OWL todavía)
- Modelo `review.package.wizard` (TransientModel) para el flujo de confirmación de "Paquetizar".
- Lógica de creación de `stock.quant.package` vacío (sin quants) al confirmar el wizard.
- Método aislado `_assign_quants_to_package(package)` que en v1 se invoca una sola vez por
  paquete durante Finalizar (ver decisión #2 — dejar preparado para reutilizarlo a futuro sin
  reescribirlo).
- Lógica de "Desempaquetar" (mover líneas de vuelta a "sin paquetizar").
- Probar desde shell/vistas form estándar, sin necesidad de OWL aún.

### Tarea 4 — Vista OWL
- Indicar a OpenCode las rutas concretas de los archivos JS/XML de `preparation.stock` como
  referencia obligatoria antes de escribir código.
- Construir los componentes descritos en la sección 4: Header, ScanBar, Sidebar, MainSection,
  PackageWizard.
- Implementar los modos ciego/visual y repaso manual como lectura de configuración, afectando
  qué se puede desplegar y dónde aparece el input manual.
- Estado de selección de líneas como estado local del componente (no persistido).

### Tarea 5 — Controles y validaciones parametrizables
- Implementar `_validate_scan()` centralizado con los controles de la sección 5.
- Conectar cada control a su flag correspondiente en `review.stock.config`.
- Mensajes de error claros y específicos por tipo de falla.

### Tarea 6 — Botón "Verificar" + Finalizar
- "Verificar": método que compara `review.stock.picking` contra el pedido origen y devuelve un
  resumen (sin paquete / falta repasar / paquetes creados / diferencias). Informativo, no bloquea
  Finalizar (decisión #1).
- "Finalizar": itera todos los paquetes del repaso y genera un `stock.move` por producto con
  **origen = destino = ubicación de separación** y `result_package_id` (empacado en sitio, sin
  traslado de ubicación). Pasa `review.stock.picking` a FINALIZADO.
- "Generar picking de salida" (server action, ejecutada desde el botón del wizard): traslada la
  mercadería **Separación → Ubicación de salida** con los mismos paquetes (stock.move directos
  done) y crea el `stock.picking` draft **Expedición → Clientes** con el `picking_type_id` de
  configuración.
- "Cancelar": pasa a CANCELADO. Paquetes vacíos ya creados quedan huérfanos, sin limpieza
  (decisión #3).

---

## 7. Cómo entregar esto a OpenCode

1. Sumar este archivo como `PLAN.md` en la raíz del módulo y pedirle a OpenCode que lo lea antes
   de tocar código, para que quede como referencia persistente entre sesiones.
2. Para cada tarea de la sección 6, abrir un prompt separado indicando:
   - Objetivo puntual de esa tarea (copiar el bloque correspondiente de este archivo).
   - Qué archivos ya existen de tareas anteriores y no debe romper.
   - Qué modelos custom externos (de `preparation.stock` u otros) va a referenciar — a entregar
     por separado, como ya tenés resuelto.
3. Pedir explícitamente que priorice reusar patrones de `preparation.stock` (nombres de campos,
   estructura de componentes OWL, listener de barcode) antes de reinventar soluciones propias.
4. No avanzar a la Tarea 4 (vista OWL) sin haber señalado las rutas concretas de los archivos de
   referencia de `preparation.stock`.

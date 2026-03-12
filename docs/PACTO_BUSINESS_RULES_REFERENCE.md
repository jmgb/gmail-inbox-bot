# Pacto Mundial — Decisiones y Reglas de Negocio

## ADR-001: Solo plantillas en respuestas automáticas (2026-02-25)

### Contexto

Pacto Mundial es una ONG oficial de Naciones Unidas. Los buzones `contabilidad@`, `asociacion@` y `proveedoressostenibles@` gestionan comunicaciones institucionales donde una respuesta inadecuada tiene impacto reputacional.

El sistema clasifica emails entrantes por categoría y ejecuta acciones automáticas (responder, reenviar, etiquetar, mover). Originalmente existía una acción `dynamic_reply` que usaba OpenAI para generar texto libre y enviarlo directamente al remitente.

### Problema detectado

El blueprint original de Make.com para la categoría "otros" creaba un **borrador sin enviar** (usando `createAMessage` de Graph API), dejándolo en la carpeta Borradores para revisión humana.

Nuestro código Python con `dynamic_reply` + `draft_mode: false` **enviaba directamente** la respuesta generada por IA sin revisión. Esto es un riesgo porque:

1. **"otros" es el cajón de sastre** — emails que la IA no supo clasificar bien
2. **El texto generado es impredecible** — no hay garantía de exactitud ni tono institucional
3. **Riesgo reputacional** — una respuesta IA incorrecta desde un buzón oficial de la ONU es grave
4. **Sin red de seguridad** — en asociacion.yaml ni siquiera se movía a carpeta de revisión

### Decisiones

#### Regla 1: NUNCA enviar texto generado por IA automáticamente

> **Las únicas respuestas que el sistema puede enviar automáticamente son plantillas predefinidas en el YAML (`templates:`).**

Si una categoría no tiene plantilla, la acción debe ser `tag_and_move` a "Pendiente Gestionar" para gestión humana. Nunca `dynamic_reply` con envío directo.

#### Regla 2: La acción `dynamic_reply` solo es válida con `draft_mode: true`

Si en el futuro se quiere usar `dynamic_reply` para generar borradores de IA como ayuda al operador, debe ir siempre con `draft_mode: true` a nivel de buzón o con `force_draft: true` en la regla. Nunca generar + enviar en un solo paso.

#### Regla 3: Categoría "otros" = gestión humana

En todos los buzones de Pacto Mundial, la categoría "otros" debe:
- Etiquetar como "PENDIENTE GESTIONAR"
- Mover a carpeta "Pendiente Gestionar"
- Marcar como no leído (`is_read: false`)
- **No generar ningún email ni borrador**

### Cambios aplicados

| Buzón | Antes | Después |
|-------|-------|---------|
| `contabilidad.yaml` | `dynamic_reply` + envío directo | `tag_and_move` + PENDIENTE GESTIONAR |
| `asociacion.yaml` | `dynamic_reply` + envío directo (sin carpeta ni unread) | `tag_and_move` + PENDIENTE GESTIONAR |
| `proveedores_sostenibles.yaml` | Ya usaba `tag` + PENDIENTE GESTIONAR | Sin cambios (correcto) |

### Estado

- **Aplicado**: 2026-02-25
- **Afecta**: `pacto_mundial/config/*.yaml`
- **Tests**: Todos pasaron tras el cambio (solo config, sin cambios de código)

---

## ADR-002: Remitentes internacionales de Proveedores Sostenibles a gestión manual (2026-03-05)

### Contexto

El 5 de marzo de 2026, Inés solicitó incorporar una regla específica para el buzón `proveedoressostenibles@pactomundial.org`.

Hay remitentes internacionales concretos que deben tratarse siempre mediante gestión humana, evitando respuestas o reenvíos automáticos.

### Decisión

Para el buzón de Proveedores Sostenibles, si el remitente del email es alguno de los siguientes:

- `sustainablesuppliers@unglobalcompact.org.uk`
- `emile.ezzeddine@globalcompact-france.org`
- `programmes@pactemondial.org`
- `lily.venables@unglobalcompact.org.uk`

entonces se aplica pre-filtro con:

- Acción: `tag`
- Tag: `PENDIENTE GESTIONAR`

### Implementación

- Archivo actualizado: `pacto_mundial/config/proveedores_sostenibles.yaml`
- Tipo de regla: `pre_filters` (evaluado antes de la clasificación IA)
- Efecto: estos emails quedan siempre en gestión manual con el tag designado

### Estado

- **Aplicado**: 2026-03-05
- **Solicitado por**: Inés

---

## ADR-003: Pre-filtros unificados — bounces, noreply y OOO (2026-03-10)

### Contexto

Los pre-filtros se evalúan antes de la clasificación IA, ahorrando llamadas a OpenAI para emails que no requieren análisis semántico. Hasta ahora solo Proveedores Sostenibles tenía pre-filtros para bounces, y ningún buzón filtraba noreply ni OOO antes de la IA.

### Decisiones

Añadir tres pre-filtros comunes a los tres buzones (`asociacion@`, `contabilidad@`, `proveedoressostenibles@`):

#### 1. Bounces (`silent`)

Emails de rebote (postmaster, MicrosoftExchange NDR, asuntos "No se puede entregar" / "Undeliverable") se marcan como leídos sin acción adicional.

#### 2. Noreply (`silent`)

Emails desde direcciones `noreply@`, `no-reply@`, `no_reply@`, `donotreply@` se marcan como leídos.

#### 3. Respuesta automática / OOO (`delete`)

Emails con subject que contenga frases típicas de fuera de oficina ("Automatic reply", "Out of Office", "Fuera de la oficina", "Respuesta automática", "Abwesenheitsnotiz", "Absence du bureau", "Fora do escritório") se eliminan directamente.

La categoría IA `respuesta_automatica` sigue existiendo en los tres prompts como segunda red para OOO con subjects atípicos que no coincidan con las frases del pre-filtro.

### Orden de evaluación

Los pre-filtros se evalúan en orden secuencial. El orden unificado es:

1. Self-send / plataformas específicas (existente)
2. Internos `@pactomundial.org` (existente)
3. Bounces (nuevo)
4. Noreply (nuevo)
5. OOO por subject (nuevo)

### Estado

- **Aplicado**: 2026-03-10

---

## Reglas permanentes (resumen)

1. **Emails intocables**: NUNCA eliminar emails de los buzones de Pacto Mundial. Son documentos institucionales de una ONG de Naciones Unidas. **Excepción**: las respuestas automáticas de tipo "fuera de oficina" (Out of Office / OOO) sí pueden eliminarse automáticamente (`action: delete`) en todos los buzones, ya que no tienen valor documental.
2. **Solo plantillas en envíos automáticos**: NUNCA enviar texto generado por IA sin revisión humana.
3. **"otros" = gestión humana**: tag + mover + no leído, sin respuesta automática.
4. **`dynamic_reply` requiere `draft_mode: true`**: Solo para crear borradores de revisión.
5. **Idempotencia por tags**: Emails con tags de procesamiento IA nunca se reprocesan.
6. **Selección de idioma**: `idioma == "portugués"` → plantilla PT, todo lo demás → plantilla ESP.
7. **Fallback seguro**: Si una categoría no tiene plantilla ni routing, se etiqueta como "PENDIENTE GESTIONAR".
8. **Plantillas temporales versionadas**: Usar variantes con `valid_from`/`valid_until` en YAML; evitar cambios manuales por fecha.
9. **Sin secretos en plantillas**: Nunca incluir credenciales, tokens ni contraseñas (ni activas ni comentadas) en `config/*.yaml`.

# TASKS — gmail-inbox-bot

## Facturas por enlace (sin PDF adjunto): ángulo muerto del cron mensual (31 ago 2026)

`scripts/download_invoice_emails.py` solo ve emails con PDF adjunto (`has:attachment filename:pdf`).
Las facturas que llegan como **enlace a un portal** (Stripe, algunos SaaS) son invisibles: ni se
descargan ni aparecen en `revisar.csv`. Mejora acordada con el usuario: una **segunda query** del
mismo mes con las mismas `KEYWORDS` pero **sin** `has:attachment`, y volcar los asuntos no cubiertos
por la primera pasada a `revisar.csv` (solo listar, no descargar — descargar sería scraping de
portales, fuera de alcance). Contexto completo en
`docs/superpowers/plans/2026-08-31-descarga-facturas-email-mensual.md`.

## Clasificación sombra con Jev: criterio de salida y revisión de discrepancias (21 sep 2026)

Desde el 21 sep 2026 Jev (TypeSafe.ai) clasifica en sombra cada email junto al LLM; los
resultados están en `email_metrics.jev_*` y en `/admin/dashboard` → "Jev vs LLM". Contexto:
`docs/superpowers/specs/2026-09-21-jev-shadow-classification-design.md`.

**Criterio de salida de la sombra (acordado con el usuario)**: no decidir el corte a Jev hasta
cumplir ambos: (a) ≥ 300 emails comparados y (b) coincidencia LLM/Jev ≥ 95 % en el tramo de
confianza `>=0.9`. Con eso, diseñar la fase 2 (Jev decide; umbral de confianza por categoría, más
exigente para `spam` porque va a papelera, más laxo para `REVISAR IA`).

**Revisar discrepancias — a partir del 28 sep 2026**: sacar todas las filas con
`category <> jev_category` y etiquetar cada una como "acierta LLM" / "acierta Jev" / "ambiguo"
junto con el usuario. Lo que importa es quién tiene razón en cada `DIFIEREN`, no el % global.
Query de partida:

```sql
SELECT created_at, mailbox, sender, subject, category AS llm, jev_category AS jev,
       jev_confidence, classification_reason
FROM email_metrics
WHERE jev_category IS NOT NULL AND category <> jev_category
ORDER BY created_at DESC;
```

Mientras dure la sombra: no cambiar el prompt LLM salvo error grave y, si se cambia, replicar la
regla en `gmail_inbox_bot/prompts/clasificador_jev.yml` el mismo día.

## El camino de respuesta con plantillas está sin usar en este repo (27 sep 2026)

Ningún buzón de `config/` define `templates`: el routing real solo usa `tag`, `move`,
`tag_and_move` y `silent`. Eso deja sin ejercitar en producción `_get_template_body`, las
plantillas `esp`/`pt` y las acciones `reply`/`reply_with_attachment`/`dynamic_reply`, que solo
las cubren los tests con el `MOCK_CONFIG` heredado de `pacto-mundial-bot`. Además la rama `pt`
decide por igualdad exacta sobre un campo `idioma` que el prompt de este repo **no** devuelve
(en pacto ese mismo código se corrigió el 27 sep con `_normalize_lang_key`).

Decidir: (a) mantenerlo como capacidad documentada y portar la normalización de pacto para que
las dos copias no divergan, o (b) retirar el camino de plantillas de este repo y quedarse con
las acciones que se usan. No tocado ahora porque es un refactor, no limpieza.

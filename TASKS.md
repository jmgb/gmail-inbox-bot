# TASKS — gmail-inbox-bot

## Facturas por enlace (sin PDF adjunto): ángulo muerto del cron mensual (31 ago 2026)

`scripts/download_invoice_emails.py` solo ve emails con PDF adjunto (`has:attachment filename:pdf`).
Las facturas que llegan como **enlace a un portal** (Stripe, algunos SaaS) son invisibles: ni se
descargan ni aparecen en `revisar.csv`. Mejora acordada con el usuario: una **segunda query** del
mismo mes con las mismas `KEYWORDS` pero **sin** `has:attachment`, y volcar los asuntos no cubiertos
por la primera pasada a `revisar.csv` (solo listar, no descargar — descargar sería scraping de
portales, fuera de alcance). Contexto completo en
`docs/superpowers/plans/2026-08-31-descarga-facturas-email-mensual.md`.

## Jev es el clasificador principal: umbral de confianza pendiente (27 sep 2026)

**Estado**: el 27 sep 2026 Jev pasó de sombra a **principal** por decisión del usuario, con la
cadena LLM (`gpt-oss-120b` → `gpt-6-luna`) como fallback **solo ante error de Jev**.

**El corte se hizo sin cumplir el criterio de salida que estaba acordado aquí**, y conviene tenerlo
escrito porque cambia lo que queda por hacer:

- Criterio (a) ≥ 300 emails comparados: **no cumplido**, se cortó con 201.
- Criterio (b) coincidencia ≥ 95 % en el tramo `>=0.9`: **cumplido** (96,5 %, n=85).
- Coincidencia global en el momento del corte: 78,6 %. Por tramo: `<0.5` 61,5 % (n=26),
  `0.5-0.7` 54,5 % (n=44), `0.7-0.9` 78,3 % (n=46), `>=0.9` 96,5 % (n=85).
- La sesión de etiquetado de las 43 discrepancias (prevista para el 28 sep) **no se había hecho**.

**Consecuencia directa**: la muestra de comparación está congelada en 201. Si Jev decide, no hay
clasificación LLM con la que compararse, así que ni se llega a 300 ni aparecen discrepancias nuevas.
Las 43 existentes son las que habrá. El dashboard las conserva y cuenta aparte los "Decididos por
Jev" para que se vea que esa muestra ya no crece.

### Lo que queda, por orden

1. **Umbral de confianza por categoría** — es lo único que falta para que esto sea seguro por
   construcción. Hoy una clasificación de Jev con `0.40` decide igual que una con `0.99`, y los
   tramos bajos son justo donde menos acierta (54,5 % en `0.5-0.7`). Diseño previsto: por debajo del
   umbral, caer al LLM en vez de decidir; más exigente para `spam` (va a papelera) y más laxo para
   las que acaban en `REVISAR IA` (las ve el usuario de todas formas).
   Caso real que lo justifica: un email de estafa (`Re: getting back to this`) salió
   `jev=finanzas` con **confianza 0.88** mientras el LLM acertaba con `spam`. Un umbral global por
   encima de 0,85 lo habría dejado pasar: `finanzas` necesita el suyo, no solo `spam`.
2. **Etiquetar las 43 discrepancias congeladas** — "acierta LLM" / "acierta Jev" / "ambiguo". Sigue
   siendo la mejor evidencia disponible para calibrar los umbrales del punto 1, y ahora es la única.
   Empezar por las 20 de la frontera `spam↔newsletters`, que son casi la mitad.
   ```sql
   SELECT created_at, mailbox, sender, subject, category AS llm, jev_category AS jev,
          jev_confidence, classification_reason
   FROM email_metrics
   WHERE jev_category IS NOT NULL AND category <> jev_category
   ORDER BY created_at DESC;
   ```
3. **Vigilar los `jev_error`** — cada uno es un email clasificado por el LLM, no por Jev. Con el
   timeout en 4 s sin reintento (2026-09-27) se esperan algunos más que en sombra.

**Camino de vuelta**: vaciar `JEV_API_KEY` en el `.env` del VPS y reiniciar. No requiere desplegar.

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

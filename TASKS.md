# TASKS — gmail-inbox-bot

## Facturas por enlace (sin PDF adjunto): ángulo muerto del cron mensual (31 ago 2026)

`scripts/download_invoice_emails.py` solo ve emails con PDF adjunto (`has:attachment filename:pdf`).
Las facturas que llegan como **enlace a un portal** (Stripe, algunos SaaS) son invisibles: ni se
descargan ni aparecen en `revisar.csv`. Mejora acordada con el usuario: una **segunda query** del
mismo mes con las mismas `KEYWORDS` pero **sin** `has:attachment`, y volcar los asuntos no cubiertos
por la primera pasada a `revisar.csv` (solo listar, no descargar — descargar sería scraping de
portales, fuera de alcance). Contexto completo en
`docs/superpowers/plans/2026-08-31-descarga-facturas-email-mensual.md`.

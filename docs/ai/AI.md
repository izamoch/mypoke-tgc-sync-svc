# 🧠 Sync Service AI Context Anchor

Bienvenida, IA. Este servicio es crítico para que la app móvil tenga datos frescos. No rompas la lógica de "Smart Sync" ni modifiques los modelos enviados a D1 sin actualizar el Worker y el código de la app móvil (repositorio `scanmon-tracker-app`).

## 📜 Estado Actual
- El servicio está operativo y corriendo tareas programadas en una Raspberry Pi.
- La cobertura de tests es del 100% en la lógica de estrategia de sincronización.
- Se ha implementado el enriquecimiento con PokéAPI.
- La base de datos de producción es Cloudflare D1; este servicio NO se conecta a ella directamente. Los cambios se envían vía HTTP a `POST {WORKER_URL}/sync/update` (ver `d1_client.py`).
- El estado local (qué sets/cards existen, cooldowns de precios) vive en SQLite (`/data/poke_tgc.sqlite`), creado automáticamente al iniciar.

## 🛠️ Tareas Pendientes
- [ ] Implementar un retry-exponential-backoff para las peticiones a PokéAPI (a veces falla por rate limit).
- [ ] Añadir validación de esquemas con Pydantic antes de enviar los payloads al Worker.

## 📊 Últimos Cambios
- [2026-06-12]: **Migración a Cloudflare D1 vía Worker HTTP API (ADR 006).**
  - Eliminada la conexión directa a Postgres/Supabase (`psycopg2`, `export.py`).
  - Nuevo módulo `d1_client.py`: envío chunked con reintentos a `POST {WORKER_URL}/sync/update` (headers `X-API-Key`/`Content-Type`).
  - SQLite local pasa de "backup" a base de estado primaria; `database.py` crea el esquema automáticamente.
  - Nuevas variables de entorno: `WORKER_URL`, `ADMIN_TOKEN`.
- [2026-04-03]: **Refactor de Limpieza y Estandarización.**
  - Creación de este sistema de anclaje de contexto en `docs/ai/`.
  - Verificación de la estructura del proyecto y estándares de Ruff/Mypy.
  - Sincronización de premisas de negocio en `VISION.md`.

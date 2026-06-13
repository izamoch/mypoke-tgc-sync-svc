# 🧠 Sync Service AI Context Anchor

Bienvenida, IA. Este servicio es crítico para que la app móvil tenga datos frescos. No rompas la lógica de "Smart Sync" ni modifiques el esquema de D1 sin tener en cuenta que la app móvil (repositorio `scanmon-tracker-app`) lee de la misma base vía el Worker.

## 📜 Estado Actual
- El servicio está operativo y corriendo tareas programadas en una Raspberry Pi.
- La cobertura de tests es del 100% en la lógica de estrategia de sincronización y del cliente D1.
- Se ha implementado el enriquecimiento con PokéAPI.
- **Cloudflare D1 es la única fuente de la verdad.** Este servicio lee y escribe directamente en D1 vía su API REST (ver `d1_client.py`), sin estado local ni proxy intermedio.
- No existe SQLite local ni SQLAlchemy: `database.py`, `models.py` y `/data/` fueron eliminados.

## 🛠️ Tareas Pendientes
- [x] Implementar un retry-exponential-backoff para las peticiones a PokéAPI (implementado en `pokeapi_client.py` con `with_async_retry`).
- [x] Evaluar si conviene exponer `card_limit` por CLI para correr barridos iniciales acotados (implementado e integrado en `main.py`).

## 📊 Últimos Cambios
- [2026-06-13]: **Migraciones e índices aplicados en D1 y CLI expandido.**
  - Ejecutadas con éxito las migraciones de índices en el D1 de producción (`0001_performance_indexes.sql`), incluyendo la creación del índice de cobertura de Smart Sync (`idx_card_prices_covering_sync`) y la eliminación del índice redundante `idx_card_prices_card_id` para reducir operaciones de lectura/escritura (ahorro del plan gratuito).
  - Añadido el parámetro `--card-limit` a la CLI para limitar el procesamiento de cartas nuevas en barridos iniciales.
- [2026-06-13]: **Sync directo a D1 vía REST API (ADR 007).**
  - Eliminados `database.py`, `models.py`, `/data/` y la dependencia `sqlalchemy` — D1 es la única fuente de la verdad.
  - Eliminado el push vía Worker (`POST {WORKER_URL}/sync/update`, `WORKER_URL`, `ADMIN_TOKEN`).
  - `d1_client.py` reescrito: `d1_query`/`d1_raw_batch`/`chunked_upsert`/`chunked_update` contra la API REST de Cloudflare D1, respetando el límite de 100 parámetros por statement.
  - `sync.py` reescrito: lecturas "lo justo y necesario" (`SELECT id FROM sets|cards`, `CANDIDATE_QUERY` con JOIN/GROUP BY para Smart Sync), sin cambios en `determine_check_strategy`.
  - Nuevas variables de entorno: `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `D1_DATABASE_ID`.
  - Nuevos índices en D1: `idx_card_prices_card_variant` (único), `idx_cards_updated_at`, `idx_card_prices_card_id`.
- [2026-06-12]: **Migración a Cloudflare D1 vía Worker HTTP API (ADR 006, superseded por ADR 007).**
  - Eliminada la conexión directa a Postgres/Supabase (`psycopg2`, `export.py`).
- [2026-04-03]: **Refactor de Limpieza y Estandarización.**
  - Creación de este sistema de anclaje de contexto en `docs/ai/`.
  - Verificación de la estructura del proyecto y estándares de Ruff/Mypy.
  - Sincronización de premisas de negocio en `VISION.md`.

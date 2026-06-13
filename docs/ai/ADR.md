# 📜 Architectural Decision Records (ADR)

Este archivo registra las decisiones técnicas clave tomadas en el motor de sincronización.

## [2026-04-03] ADR 001: Estrategia de Sincronización "Smart Sync"
- **Estatus:** Aceptado
- **Contexto:** Las APIs externas (TCGDex) tienen límites de tasa y los metadatos de cartas antiguas no cambian a menudo. Los precios sí fluctúan.
- **Decisión:** Implementar una rotación de hashes:
    - Cartas Premium (≥$20): Chequeo diario.
    - Cartas Standard ($0-$20): Chequeo cada 5 días (hash % 5).
    - Cartas sin precio: Chequeo cada 15 días (hash % 15).
- **Consecuencia:** Reducción masiva de peticiones API y ahorro de ancho de banda.

## [2026-04-03] ADR 002: Enriquecimiento Asíncrono con PokéAPI
- **Estatus:** Aceptado
- **Contexto:** TCGDex proporciona metadatos de cartas, pero carece de cadenas evolutivas completas y textos de Pokédex.
- **Decisión:** Usar PokéAPI como fuente secundaria para rellenar campos `flavor_text` y `evolutions` (JSON).
- **Consecuencia:** La experiencia de usuario en la app móvil es mucho más rica y "oficial".

## [2026-04-03] ADR 003: Replicación SQLite Local (Offline Backup)
- **Estatus:** Superseded por ADR 006
- **Contexto:** Si Supabase cae o hay problemas de red durante un backup, se pierde la visibilidad del estado de la sincronización.
- **Decisión:** Al final de cada ejecución exitosa, volcar el contenido de Supabase a un archivo local `/data/poke_tgc.sqlite`.
- **Consecuencia:** Permite realizar auditorías rápidas de datos sin necesidad de conectarse a la DB de producción.

## [2026-04-03] ADR 004: Adopción de Ruff para Calidad
- **Estatus:** Aceptado
- **Contexto:** La base de código de Python crecía sin un estilo unificado claro.
- **Decisión:** Sustituir linters lentos por Ruff.
- **Consecuencia:** Tiempos de linting casi instantáneos y cumplimiento de PEP 8 garantizado.

## [2026-04-03] ADR 005: Robustez y Rendimiento en Sync Engine
- **Estatus:** Aceptado
- **Contexto:** Se detectaron cuellos de botella en la exportación SQLite (~1min para 10k cartas) y fragilidad en el cliente de PokéAPI (sin reintentos).
- **Decisión:**
    - Implementar **Exponential Backoff** en el cliente de PokéAPI para manejar errores 429 y 5xx.
    - Optimizar la exportación SQLite usando **Bulk Inserts** (`insert().values()`) y **Pragmas** de rendimiento (`synchronous=OFF`, `journal_mode=MEMORY`).
    - Añadir **Validación de Datos** manual (`validator.py`) antes de persistir para asegurar integridad.
- **Consecuencia:** Sincronización más estable y replicación local instantánea (segundos en lugar de minutos).

## [2026-06-12] ADR 006: Migración a Cloudflare D1 vía Worker HTTP API
- **Estatus:** Superseded por ADR 007
- **Contexto:** La base de datos de producción se migró de Supabase (PostgreSQL) a Cloudflare D1. D1 no acepta conexiones TCP directas (psycopg2/SQLAlchemy contra Postgres), solo es accesible mediante Workers.
- **Decisión:**
    - Eliminar toda conexión directa a Postgres (`psycopg2`, `export.py`, replicación Supabase → SQLite).
    - SQLite local (`/data/poke_tgc.sqlite`) pasa de ser un "backup" a ser la **única base de estado local** (existencia de sets/cards, cooldowns de precios para Smart Sync). `Base.metadata.create_all` se ejecuta al iniciar (`database.py`) para garantizar el esquema.
    - Los registros nuevos/actualizados (`sets`, `cards`, `prices`) se envían a `POST {WORKER_URL}/sync/update` con cabecera `X-API-Key: ADMIN_TOKEN`, vía el nuevo módulo `d1_client.py`.
    - El envío se fragmenta en chunks (~150 registros por lista) para respetar los límites de tamaño/tiempo de ejecución de Workers/D1, con reintentos simples (backoff exponencial) ante errores 4xx/5xx o de red.
- **Consecuencia:** El servicio en la Raspberry Pi ya no requiere conectividad de base de datos saliente más allá de HTTPS al Worker. Si `WORKER_URL`/`ADMIN_TOKEN` faltan, el push se omite (`skipped`) sin romper el resto del job.

## [2026-06-13] ADR 007: Sync directo a D1 vía REST API; eliminación del Worker proxy y de SQLite local
- **Estatus:** Aceptado
- **Contexto:**
    - El Worker `/sync/update` (ADR 006) tiene un bug crítico: devuelve 500 ante cualquier campo `null` en `cards`, y el endpoint de `prices` está completamente roto. Esto bloqueaba la sincronización de precios en producción.
    - El estado local en SQLite (`/data/poke_tgc.sqlite`) quedó desincronizado respecto a D1 (~23k cards en producción vs 1 en local), generando divergencias difíciles de depurar (p.ej. el incidente de `base1-1`, donde el estado local y D1 contaban historias distintas).
    - Mantener "estado local (SQLite) + proxy (Worker) + D1" implicaba **tres copias de la verdad** que podían desincronizarse de forma silenciosa.
- **Decisión:**
    - **D1 es la única fuente de la verdad.** Se elimina por completo el estado local en SQLite (`database.py`, `models.py`, directorio `data/`, dependencia `sqlalchemy`).
    - Se elimina el proxy del Worker del camino de escritura (`POST {WORKER_URL}/sync/update`, `WORKER_URL`, `ADMIN_TOKEN`).
    - `d1_client.py` se reescribe para hablar directo con la **API REST de Cloudflare D1** (`POST .../d1/database/{id}/query` y `/raw` para batches), usando `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` (scoped a `D1:Edit` sobre esta base de datos) + `D1_DATABASE_ID`.
    - Lecturas "lo justo y necesario": `SELECT id FROM sets|cards` para detectar novedades frente a TCGDex, y una query con `LEFT JOIN`/`GROUP BY` (`CANDIDATE_QUERY`) que trae `id, updated_at, max_market` de un solo golpe para alimentar Smart Sync.
    - Escrituras vía `chunked_upsert`/`chunked_update` con `INSERT ... ON CONFLICT DO UPDATE` y `UPDATE ... WHERE id=?`, respetando el límite de 100 parámetros por statement de D1 (`D1_MAX_PARAMS_PER_STATEMENT`), agrupando varios statements por request vía `/raw`.
    - **La lógica de Smart Sync (`determine_check_strategy`) no cambia**: sigue siendo una función pura sobre `id`/`updated_at`/`max_market_price`, ahora alimentada por la fila de D1 en lugar del ORM local.
    - Nuevos índices en D1: `idx_card_prices_card_variant` (único, requerido para el upsert de precios), `idx_cards_updated_at`, `idx_card_prices_card_id`, además de los índices ya existentes en `cards`/`sets`.
    - Si `CLOUDFLARE_ACCOUNT_ID`/`CLOUDFLARE_API_TOKEN`/`D1_DATABASE_ID` no están configurados, el job aborta inmediatamente (no hay "modo degradado" posible: sin D1 no hay estado que leer ni escribir).
- **Consecuencia:** Arquitectura end-to-end sin dependencias externas más allá de TCGDex, PokéAPI y D1: el servicio en la Raspberry Pi lee y escribe directamente en D1 por HTTPS. El Worker sigue existiendo como API de lectura para la app móvil (`scanmon-tracker-app`), sin cambios. Se elimina por diseño la posibilidad de divergencia entre "estado local" y "producción".

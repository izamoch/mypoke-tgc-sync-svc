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
- **Estatus:** Aceptado
- **Contexto:** La base de datos de producción se migró de Supabase (PostgreSQL) a Cloudflare D1. D1 no acepta conexiones TCP directas (psycopg2/SQLAlchemy contra Postgres), solo es accesible mediante Workers.
- **Decisión:**
    - Eliminar toda conexión directa a Postgres (`psycopg2`, `export.py`, replicación Supabase → SQLite).
    - SQLite local (`/data/poke_tgc.sqlite`) pasa de ser un "backup" a ser la **única base de estado local** (existencia de sets/cards, cooldowns de precios para Smart Sync). `Base.metadata.create_all` se ejecuta al iniciar (`database.py`) para garantizar el esquema.
    - Los registros nuevos/actualizados (`sets`, `cards`, `prices`) se envían a `POST {WORKER_URL}/sync/update` con cabecera `X-API-Key: ADMIN_TOKEN`, vía el nuevo módulo `d1_client.py`.
    - El envío se fragmenta en chunks (~150 registros por lista) para respetar los límites de tamaño/tiempo de ejecución de Workers/D1, con reintentos simples (backoff exponencial) ante errores 4xx/5xx o de red.
- **Consecuencia:** El servicio en la Raspberry Pi ya no requiere conectividad de base de datos saliente más allá de HTTPS al Worker. Si `WORKER_URL`/`ADMIN_TOKEN` faltan, el push se omite (`skipped`) sin romper el resto del job.

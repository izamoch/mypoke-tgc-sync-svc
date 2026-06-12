# 🏗️ Architecture: MyPoke Sync Service

## 🛠️ Stack Tecnológico
- **Backend:** Python 3.11+ (Asíncrono).
- **Database Principal:** Cloudflare D1, accedida exclusivamente vía la API HTTP de un Cloudflare Worker (no hay conexión TCP directa).
- **Database de Estado Local:** SQLite (`/data/poke_tgc.sqlite`) — única fuente local de verdad para saber qué sets/cards ya existen y cuándo se revisó cada precio (estrategia "Smart Sync").
- **API Clients:** HTTPX (Asíncrono) para TCGDex, PokéAPI y el Worker de Cloudflare.
- **Image Analysis:** ImageHash (pHash) para firmas visuales.

## 🧩 Flujo de Sincronización
1. **Extraction:** Se descarga el catálogo de TCGDex.
2. **Strategy Filter:** Se aplica la rotación de hashes (contra el estado en SQLite local) para decidir qué cartas necesitan actualización de precios (Premium, Standard, o Untracked).
3. **Enrichment:** Se solicita información adicional a PokéAPI para cartas de especies no mapeadas.
4. **Local Upsert:** Se actualiza el SQLite local (estado/cooldowns) usando transacciones atómicas.
5. **D1 Push:** Los registros nuevos/actualizados (`sets`, `cards`, `prices`) se envían en lotes (`d1_client.py`) mediante `POST {WORKER_URL}/sync/update`, con cabecera `X-API-Key: ADMIN_TOKEN`, fragmentados en chunks (~150 registros por lista) y con reintentos simples ante errores 4xx/5xx.

## ⚙️ Procesos Críticos
- **Rate Limiting:** El servicio respeta las cuotas de TCGDex y PokéAPI mediante delays configurables.
- **D1 Sync Resilience:** Si `WORKER_URL`/`ADMIN_TOKEN` no están configurados, el push a D1 se omite (logueado como `skipped`) y el resto del job continúa normalmente contra el estado local.
- **Error Reporting:** Tras cada ejecución, se genera un reporte en markdown en `reports/` (incluyendo el estado del push a D1) y se envía a un webhook si está configurado.

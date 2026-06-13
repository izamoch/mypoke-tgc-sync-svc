# 🏗️ Architecture: MyPoke Sync Service

## 🛠️ Stack Tecnológico
- **Backend:** Python 3.11+ (Asíncrono).
- **Database:** Cloudflare D1, accedida directamente vía la API REST de Cloudflare (`d1_client.py`). D1 es la **única fuente de la verdad** — no hay estado local ni proxy intermedio.
- **API Clients:** HTTPX (Asíncrono) para TCGDex, PokéAPI y la API REST de Cloudflare D1.
- **Image Analysis:** ImageHash (pHash) para firmas visuales.

## 🧩 Flujo de Sincronización
1. **Extraction:** Se descarga el catálogo de TCGDex (`sets`, `cards`).
2. **Diff contra D1:** `SELECT id FROM sets` / `SELECT id FROM cards` para detectar qué sets/cards son nuevos.
3. **Strategy Filter (Smart Sync):** Para precios, una sola query (`CANDIDATE_QUERY`, con `LEFT JOIN card_prices` + `GROUP BY`) trae `id, updated_at, max_market` de todas las cartas. `determine_check_strategy` (función pura, sin cambios respecto a versiones previas) decide qué cartas se revisan hoy (PREMIUM/STANDARD/NO_PRICE + variantes SAFETY).
4. **Enrichment:** Se solicita información adicional a PokéAPI (lore, evoluciones) y se calcula pHash para cartas nuevas o sin `flavor_text`.
5. **D1 Upsert:** Los registros nuevos/actualizados (`sets`, `cards`, `card_prices`) se escriben directo en D1 vía `d1_client.chunked_upsert` (`INSERT ... ON CONFLICT DO UPDATE`) y `d1_client.chunked_update` (`UPDATE ... WHERE id=?`), agrupando varios statements por request (`/raw` batch endpoint) y respetando el límite de 100 parámetros por statement.

## 🔌 Cliente D1 (`d1_client.py`)
- `d1_query(sql, params)` — un único statement vía `POST /d1/database/{id}/query`, devuelve `result[0].results`.
- `d1_raw_batch(statements)` — varios statements independientes vía `POST /d1/database/{id}/raw` (`{"batch": [...]}`).
- `chunked_upsert(table, columns, conflict_columns, rows)` — construye `INSERT ... VALUES (...),(...) ON CONFLICT(...) DO UPDATE SET col=excluded.col` agrupando filas hasta el límite de 100 parámetros/statement, y agrupando statements en lotes de requests.
- `chunked_update(table, set_columns, where_column, rows)` — un `UPDATE ... WHERE id=?` por fila, agrupado igual en batches de `/raw`.
- Reintentos con backoff exponencial ante `5xx`/errores de transporte (`_post_with_retry`).

## ⚙️ Procesos Críticos
- **Rate Limiting:** El servicio respeta las cuotas de TCGDex y PokéAPI mediante delays configurables.
- **Fail-fast en D1:** Si `CLOUDFLARE_ACCOUNT_ID`/`CLOUDFLARE_API_TOKEN`/`D1_DATABASE_ID` no están configurados, el job aborta de inmediato (sin D1 no hay estado que leer ni escribir, no existe modo "skip").
- **Error Reporting:** Tras cada ejecución, se genera un reporte en markdown/HTML en `reports/` (incluyendo errores de escritura en D1, si los hubo) y se envía a un webhook si está configurado.

## 📱 Frontera con la app móvil
El Worker de Cloudflare (`scanmon-tracker-app` lo consume) sigue existiendo como **API de lectura** para la app móvil — este servicio no lo modifica ni depende de él. El sync escribe directo en D1; el Worker lee de la misma D1.

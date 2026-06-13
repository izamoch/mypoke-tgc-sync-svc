# 🎯 Project Vision: Pokémon TCG Data Sync

## 💡 Propósito
Motor de sincronización incremental encargado de alimentar MyPoke con datos premium de TCGDex y PokéAPI.

## 🚀 Premisas del Motor (Inamovibles)
- **Smart Sync Strategy**: No saturar APIs externas. Rotación de hashes para precios y metadatos.
- **Lore Enrichment**: Los datos base de TCGDex se enriquecen con cadenas evolutivas y lore de PokéAPI.
- **Atomic Sync**: Una carta no se actualiza a medias. Si falla la actualización de precios, se revierte la transacción para esa carta.
- **D1 como única fuente de la verdad**: No hay estado local ni réplica intermedia. Qué existe y cuándo se revisó cada precio se lee directamente de D1 (vía su API REST) con queries optimizadas para traer solo lo necesario.
- **D1 Direct Write**: Toda novedad (sets, cards, prices) se escribe directo en Cloudflare D1 vía su API REST (`d1_client.py`), en lotes respetando el límite de parámetros por statement y con reintentos, sin pasar por el Worker.

# 🎯 Project Vision: Pokémon TCG Data Sync

## 💡 Propósito
Motor de sincronización incremental encargado de alimentar MyPoke con datos premium de TCGDex y PokéAPI.

## 🚀 Premisas del Motor (Inamovibles)
- **Smart Sync Strategy**: No saturar APIs externas. Rotación de hashes para precios y metadatos.
- **Lore Enrichment**: Los datos base de TCGDex se enriquecen con cadenas evolutivas y lore de PokéAPI.
- **Atomic Sync**: Una carta no se actualiza a medias. Si falla la actualización de precios, se revierte la transacción para esa carta.
- **Local State Store**: SQLite (/data/poke_tgc.sqlite) es la base de estado local (qué existe, cuándo se revisó) que alimenta la estrategia Smart Sync, sin depender de conectividad a la base de datos de producción.
- **D1 Push**: Toda novedad (sets, cards, prices) se envía a Cloudflare D1 vía la API HTTP del Worker, en lotes pequeños y con reintentos, nunca mediante conexión directa a la base de datos.

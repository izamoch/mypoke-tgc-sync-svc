# 🤖 Sync Service AI Context Index (MAPA MAESTRO)

Bienvenida, IA. Este servicio es el motor de datos de MyPoke. Debes leer estos archivos para entender el flujo de sincronización y las reglas de negocio antes de proponer cambios.

### 1. 📂 [Entrada Principal: AI.md](./AI.md)
Estado actual del servicio, logs de la última ejecución y tareas pendientes.

### 2. 🎯 [Visión del Motor: VISION.md](./VISION.md)
El "por qué" de la estrategia de sincronización (Smart Sync, Delta Updates, Lore Enrichment).

### 3. 🏗️ [Estructura del Servicio: ARCH.md](./ARCH.md)
El "cómo". Integración con Cloudflare D1 vía Worker HTTP API, estado local en SQLite y cliente de PokéAPI/TCGDex.

### 4. 🛠️ [Guía de Calidad: STANDARDS.md](./STANDARDS.md)
Normas de Python (Ruff/Mypy), manejo de concurrencia asíncrona y protocolos de reporte.

### 5. 📜 [Historial de Decisiones: ADR.md](./ADR.md)
Registro de por qué usamos ciertas estrategias (ej: Hash Rotation para precios).

---
**IMPORTANTE:** Si modificas la lógica de sincronización o el esquema de la base de datos, actualiza el `ADR.md` y el `README.md` principal.

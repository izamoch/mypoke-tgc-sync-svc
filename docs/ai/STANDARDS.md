# 🛠️ Coding Standards & Quality Protocol

## 📝 Convenciones de Python
- **Estilo:** Seguir `pyproject.toml` con Ruff (reglas E, W, F, I, C, B).
- **Tipado:** Tipado estático con Mypy obligatorio en firmas de funciones públicas.
- **Naming:** CamelCase para clases, snake_case para variables y funciones.
- **Imports:** Ordenados con isort (incluido en Ruff).

## 🧪 Protocolo de Testing
- **Unit Testing:** Obligatorio para lógica de transformación y cliente de APIs.
- **Mocks:** Usar `pytest-mock` para evitar peticiones reales a TCGDex/PokéAPI.
- **Coverage:** El objetivo de cobertura mínima es del 80%.

## 🐞 Debugging & Logging
- **Structured Logs:** Usar `logging` de Python estándar con formato legible.
- **Error Handing:** Nunca silenciar excepciones sin capturar (`except: pass` prohibido). Siempre usar bloques `try/except` específicos.
- **Reports:** El archivo markdown de reporte en `reports/` es la única fuente oficial del estado de la sincronización.

## 🔄 Workflow de Vibe Coding
1. **Analyze:** Antes de modificar los modelos o los payloads enviados, verificar el esquema esperado por el Worker/D1.
2. **Test:** Correr `pytest` tras cada cambio significativo.
3. **Lint:** Ejecutar `ruff check .` antes de subir cambios.

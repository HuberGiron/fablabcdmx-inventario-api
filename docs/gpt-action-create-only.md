# Action del GPT: política create-only

La integración permite que el GPT consulte el inventario y, cuando el usuario lo solicita y confirma expresamente, cree zonas, subzonas, ubicaciones o ítems nuevos.

No hay endpoints para actualizar, reemplazar, mezclar o eliminar registros.

## Protecciones

1. Todas las rutas usan una API key Bearer exclusiva del GPT.
2. La preparación vuelve a consultar Firestore y rechaza IDs, SKU y códigos de área existentes.
3. El servidor devuelve el borrador normalizado y un token firmado con vencimiento.
4. El GPT debe mostrar el borrador exacto y obtener confirmación explícita.
5. La creación verifica el token y vuelve a validar la base inmediatamente antes de escribir.
6. El token queda ligado criptográficamente al contenido; cualquier cambio invalida la confirmación.
7. Se usa exclusivamente `batch.create(...)`. Si un documento existe, Firestore rechaza el lote completo.
8. Zona, subzona, ubicación e ítem se crean en un solo lote atómico, con máximo 399 registros más una auditoría.
9. Cada alta crea un documento en `gptCreateAudits/{auditId}` para evitar reutilizar exitosamente la misma confirmación y conservar trazabilidad.

## Secretos

Genera dos valores diferentes:

```bash
openssl rand -hex 32
openssl rand -hex 32
```

Agrégalos a `/etc/fablab-inventario-api/api.env`:

```env
GPT_ACTION_API_KEY="PRIMER_VALOR"
GPT_ACTION_SIGNING_SECRET="SEGUNDO_VALOR"
GPT_ACTION_TOKEN_TTL_SECONDS=600
```

Configura la Action del GPT con autenticación **API Key → Bearer** y usa únicamente `GPT_ACTION_API_KEY`. `GPT_ACTION_SIGNING_SECRET` permanece siempre en el servidor.

## Flujo de alta

1. `validateInventoryDraft`: diagnóstico sin escritura.
2. `prepareInventoryCreate`: revalida, normaliza y devuelve `confirmationToken` y `confirmationText`.
3. El GPT muestra `normalizedDraft` y pide confirmación explícita.
4. `createInventoryRecords`: operación marcada como consecuencial; ChatGPT vuelve a mostrar su confirmación de interfaz.
5. El backend revalida y ejecuta el lote con `create`.

Si el inventario cambia entre los pasos 2 y 4, la creación devuelve `409` y no escribe nada.

## Prueba en el servidor

```bash
curl -H "Authorization: Bearer TU_GPT_ACTION_API_KEY" \
  "https://inventario-api.mecatronica-ibero.mx/api/gpt/context?includeLocations=false"
```

Nunca pegues en ChatGPT la cuenta de servicio de Firebase ni `GPT_ACTION_SIGNING_SECRET`.

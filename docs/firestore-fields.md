# Campos agregados a Firestore

## Colección `items`

Cuando se sube una imagen al item, se agregan o actualizan estos campos:

```json
{
  "imageFileId": "uuid",
  "imageFilename": "nombre-original.jpg",
  "imageMimeType": "image/jpeg",
  "imageSizeBytes": 12345,
  "imageUpdatedAt": "serverTimestamp",
  "updatedAt": "serverTimestamp"
}
```

Cuando se sube documentación al item, se agregan o actualizan estos campos:

```json
{
  "documentationFileId": "uuid",
  "documentationFilename": "manual.pdf",
  "documentationMimeType": "application/pdf",
  "documentationSizeBytes": 12345,
  "documentationUpdatedAt": "serverTimestamp",
  "pdfFileId": "uuid",
  "pdfFilename": "manual.pdf",
  "updatedAt": "serverTimestamp"
}
```

`pdfFileId` y `pdfFilename` se conservan por compatibilidad con vistas anteriores.

## Colección `files`

Cada archivo subido crea un documento en `files/{fileId}`:

```json
{
  "fileId": "uuid",
  "itemId": "id-del-item",
  "assetType": "image | documentation",
  "originalFilename": "nombre-original.pdf",
  "storedFilename": "uuid.pdf",
  "storedPath": "ab/uuid.pdf",
  "mimeType": "application/pdf",
  "sizeBytes": 12345,
  "active": true,
  "createdAt": "serverTimestamp",
  "updatedAt": "serverTimestamp",
  "createdByUid": "uid-admin"
}
```

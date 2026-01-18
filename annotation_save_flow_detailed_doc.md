# FiftyOne Annotation Save処理の詳細フロー解説

## 概要

FiftyOne の Annotation 機能では、サイドバーに表示される編集UI（Detection、Classification、Polylineなど）で項目を編集した後、**Save**ボタンをクリックすることで変更がデータベースに保存されます。

この  ドキュメントでは、Saveボタンクリックから DB 保存、UI 再描画までの全処理フローを詳細に解説します。

---

## 処理フロー概要図

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. UI層 (React Component)                                       │
│    - Saveボタンクリック                                          │
│    - Footer.tsx → useSave hook呼び出し                          │
└────────────────┬────────────────────────────────────────────────┘
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. Command発行 (Command Pattern)                                │
│    - UpsertAnnotationCommand生成                                 │
│    - CommandBus.execute() でハンドラーに委譲                     │
└────────────────┬────────────────────────────────────────────────┘
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. Command Handler (Frontend)                                   │
│    - useRegisterAnnotationCommandHandlers.ts                    │
│    - Label Delta計算 (JSON-Patch形式)                           │
│    - Sample Delta計算                                            │
└────────────────┬────────────────────────────────────────────────┘
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ 4. HTTP API呼び出し                                              │
│    - patchSample() → PATCH /dataset/{id}/sample/{id}           │
│    - Content-Type: application/json-patch+json                  │
│    - If-Match: ETag (バージョン管理)                            │
└────────────────┬────────────────────────────────────────────────┘
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ 5. Backend API (Python/Starlette)                               │
│    - fiftyone/server/routes/sample.py                           │
│    - Sample.patch() エンドポイント                              │
│    - ETag検証、JSON-Patch適用                                   │
└────────────────┬────────────────────────────────────────────────┘
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ 6. Database保存 (MongoDB)                                        │
│    - sample.save() で MongoDBに永続化                           │
│    - last_modified_at更新                                        │
│    - 新しいETag生成                                              │
└────────────────┬────────────────────────────────────────────────┘
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ 7. Response返却                                                  │
│    - 更新後のsample JSON                                         │
│    - 新しいETag                                                  │
└────────────────┬────────────────────────────────────────────────┘
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ 8. Frontend State更新                                            │
│    - transformSampleData() でデータ変換                          │
│    - refreshSample() でRecoil state更新                         │
│    - EventBus発火 (annotation:upsertSuccess)                    │
└────────────────┬────────────────────────────────────────────────┘
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ 9. UI再描画                                                      │
│    - Scene2D.exitInteractiveMode()                               │
│    - addOverlay() で編集完了したオーバーレイを追加               │
│    - Notification表示 ("Label saved successfully")               │
└─────────────────────────────────────────────────────────────────┘
```

---

## 1. UI層: Saveボタンのクリック

### コンポーネント構造

**ファイル**: `source/app/packages/core/src/components/Modal/Sidebar/Annotate/Edit/Footer.tsx`

```tsx
const SaveFooter = () => {
  const onSave = useSave();  // カスタムフック
  const changes = useAtomValue(hasChanges);
  const saving = useAtomValue(isSavingAtom);

  return (
    <MuiButton
      disabled={!changes || saving}
      onClick={() => {
        onSave();  // ← ここでSave処理開始
      }}
      variant="contained"
      color="primary"
    >
      {saving ? <LoadingDots text={"Saving"} /> : "Save"}
    </MuiButton>
  );
};
```

### State管理

**変更検知**:
- `hasChanges` atom が編集前後のデータを `JSON.stringify` で比較
- `savedLabel` に最後に保存したデータを保持
- 変更がある場合のみSaveボタンが有効化

**ファイル**: `source/app/packages/core/src/components/Modal/Sidebar/Annotate/Edit/state.ts`

```typescript
export const hasChanges = atom((get) => {
  const label = get(currentData);
  const saved = get(savedLabel);

  return saved === null
    ? false
    : JSON.stringify(label) !== JSON.stringify(saved);
});
```

---

## 2. Command発行: UpsertAnnotationCommand

### useSave Hook

**ファイル**: `source/app/packages/core/src/components/Modal/Sidebar/Annotate/Edit/useSave.ts`

```typescript
export default function useSave() {
  const commandBus = useCommandBus();
  const label = useAtomValue(current);
  const schema = useRecoilValue(fos.fieldSchema({ space: fos.State.SPACE.SAMPLE }));
  const setSaving = useSetAtom(isSavingAtom);
  const exit = useExit(false);
  const setNotification = fos.useNotification();

  return useCallback(async () => {
    if (!label || isSaving) return;

    setSaving(true);

    try {
      const fieldSchema = getFieldSchema(schema, label.path);
      
      // Command発行
      await commandBus.execute(
        new UpsertAnnotationCommand({ ...label }, fieldSchema)
      );

      setter();
      
      // Sceneから編集モードを抜ける
      if (scene && !scene.isDestroyed && scene.renderLoopActive) {
        scene.exitInteractiveMode();
        addOverlay(label.overlay as BaseOverlay);
      }

      saved(label.data);
      setSaving(false);
      exit();
      
      setNotification({
        msg: `Label "${label.data.label}" saved successfully.`,
        variant: "success",
      });
    } catch (error) {
      setSaving(false);
      setNotification({
        msg: `Label "${label.data.label}" not saved successfully. Try again.`,
        variant: "error",
      });
    }
  }, [commandBus, label, ...]);
}
```

### Command定義

**ファイル**: `source/app/packages/annotation/src/commands.ts`

```typescript
/**
 * Command to upsert (create or update) an annotation label.
 */
export class UpsertAnnotationCommand extends Command<boolean> {
  constructor(
    public readonly label: AnnotationLabel,
    public readonly schema: Field
  ) {
    super();
  }
}
```

---

## 3. Command Handler: Delta計算とAPI呼び出し

### Handler登録

**ファイル**: `source/app/packages/annotation/src/hooks/useRegisterAnnotationCommandHandlers.ts`

Modalコンポーネントのマウント時に一度だけハンドラーを登録:

**ファイル**: `source/app/packages/core/src/components/Modal/Modal.tsx`

```tsx
const ModalCommandHandlersRegistration = () => {
  useRegisterAnnotationCommandHandlers();  // ← ここで登録
  return null;
};
```

### UpsertAnnotationCommandハンドラー

```typescript
useRegisterCommandHandler(
  UpsertAnnotationCommand,
  useCallback(
    async (cmd) => {
      const labelId = cmd.label.data._id;
      try {
        // 永続化処理を実行
        const success = await handlePersistence(
          cmd.label,
          cmd.schema,
          "mutate"  // 操作タイプ: "mutate" または "delete"
        );

        if (success) {
          eventBus.dispatch("annotation:upsertSuccess", {
            labelId,
            type: "upsert",
          });
        } else {
          eventBus.dispatch("annotation:upsertError", {
            labelId,
            type: "upsert",
          });
        }
        return success;
      } catch (error) {
        eventBus.dispatch("annotation:upsertError", {
          labelId,
          type: "upsert",
          error: error as Error,
        });
        throw error;
      }
    },
    [handlePersistence, eventBus]
  )
);
```

### Delta計算ロジック

**handlePersistence関数**:

```typescript
const handlePersistence = useRecoilCallback(
  ({ snapshot }) =>
    async (
      annotationLabel: AnnotationLabel,
      schema: Field,
      opType: OpType  // "mutate" or "delete"
    ): Promise<boolean> => {
      const currentSample = (await snapshot.getPromise(modalSample))?.sample;

      if (!currentSample) {
        console.error("missing sample data!");
        return false;
      }

      // Label deltaを計算 (JSON-Patch形式)
      const sampleDeltas = buildLabelDeltas(
        currentSample,
        annotationLabel,
        schema,
        opType
      ).map((delta) => ({
        ...delta,
        // label deltaをsample deltaに変換
        path: buildJsonPath(annotationLabel.path, delta.path),
      }));

      return await handlePatchSample(sampleDeltas);
    },
  [handlePatchSample]
);
```

**buildLabelDeltas関数** (`source/app/packages/annotation/src/deltas.ts`):
- 現在のsampleデータと新しいlabelデータを比較
- JSON-Patch形式の操作リストを生成
- 例: `{ op: "replace", path: "/detections/0/label", value: "car" }`

---

## 4. HTTP API呼び出し

### patchSample関数

**ファイル**: `source/app/packages/core/src/client/annotationClient.ts`

```typescript
export const patchSample = async (
  request: PatchSampleRequest
): Promise<PatchSampleResponse> => {
  const pathParts = ["dataset", request.datasetId, "sample", request.sampleId];
  
  if (request.path && request.labelId) {
    pathParts.push(request.path, request.labelId);
  }

  const response = await doFetch<JSONDeltas, Sample>({
    path: encodeURIPath(pathParts),
    method: "PATCH",
    body: request.deltas,  // JSON-Patch operations
    headers: {
      "Content-Type": "application/json-patch+json",
      "If-Match": `"${request.versionToken}"`,  // ETag for version control
    },
  });

  return {
    sample: response.response,
    versionToken: parseETag(response.headers.get("ETag")),
  };
};
```

### HTTPリクエスト例

```http
PATCH /dataset/65abc123def456/sample/65def789abc123 HTTP/1.1
Content-Type: application/json-patch+json
If-Match: "MjAyNi0wMS0xOVQxMDozMDoxNS4xMjM0NTY="

[
  {
    "op": "replace",
    "path": "/detections/0/label",
    "value": "vehicle.truck"
  },
  {
    "op": "replace",
    "path": "/detections/0/bounding_box",
    "value": [0.1, 0.2, 0.3, 0.4]
  }
]
```

---

## 5. Backend API: Python処理

### Starletteエンドポイント

**ファイル**: `source/fiftyone/server/routes/sample.py`

```python
class Sample(HTTPEndpoint):
    """Sample endpoints."""

    @decorators.route
    async def patch(self, request: Request, data: dict) -> dict:
        """Applies a list of field updates to a sample.

        Args:
            request: Starlette request with dataset_id and sample_id in path
            data: A list of JSON-Patch operations

        Returns:
            the final state of the sample as a dict
        """
        dataset_id = request.path_params["dataset_id"]
        sample_id = request.path_params["sample_id"]

        logger.info(
            "Received patch request for sample %s in dataset %s",
            sample_id,
            dataset_id,
        )

        # ETag検証
        if_last_modified_at = get_if_last_modified_at(request)
        if if_last_modified_at is None:
            raise HTTPException(
                status_code=400, detail="Invalid If-Match header"
            )

        # Sampleを取得
        sample = get_sample(dataset_id, sample_id, if_last_modified_at)

        # Content-Typeによって処理を分岐
        content_type = request.headers.get("Content-Type", "")
        ctype = content_type.split(";", 1)[0].strip().lower()
        
        if ctype == "application/json":
            self._handle_patch(sample, data)
        elif ctype == "application/json-patch+json":
            _handle_top_level_patch(sample, data)  # ← Annotation機能はこちら
        else:
            raise HTTPException(
                status_code=415, detail=f"Unsupported Content-Type '{ctype}'"
            )

        # 保存してETag生成
        etag = save_sample(sample, if_last_modified_at)

        return utils.json.JSONResponse(
            utils.json.serialize(sample), headers={"ETag": etag}
        )
```

### ETag検証とバージョン管理

**get_if_last_modified_at関数**:

```python
def get_if_last_modified_at(request: Request) -> Union[datetime.datetime, None]:
    """Parses the If-Match header from the request, if present, and returns
    the last modified date.
    """
    if_last_modified_at = None
    if request.headers.get("If-Match"):
        if_match, _ = utils.http.ETag.parse(request.headers["If-Match"])

        # ETagはlast_modified_atのbase64エンコード
        try:
            if_last_modified_at = datetime.datetime.fromisoformat(
                base64.b64decode(if_match.encode("utf-8")).decode("utf-8")
            )
        except Exception:
            ...

        # ISOフォーマットとしても試行
        try:
            if_last_modified_at = datetime.datetime.fromisoformat(if_match)
        except Exception:
            ...

        if if_last_modified_at is None:
            raise HTTPException(
                status_code=400, detail="Invalid If-Match header"
            )
            
    return if_last_modified_at
```

**get_sample関数** - バージョン競合チェック:

```python
def get_sample(
    dataset_id: str,
    sample_id: str,
    if_last_modified_at: Union[datetime.datetime, None],
) -> fo.Sample:
    """Retrieves a sample from a dataset."""
    
    dataset = get_dataset(dataset_id)
    sample = get_sample_from_dataset(dataset, sample_id)

    # バージョンチェック: 別のユーザーが編集していた場合は412エラー
    if if_last_modified_at is not None:
        if not datetimes_match(sample.last_modified_at, if_last_modified_at):
            logger.debug(
                "If-Match condition failed for sample %s: %s != %s",
                sample.id,
                sample.last_modified_at,
                if_last_modified_at,
            )
            raise HTTPException(
                status_code=412, detail="If-Match condition failed"
            )

    return sample
```

---

## 6. Database保存 (MongoDB)

### save_sample関数

**save_sample実装** (推定):

```python
def save_sample(sample: fo.Sample, if_last_modified_at: datetime.datetime) -> str:
    """Saves the sample to MongoDB and generates a new ETag."""
    
    # MongoDBに保存 (FiftyOneのORM経由)
    sample.save()
    
    # 新しいETagを生成
    return generate_sample_etag(sample)
```

### MongoDB操作

FiftyOneは内部的にMongoDBを使用:

1. **sample.save()** が呼ばれると:
   - `last_modified_at` フィールドが現在時刻に更新
   - MongoDBの `samples` コレクションに `updateOne()` or `replaceOne()` 実行
   - ドキュメント全体が永続化

2. **インデックス**:
   - `_id` (サンプルID)
   - `last_modified_at` (バージョン管理用)

### データ形式例

MongoDB に保存されるサンプルドキュメント:

```json
{
  "_id": ObjectId("65def789abc123"),
  "_dataset_id": ObjectId("65abc123def456"),
  "filepath": "/data/image001.jpg",
  "last_modified_at": ISODate("2026-01-19T10:35:22.456Z"),
  "detections": {
    "_cls": "Detections",
    "detections": [
      {
        "_id": ObjectId("65abc456def789"),
        "_cls": "Detection",
        "label": "vehicle.truck",
        "bounding_box": [0.1, 0.2, 0.3, 0.4],
        "confidence": 0.95
      }
    ]
  }
}
```

---

## 7. Response返却

### レスポンス形式

```json
HTTP/1.1 200 OK
Content-Type: application/json
ETag: "MjAyNi0wMS0xOVQxMDozNToyMi40NTY3ODk="

{
  "_id": "65def789abc123",
  "filepath": "/data/image001.jpg",
  "last_modified_at": "2026-01-19T10:35:22.456789",
  "detections": {
    "_cls": "Detections",
    "detections": [
      {
        "_id": "65abc456def789",
        "_cls": "Detection",
        "label": "vehicle.truck",
        "bounding_box": [0.1, 0.2, 0.3, 0.4],
        "confidence": 0.95
      }
    ]
  }
}
```

### ETa生成

```python
def generate_sample_etag(sample: fo.Sample) -> str:
    """Generates an ETag for a sample based on its last modified date."""
    value = base64.b64encode(
        sample.last_modified_at.isoformat().encode("utf-8")
    ).decode("utf-8")

    return utils.http.ETag.create(value)
```

---

## 8. Frontend State更新

### レスポンス処理

**handlePatchSample関数**:

```typescript
const handlePatchSample = useRecoilCallback(
  ({ snapshot }) =>
    async (sampleDeltas: JSONDeltas): Promise<boolean> => {
      const currentSample = (await snapshot.getPromise(modalSample))?.sample;
      const versionToken = await getVersionToken();

      if (!datasetId || !currentSample?._id || !versionToken) {
        return false;
      }

      if (sampleDeltas.length > 0) {
        try {
          const response = await patchSample({
            datasetId,
            sampleId: currentSample._id,
            deltas: sampleDeltas,
            versionToken,
          });

          // レスポンスデータをGraphQL形式に変換
          const cleanedSample = transformSampleData(response.sample);
          
          if (isSampleIsh(cleanedSample)) {
            // Recoil stateを更新
            refreshSample(cleanedSample as Sample);
          } else {
            console.error(
              "response data does not adhere to sample format",
              cleanedSample
            );
          }
        } catch (error) {
          console.error("error patching sample", error);
          return false;
        }
      }

      return true;
    },
  [datasetId, refreshSample, getVersionToken]
);
```

### State更新フロー

1. **transformSampleData**: バックエンドのJSON形式をGraphQLスキーマ互換形式に変換
2. **refreshSample**: 
   - Recoilの `modalSample` atomを更新
   - Lookerコンポーネントに変更を伝播
   - サイドバーの表示を更新

---

## 9. UI再描画

### Scene2Dの更新

**useSave内**:

```typescript
if (scene && !scene.isDestroyed && scene.renderLoopActive) {
  scene.exitInteractiveMode();  // 編集モードを終了
  addOverlay(label.overlay as BaseOverlay);  // 編集完了したオーバーレイを追加
}
```

### Scene2D.exitInteractiveMode()

**ファイル**: `source/app/packages/lighter/src/core/Scene2D.ts`

```typescript
exitInteractiveMode(): void {
  this.interactiveMode = false;
  this.interactiveOverlay = null;
  
  // カーソルをデフォルトに戻す
  this.container.style.cursor = "default";
  
  // 再描画
  this.render();
}
```

### Overlay追加

**addOverlay関数**:

編集が完了したオーバーレイ（BoundingBox、Polylineなど）を通常の表示レイヤーに追加:

```typescript
addOverlay(overlay: BaseOverlay): void {
  this.overlays.set(overlay.id, overlay);
  overlay.addToScene(this.pixiApp.stage);
  this.render();
}
```

### Notification表示

**useNotification**:

```typescript
setNotification({
  msg: `Label "${label.data.label}" saved successfully.`,
  variant: "success",  // or "error"
});
```

画面右下にスナックバー通知が表示される。

---

## エラーハンドリング

### 1. バージョン競合 (412 Precondition Failed)

別のユーザーが同じサンプルを先に編集していた場合:

```typescript
catch (error) {
  if (error.status === 412) {
    setNotification({
      msg: "Sample was modified by another user. Please refresh and try again.",
      variant: "error",
    });
  }
}
```

### 2. ネットワークエラー

```typescript
catch (error) {
  console.error("error patching sample", error);
  setNotification({
    msg: `Label "${label.data.label}" not saved successfully. Try again.`,
    variant: "error",
  });
}
```

### 3. バリデーションエラー

バックエンドでのデータ検証失敗時は400エラー:

```python
if errors:
    raise HTTPException(status_code=400, detail=errors)
```

---

## データフロー詳細例: Detectionの編集

### 初期状態

```json
{
  "detections": {
    "detections": [
      {
        "_id": "65abc456def789",
        "label": "vehicle.car",
        "bounding_box": [0.1, 0.2, 0.3, 0.4],
        "confidence": 0.85
      }
    ]
  }
}
```

### ユーザー編集

サイドバーで `label` を `"vehicle.car"` → `"vehicle.truck"` に変更

### 生成されるJSON-Patch

```json
[
  {
    "op": "replace",
    "path": "/detections/detections/0/label",
    "value": "vehicle.truck"
  }
]
```

### MongoDBへの適用

FiftyOne ORMが内部的にMongoDBクエリを生成:

```javascript
db.samples.updateOne(
  { _id: ObjectId("65def789abc123") },
  { 
    $set: { 
      "detections.detections.0.label": "vehicle.truck",
      "last_modified_at": new Date()
    } 
  }
)
```

### レスポンス

```json
{
  "_id": "65def789abc123",
  "detections": {
    "detections": [
      {
        "_id": "65abc456def789",
        "label": "vehicle.truck",  // ← 更新済み
        "bounding_box": [0.1, 0.2, 0.3, 0.4],
        "confidence": 0.85
      }
    ]
  },
  "last_modified_at": "2026-01-19T10:40:15.123456"
}
```

### UI更新

1. Recoil `modalSample` が新しいデータで更新
2. サイドバーのDetectionコンポーネントが再レンダリング
3. Lookerキャンバス上のBoundingBoxラベルが "vehicle.truck" に変更
4. 成功通知が表示

---

## パフォーマンス最適化

### 1. Optimistic Update

現在の実装では**悲観的更新**（サーバーレスポンス待ち）ですが、楽観的更新も可能:

```typescript
// 1. UI を即座に更新
setLocalLabel(newLabel);

// 2. バックグラウンドで保存
try {
  await commandBus.execute(new UpsertAnnotationCommand(newLabel, schema));
} catch (error) {
  // 3. エラー時はロールバック
  setLocalLabel(originalLabel);
  showError();
}
```

### 2. Debounce

連続編集時に不要なリクエストを防ぐ:

```typescript
const debouncedSave = useMemo(
  () => debounce(onSave, 500),
  [onSave]
);
```

### 3. ETag Cache

ETagをキャッシュして不要なバージョンチェックを削減:

```typescript
const cachedETag = useRef<string | null>(null);

if (cachedETag.current === responseETag) {
  // スキップ
}
```

---

## セキュリティ

### 1. ETag による楽観的ロック

- 同時編集時の競合を検出
- Last-Win戦略ではなくFirst-Win（先勝ち）
- ユーザーに明示的なエラー通知

### 2. Read-Onlyモード

```typescript
const readOnly = useRecoilValue(readOnly$1);

if (readOnly) {
  return <DisabledSaveButton />;
}
```

### 3. 権限チェック

```typescript
function useMutation(action: string, config?: Config) {
  const readOnly = useRecoilValue(readOnly$1);
  return canPerformAction(action, readOnly, config);
}
```

---

## まとめ

### 処理の流れ

1. **Saveボタンクリック** → useSave hook
2. **Command発行** → UpsertAnnotationCommand
3. **Command Handler** → Delta計算
4. **HTTP PATCH** → /dataset/{id}/sample/{id}
5. **Backend処理** → ETag検証、JSON-Patch適用
6. **MongoDB保存** → sample.save()
7. **Response** → 更新後データ + 新ETag
8. **State更新** → Recoil refreshSample
9. **UI再描画** → Scene2D、Notification

### 主要な技術要素

- **Command Pattern**: UI操作とビジネスロジックの分離
- **JSON-Patch (RFC 6902)**: 差分ベースの更新
- **ETag**: 楽観的ロック（バージョン管理）
- **Recoil**: Reactの状態管理
- **Jotai**: 軽量な状態管理（補助）
- **Scene2D (Lighter)**: 2Dレンダリングエンジン
- **FiftyOne ORM**: MongoDB抽象化層

### データ永続化の保証

- MongoDBのACIDトランザクション
- last_modified_at による変更追跡
- ETagによる競合検出
- エラー時の適切なロールバック

---

## 参考ファイル一覧

### Frontend (TypeScript/React)

1. `source/app/packages/core/src/components/Modal/Sidebar/Annotate/Edit/Footer.tsx` - Saveボタン UI
2. `source/app/packages/core/src/components/Modal/Sidebar/Annotate/Edit/useSave.ts` - Save hook
3. `source/app/packages/annotation/src/commands.ts` - Command定義
4. `source/app/packages/annotation/src/hooks/useRegisterAnnotationCommandHandlers.ts` - Command handlers
5. `source/app/packages/annotation/src/deltas.ts` - JSON-Patch生成
6. `source/app/packages/core/src/client/annotationClient.ts` - HTTP API
7. `source/app/packages/lighter/src/core/Scene2D.ts` - 2Dレンダリング

### Backend (Python)

1. `source/fiftyone/server/routes/sample.py` - Starlette endpoints
2. `source/fiftyone/core/sample.py` - FiftyOne Sample ORM
3. `source/fiftyone/core/odm/database.py` - MongoDB connection

### 設定

1. `source/app/packages/command-bus/` - Command Bus infrastructure
2. `source/app/packages/state/` - Recoil state atoms

---

以上が、FiftyOne Annotation機能におけるSave処理の詳細フローです。

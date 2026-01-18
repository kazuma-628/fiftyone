# FiftyOne Annotation プログラム的更新設計書

## 概要

FiftyOneのAnnotation編集モード（UIサイドバー）を使わず、プログラムからアノテーションデータを更新してDBに保存し、UI上で再描画する方法を解説します。

既存のAnnotation機能のインフラストラクチャ（Command Bus、Delta計算、HTTP API、State管理）を最大限活用することで、**最小限のコードで信頼性の高い更新処理**を実装できます。

---

## ユースケース例

### 1. 座標変換処理

カメラキャリブレーション情報を元に、すべての2D bounding boxの座標を変換:

```typescript
// 例: すべてのdetectionを10%拡大
detections.forEach(det => {
  const [x, y, w, h] = det.bounding_box;
  det.bounding_box = [x - w*0.05, y - h*0.05, w*1.1, h*1.1];
});
```

### 2. バッチラベル変更

特定条件に合致するラベルを一括変更:

```typescript
// 例: confidence < 0.5 のdetectionをすべて "uncertain" に変更
detections.forEach(det => {
  if (det.confidence < 0.5) {
    det.label = "uncertain";
  }
});
```

### 3. 外部ツール連携

Python処理結果をフロントエンドに反映:

```typescript
// 例: Pythonで計算した新しいpolyline座標を適用
const newPolylines = await fetchFromBackend('/api/transform-polylines');
sample.cuboids.polylines = newPolylines;
```

---

## アーキテクチャ: 流用可能な既存機能

### 全体図

```
┌──────────────────────────────────────────────────────────────┐
│ あなたのカスタムコード                                         │
│  - 座標変換ロジック                                           │
│  - バッチ処理ロジック                                         │
│  - 外部API連携                                                │
└─────────────┬────────────────────────────────────────────────┘
              ▼
┌──────────────────────────────────────────────────────────────┐
│ 【流用】カスタムフック (useUpdateAnnotation)                  │
│  - UpsertAnnotationCommand生成                                │
│  - CommandBus.execute()                                       │
└─────────────┬────────────────────────────────────────────────┘
              ▼
┌──────────────────────────────────────────────────────────────┐
│ 【流用】既存Command Handler                                   │
│  - useRegisterAnnotationCommandHandlers                       │
│  - buildLabelDeltas (JSON-Patch生成)                         │
│  - handlePatchSample (HTTP API呼び出し)                      │
└─────────────┬────────────────────────────────────────────────┘
              ▼
┌──────────────────────────────────────────────────────────────┐
│ 【流用】既存Backend API                                       │
│  - PATCH /dataset/{id}/sample/{id}                          │
│  - ETag検証、MongoDB保存                                     │
└─────────────┬────────────────────────────────────────────────┘
              ▼
┌──────────────────────────────────────────────────────────────┐
│ 【流用】既存State管理 & UI更新                                │
│  - refreshSample (Recoil state更新)                         │
│  - Lookerの自動再描画                                         │
│  - EventBus通知 (annotation:upsertSuccess)                   │
└──────────────────────────────────────────────────────────────┘
```

---

## 流用度マトリクス

| 機能 | 流用度 | 必要な実装 | 備考 |
|------|--------|-----------|------|
| **Command Bus** | ✅ 100% | なし | `useCommandBus()` をそのまま使用 |
| **Command定義** | ✅ 100% | なし | `UpsertAnnotationCommand` を再利用 |
| **Command Handler** | ✅ 100% | なし | 既に登録済み（Modal起動時） |
| **JSON-Patch生成** | ⚠️ 50% | Delta計算ロジック | `buildLabelDeltas` は再利用可能だが、手動構築も可 |
| **HTTP API** | ✅ 100% | なし | `patchSample()` をそのまま使用 |
| **State更新** | ✅ 100% | なし | `refreshSample` が自動実行 |
| **UI再描画** | ✅ 100% | なし | Recoil subscriptionで自動 |
| **エラーハンドリング** | ✅ 90% | try-catch追加 | 既存の仕組みを拡張 |

**結論**: **約90%の既存インフラが流用可能**。カスタムロジック部分のみ実装すればよい。

---

## 実装パターン

### パターン1: Command Busを使う（推奨）

既存のCommand Busインフラをそのまま活用。最も安全で保守性が高い。

#### 実装例: カスタムフック

```typescript
/**
 * プログラム的にアノテーションを更新するカスタムフック
 * 
 * @example
 * const updateAnnotation = useUpdateAnnotation();
 * 
 * // Detection座標を変更
 * await updateAnnotation({
 *   field: "detections",
 *   labelId: "65abc456def789",
 *   updates: {
 *     bounding_box: [0.2, 0.3, 0.4, 0.5]
 *   }
 * });
 */
export function useUpdateAnnotation() {
  const commandBus = useCommandBus();
  const schema = useRecoilValue(fos.fieldSchema({ space: fos.State.SPACE.SAMPLE }));
  const sample = useRecoilValue(fos.modalSample)?.sample;
  const setNotification = fos.useNotification();

  return useCallback(async ({
    field,
    labelId,
    updates,
    suppressNotification = false
  }: UpdateAnnotationParams) => {
    if (!sample) {
      throw new Error("No sample loaded");
    }

    try {
      // 1. 現在のラベルを取得
      const currentLabel = findLabelInSample(sample, field, labelId);
      if (!currentLabel) {
        throw new Error(`Label ${labelId} not found in field ${field}`);
      }

      // 2. 更新されたラベルを作成
      const updatedLabel: AnnotationLabel = {
        ...currentLabel,
        data: {
          ...currentLabel.data,
          ...updates
        },
        path: field,
        overlay: null, // プログラム的更新ではオーバーレイ不要
      };

      // 3. Field schemaを取得
      const fieldSchema = getFieldSchema(schema, field);
      if (!fieldSchema) {
        throw new Error(`Field schema not found for ${field}`);
      }

      // 4. Command発行 (既存ハンドラーが自動実行される)
      await commandBus.execute(
        new UpsertAnnotationCommand(updatedLabel, fieldSchema)
      );

      // 5. 成功通知
      if (!suppressNotification) {
        setNotification({
          msg: `Annotation updated successfully`,
          variant: "success",
        });
      }

      return true;
    } catch (error) {
      console.error("Failed to update annotation:", error);
      
      if (!suppressNotification) {
        setNotification({
          msg: `Failed to update annotation: ${error.message}`,
          variant: "error",
        });
      }

      throw error;
    }
  }, [commandBus, schema, sample, setNotification]);
}
```

#### 型定義

```typescript
interface UpdateAnnotationParams {
  /** アノテーションフィールド名 (例: "detections", "cuboids") */
  field: string;
  
  /** 更新対象のラベルID */
  labelId: string;
  
  /** 更新する属性 */
  updates: Partial<{
    label: string;
    bounding_box: [number, number, number, number];
    confidence: number;
    polylines: Array<Array<[number, number]>>;
    // ... その他のLabel属性
  }>;
  
  /** 通知を非表示にするか */
  suppressNotification?: boolean;
}
```

#### 使用例1: 単一ラベルの更新

```typescript
function MyCustomComponent() {
  const updateAnnotation = useUpdateAnnotation();

  const handleTransformCoordinates = async () => {
    await updateAnnotation({
      field: "detections",
      labelId: "65abc456def789",
      updates: {
        bounding_box: [0.15, 0.25, 0.35, 0.45],
        confidence: 0.92
      }
    });
  };

  return (
    <Button onClick={handleTransformCoordinates}>
      Transform Coordinates
    </Button>
  );
}
```

#### 使用例2: バッチ更新

```typescript
function BatchUpdateComponent() {
  const updateAnnotation = useUpdateAnnotation();
  const sample = useRecoilValue(fos.modalSample)?.sample;

  const handleBatchUpdate = async () => {
    if (!sample?.detections?.detections) return;

    // すべてのdetectionを処理
    for (const detection of sample.detections.detections) {
      // 条件に合致するものだけ更新
      if (detection.confidence < 0.5) {
        await updateAnnotation({
          field: "detections",
          labelId: detection._id,
          updates: {
            label: "uncertain",
            confidence: detection.confidence * 0.8
          },
          suppressNotification: true // バッチ処理中は通知を抑制
        });
      }
    }

    // 完了通知
    setNotification({
      msg: `Updated ${count} detections`,
      variant: "success"
    });
  };

  return (
    <Button onClick={handleBatchUpdate}>
      Mark Low Confidence as Uncertain
    </Button>
  );
}
```

---

### パターン2: HTTP APIを直接使う（中級者向け）

Command Busを経由せず、直接HTTP APIを呼び出す。より低レベルな制御が可能。

#### 実装例: カスタムフック

```typescript
/**
 * HTTP APIを直接使用してアノテーションを更新
 * 
 * Command Busをバイパスするため、以下に注意:
 * - EventBusイベントは手動で発火
 * - State更新も手動で実行
 */
export function useDirectPatchSample() {
  const datasetId = useRecoilValue(fos.datasetId);
  const sampleId = useRecoilValue(fos.modalSampleId);
  const refreshSample = fos.useRefreshSample();
  const setNotification = fos.useNotification();

  return useCallback(async (deltas: JSONDeltas) => {
    if (!datasetId || !sampleId) {
      throw new Error("Dataset or sample not loaded");
    }

    try {
      // 1. 現在のサンプルを取得してバージョントークンを確認
      const currentSample = await getCurrentSample();
      const versionToken = getVersionTokenFromSample(currentSample);

      // 2. PATCH API呼び出し
      const response = await patchSample({
        datasetId,
        sampleId,
        deltas,
        versionToken,
      });

      // 3. State更新（手動）
      const cleanedSample = transformSampleData(response.sample);
      refreshSample(cleanedSample);

      // 4. EventBus発火（手動）
      selectiveRenderingEventBus.emit(
        new CustomEvent("annotation:upsertSuccess", {
          detail: { labelId: "custom", type: "upsert" }
        })
      );

      setNotification({
        msg: "Sample updated successfully",
        variant: "success"
      });

      return response.sample;
    } catch (error) {
      console.error("Failed to patch sample:", error);
      
      setNotification({
        msg: `Failed to update sample: ${error.message}`,
        variant: "error"
      });

      throw error;
    }
  }, [datasetId, sampleId, refreshSample, setNotification]);
}
```

#### 使用例: 座標変換

```typescript
function CoordinateTransformComponent() {
  const patchSample = useDirectPatchSample();

  const handleTransform = async () => {
    // JSON-Patchを手動構築
    const deltas: JSONDeltas = [
      {
        op: "replace",
        path: "/detections/detections/0/bounding_box",
        value: [0.1, 0.2, 0.3, 0.4]
      },
      {
        op: "replace",
        path: "/detections/detections/0/label",
        value: "transformed"
      }
    ];

    await patchSample(deltas);
  };

  return <Button onClick={handleTransform}>Transform</Button>;
}
```

---

### パターン3: Pythonバックエンドから更新（サーバーサイド）

フロントエンドを経由せず、Pythonから直接MongoDB更新。高速だがUI同期に注意。

#### 実装例: Python

```python
import fiftyone as fo
from datetime import datetime

def update_annotations_batch(dataset_name: str, transform_fn):
    """
    アノテーションをバッチ更新
    
    Args:
        dataset_name: データセット名
        transform_fn: 変換関数 (sample -> None)
    """
    dataset = fo.load_dataset(dataset_name)
    
    for sample in dataset:
        # カスタム変換を適用
        transform_fn(sample)
        
        # last_modified_atを更新（ETag整合性のため）
        sample.last_modified_at = datetime.utcnow()
        
        # MongoDB保存
        sample.save()
    
    print(f"Updated {len(dataset)} samples")

# 使用例: すべてのdetectionを10%拡大
def expand_bounding_boxes(sample):
    if not sample.detections:
        return
    
    for det in sample.detections.detections:
        x, y, w, h = det.bounding_box
        det.bounding_box = [
            max(0, x - w * 0.05),
            max(0, y - h * 0.05),
            min(1, w * 1.1),
            min(1, h * 1.1)
        ]

update_annotations_batch("nuscenes_annotations", expand_bounding_boxes)
```

#### フロントエンドでの同期

Python更新後、フロントエンドで再読み込み:

```typescript
function ReloadButton() {
  const refreshSample = fos.useRefreshSample();

  const handleReload = async () => {
    // サーバーから最新データを取得
    await refreshSample();
    
    setNotification({
      msg: "Reloaded latest data from server",
      variant: "info"
    });
  };

  return <Button onClick={handleReload}>Reload</Button>;
}
```

---

## JSON-Patch生成: 既存ロジックの活用

### 方法1: buildLabelDeltasを使う（推奨）

**ファイル**: `source/app/packages/annotation/src/deltas.ts`

```typescript
import { buildLabelDeltas } from "@fiftyone/annotation";

function MyComponent() {
  const sample = useRecoilValue(fos.modalSample)?.sample;
  const schema = useRecoilValue(fos.fieldSchema({ space: fos.State.SPACE.SAMPLE }));

  const generateDeltas = () => {
    // 新しいラベルデータを作成
    const updatedLabel: AnnotationLabel = {
      data: {
        _id: "65abc456def789",
        label: "vehicle.truck",  // 変更
        bounding_box: [0.2, 0.3, 0.4, 0.5],  // 変更
      },
      path: "detections",
      // ... その他
    };

    // JSON-Patch自動生成
    const labelDeltas = buildLabelDeltas(
      sample,
      updatedLabel,
      schema.detections,
      "mutate"  // "mutate" or "delete"
    );

    // Label deltaをSample deltaに変換
    const sampleDeltas = labelDeltas.map(delta => ({
      ...delta,
      path: buildJsonPath("detections", delta.path)
    }));

    return sampleDeltas;
  };

  return <Button onClick={() => console.log(generateDeltas())}>
    Generate Deltas
  </Button>;
}
```

### 方法2: 手動でJSON-Patchを構築

シンプルな更新の場合は手動構築も簡単:

```typescript
function generateSimpleDeltas() {
  return [
    {
      op: "replace",
      path: "/detections/detections/0/label",
      value: "vehicle.truck"
    },
    {
      op: "replace",
      path: "/detections/detections/0/bounding_box",
      value: [0.2, 0.3, 0.4, 0.5]
    }
  ] as JSONDeltas;
}
```

---

## UI再描画の仕組み: 自動実行される処理

### なぜ自動で再描画されるのか？

FiftyOneは **Recoil subscriptionパターン** を採用しているため、State更新時にUIが自動的に再レンダリングされます。

#### フロー

```
patchSample成功
  ↓
handlePatchSample内でrefreshSample()呼び出し
  ↓
Recoil atom (modalSample) が更新
  ↓
useRecoilValue(modalSample) を使っているコンポーネントが再レンダリング
  ↓
Lookerコンポーネントが新しいsampleデータを受け取る
  ↓
Looker.updateSample() → loadOverlays()
  ↓
Scene2D.render() → Canvas再描画
```

### 関連コンポーネント

**ファイル**: `source/app/packages/core/src/components/Modal/Modal.tsx`

```tsx
function ModalLooker() {
  const sample = useRecoilValue(fos.modalSample)?.sample;
  const lookerRef = useRef<AbstractLooker>();

  useEffect(() => {
    if (sample && lookerRef.current) {
      // sampleが更新されたら自動的にLookerを更新
      lookerRef.current.updateSample(sample);
    }
  }, [sample]);

  return <div ref={lookerElement} />;
}
```

### カスタムコードで特別な処理は不要

既存のRecoil atomを更新するだけで、以下が自動実行されます:

1. ✅ Lookerの再描画
2. ✅ サイドバーの更新
3. ✅ Thumbnailの更新
4. ✅ オーバーレイの再計算
5. ✅ Taggingの反映

**結論**: `refreshSample()` または `commandBus.execute()` を呼ぶだけでOK。

---

## エラーハンドリング

### 既存の仕組みを活用

Command Handlerは既にエラーハンドリングを実装しているため、カスタムコードでは **try-catch** を追加するだけ:

```typescript
export function useUpdateAnnotation() {
  const commandBus = useCommandBus();
  const setNotification = fos.useNotification();

  return useCallback(async (params) => {
    try {
      await commandBus.execute(
        new UpsertAnnotationCommand(...)
      );

      setNotification({
        msg: "Success",
        variant: "success"
      });

      return true;
    } catch (error) {
      // エラーハンドリング
      if (error.status === 412) {
        setNotification({
          msg: "Sample was modified by another user. Reload and try again.",
          variant: "error"
        });
      } else {
        setNotification({
          msg: `Failed: ${error.message}`,
          variant: "error"
        });
      }

      throw error;
    }
  }, [commandBus, setNotification]);
}
```

### エラー種別

| HTTPステータス | 意味 | 対処法 |
|--------------|------|--------|
| **400** | バリデーションエラー | データ形式を確認 |
| **404** | Sample not found | サンプルIDを確認 |
| **412** | ETag conflict (バージョン競合) | リロードして再試行 |
| **500** | サーバーエラー | ログ確認、管理者に連絡 |

---

## ヘルパー関数

### findLabelInSample

Sampleから特定のラベルを検索:

```typescript
/**
 * Sampleから指定されたフィールドとIDのラベルを取得
 */
function findLabelInSample(
  sample: Sample,
  field: string,
  labelId: string
): Label | null {
  const fieldData = sample[field];
  
  if (!fieldData) return null;

  // Detectionsの場合
  if (fieldData._cls === "Detections") {
    return fieldData.detections.find(d => d._id === labelId) || null;
  }

  // Polylinesの場合
  if (fieldData._cls === "Polylines") {
    return fieldData.polylines.find(p => p._id === labelId) || null;
  }

  // 単一ラベルの場合
  if (fieldData._id === labelId) {
    return fieldData;
  }

  return null;
}
```

### getVersionTokenFromSample

Sampleからバージョントークン（ETag）を抽出:

```typescript
/**
 * SampleのlastModifiedAtからバージョントークンを生成
 */
function getVersionTokenFromSample(sample: Sample): string {
  if (!sample.last_modified_at) {
    throw new Error("Sample missing last_modified_at");
  }

  // ISO形式の文字列をbase64エンコード
  return btoa(sample.last_modified_at);
}
```

### buildJsonPath

Label pathをSample pathに変換:

```typescript
/**
 * ラベルフィールド内のパスをサンプルルートからのパスに変換
 * 
 * @example
 * buildJsonPath("detections", "/detections/0/label")
 * // => "/detections/detections/0/label"
 */
export function buildJsonPath(
  labelPath: string,
  operationPath: string
): string {
  const parts = labelPath.split(".");
  parts.push(
    ...operationPath.split("/").filter(p => p !== "")
  );
  return "/" + parts.join("/");
}
```

---

## 実装チェックリスト

### 最小限の実装（パターン1）

- [ ] `useUpdateAnnotation` カスタムフック作成
- [ ] `findLabelInSample` ヘルパー実装
- [ ] エラーハンドリング（try-catch）
- [ ] 通知表示（成功/失敗）

### フル実装（パターン2）

- [ ] `useDirectPatchSample` カスタムフック作成
- [ ] JSON-Patch手動生成ロジック
- [ ] `refreshSample` 手動呼び出し
- [ ] EventBus手動発火
- [ ] バージョン管理（ETag）

### Pythonバックエンド連携（パターン3）

- [ ] Pythonバッチ処理スクリプト
- [ ] `last_modified_at` 更新処理
- [ ] フロントエンド再読み込みボタン
- [ ] WebSocket通知（オプション）

---

## パフォーマンス最適化

### 1. バッチ処理の最適化

複数ラベルを更新する場合、1つのHTTPリクエストにまとめる:

```typescript
async function batchUpdateLabels(updates: Array<UpdateAnnotationParams>) {
  // すべての更新をJSON-Patchにまとめる
  const allDeltas: JSONDeltas = [];
  
  for (const update of updates) {
    const deltas = generateDeltasForUpdate(update);
    allDeltas.push(...deltas);
  }

  // 1回のHTTPリクエストで送信
  await patchSample({
    datasetId,
    sampleId,
    deltas: allDeltas,
    versionToken
  });
}
```

### 2. Debounce

連続更新時の負荷を軽減:

```typescript
const debouncedUpdate = useMemo(
  () => debounce(updateAnnotation, 300),
  [updateAnnotation]
);

// 使用例
onSliderChange(value => {
  debouncedUpdate({ labelId, updates: { confidence: value } });
});
```

### 3. Optimistic UI Update

レスポンス待ちせず即座にUIを更新:

```typescript
async function optimisticUpdate(params: UpdateAnnotationParams) {
  // 1. UIを即座に更新
  const rollback = updateLocalState(params);

  try {
    // 2. バックエンドに保存
    await commandBus.execute(new UpsertAnnotationCommand(...));
  } catch (error) {
    // 3. エラー時はロールバック
    rollback();
    throw error;
  }
}
```

---

## セキュリティ・権限チェック

### Read-Onlyモードの確認

```typescript
export function useUpdateAnnotation() {
  const readOnly = useRecoilValue(fos.readOnly);
  const commandBus = useCommandBus();

  return useCallback(async (params) => {
    // Read-Onlyモードではエラー
    if (readOnly) {
      throw new Error("Cannot update annotations in read-only mode");
    }

    await commandBus.execute(
      new UpsertAnnotationCommand(...)
    );
  }, [readOnly, commandBus]);
}
```

### useMutation活用

FiftyOne標準の権限チェック:

```typescript
function MyComponent() {
  const canUpdate = fos.useMutation("update_annotations");

  if (!canUpdate) {
    return <div>You don't have permission to update annotations</div>;
  }

  return <UpdateButton />;
}
```

---

## テスト例

### ユニットテスト

```typescript
import { renderHook } from "@testing-library/react";
import { useUpdateAnnotation } from "./useUpdateAnnotation";

describe("useUpdateAnnotation", () => {
  it("should update annotation successfully", async () => {
    const { result } = renderHook(() => useUpdateAnnotation());

    await result.current({
      field: "detections",
      labelId: "test-id",
      updates: {
        label: "new-label"
      }
    });

    // CommandBusがUpsertAnnotationCommandを実行したか確認
    expect(mockCommandBus.execute).toHaveBeenCalledWith(
      expect.objectContaining({
        label: expect.objectContaining({
          data: expect.objectContaining({
            label: "new-label"
          })
        })
      })
    );
  });

  it("should handle errors", async () => {
    const { result } = renderHook(() => useUpdateAnnotation());

    mockCommandBus.execute.mockRejectedValue(new Error("Network error"));

    await expect(
      result.current({ field: "detections", labelId: "test-id", updates: {} })
    ).rejects.toThrow("Network error");
  });
});
```

---

## 実例: 座標変換の完全フロー

### シナリオ

カメラキャリブレーション補正により、すべてのdetectionのbounding boxを変換する。

### Step 1: 変換ロジック

```typescript
/**
 * カメラキャリブレーション行列を適用してbounding boxを変換
 */
function transformBoundingBox(
  bbox: [number, number, number, number],
  calibMatrix: number[][]
): [number, number, number, number] {
  const [x, y, w, h] = bbox;
  
  // 4隅の座標を計算
  const corners = [
    [x, y],
    [x + w, y],
    [x + w, y + h],
    [x, y + h]
  ];

  // 行列変換を適用
  const transformedCorners = corners.map(([px, py]) => {
    const tx = calibMatrix[0][0] * px + calibMatrix[0][1] * py + calibMatrix[0][2];
    const ty = calibMatrix[1][0] * px + calibMatrix[1][1] * py + calibMatrix[1][2];
    return [tx, ty];
  });

  // 変換後の境界ボックスを計算
  const xs = transformedCorners.map(c => c[0]);
  const ys = transformedCorners.map(c => c[1]);
  
  const newX = Math.min(...xs);
  const newY = Math.min(...ys);
  const newW = Math.max(...xs) - newX;
  const newH = Math.max(...ys) - newY;

  return [newX, newY, newW, newH];
}
```

### Step 2: カスタムコンポーネント

```typescript
function CameraCalibrationTool() {
  const updateAnnotation = useUpdateAnnotation();
  const sample = useRecoilValue(fos.modalSample)?.sample;
  const [calibMatrix, setCalibMatrix] = useState<number[][]>([
    [1.05, 0, 0],
    [0, 1.05, 0],
    [0, 0, 1]
  ]);
  const [processing, setProcessing] = useState(false);

  const handleApplyCalibration = async () => {
    if (!sample?.detections?.detections) {
      alert("No detections found");
      return;
    }

    setProcessing(true);

    try {
      // すべてのdetectionに変換を適用
      for (const detection of sample.detections.detections) {
        const transformedBbox = transformBoundingBox(
          detection.bounding_box,
          calibMatrix
        );

        // 個別に更新
        await updateAnnotation({
          field: "detections",
          labelId: detection._id,
          updates: {
            bounding_box: transformedBbox
          },
          suppressNotification: true  // バッチ処理中は通知抑制
        });
      }

      // 完了通知
      setNotification({
        msg: `Transformed ${sample.detections.detections.length} detections`,
        variant: "success"
      });
    } catch (error) {
      setNotification({
        msg: `Transformation failed: ${error.message}`,
        variant: "error"
      });
    } finally {
      setProcessing(false);
    }
  };

  return (
    <Box>
      <Typography variant="h6">Camera Calibration</Typography>
      
      {/* キャリブレーション行列入力 */}
      <CalibrationMatrixInput 
        value={calibMatrix} 
        onChange={setCalibMatrix} 
      />

      <Button
        onClick={handleApplyCalibration}
        disabled={processing}
        variant="contained"
      >
        {processing ? "Processing..." : "Apply Calibration"}
      </Button>

      <Typography variant="caption">
        This will transform all bounding boxes using the calibration matrix
      </Typography>
    </Box>
  );
}
```

### Step 3: UI統合

Modal内のカスタムパネルとして追加:

```tsx
// source/app/packages/core/src/components/Modal/Modal.tsx

function ModalWithCustomTools() {
  return (
    <ModalContainer>
      <ModalLooker />
      <ModalSidebar>
        {/* 既存のタブ */}
        <SamplesTab />
        <AnnotateTab />
        
        {/* カスタムツールタブ */}
        <CustomToolsTab>
          <CameraCalibrationTool />
          <BatchLabelTool />
          <ExportTool />
        </CustomToolsTab>
      </ModalSidebar>
    </ModalContainer>
  );
}
```

---

## まとめ

### 流用可能な既存機能

| 機能 | 流用方法 | コード量 |
|------|---------|---------|
| **Command Bus** | `useCommandBus()` | 0行（そのまま使用） |
| **Command Handler** | 既に登録済み | 0行 |
| **HTTP API** | `patchSample()` | 0行 |
| **State管理** | `refreshSample()` | 0行 |
| **UI再描画** | Recoil自動 | 0行 |
| **エラーハンドリング** | try-catch追加 | 3-5行 |
| **通知** | `useNotification()` | 2-3行 |

**合計**: **約10-20行**のカスタムコードで実装可能。

### 推奨実装パターン

1. **シンプルな更新**: パターン1（Command Bus） - 最も安全で保守性が高い
2. **高度な制御**: パターン2（直接API） - 低レベルアクセスが必要な場合
3. **バッチ処理**: パターン3（Python） - 大量データの高速処理

### キーポイント

1. ✅ **既存インフラを最大限活用** - 車輪の再発明は不要
2. ✅ **Command Busで安全性確保** - エラーハンドリング、State管理が自動
3. ✅ **UI再描画は自動実行** - refreshSampleを呼ぶだけでOK
4. ✅ **ETag管理は既存実装** - バージョン競合も自動検出
5. ✅ **最小限のコード** - カスタムロジックのみ実装すればよい

---

## 次のステップ

1. **プロトタイプ作成**: `useUpdateAnnotation` フックを実装
2. **テスト**: 単一ラベル更新で動作確認
3. **拡張**: バッチ処理、座標変換など高度な機能追加
4. **最適化**: Debounce、Optimistic UIなどパフォーマンス改善
5. **ドキュメント化**: チーム内での使用方法を共有

---

以上が、FiftyOne Annotation機能の既存インフラを活用したプログラム的更新の設計書です。

# FiftyOne アノテーション機能 設計書

> **バージョン:** 1.0  
> **最終更新日:** 2026年1月19日  
> **対象:** FiftyOne v1.x / NuScenes v1.0-mini

---

## 目次

1. [概要](#1-概要)
2. [アーキテクチャ概観](#2-アーキテクチャ概観)
3. [環境設定と機能フラグ](#3-環境設定と機能フラグ)
4. [メディアタイプとラベルタイプの対応関係](#4-メディアタイプとラベルタイプの対応関係)
5. [Label Schema システム詳細](#5-label-schema-システム詳細)
6. [3Dシーン（.fo3d）の構造と設計](#6-3dシーンfo3dの構造と設計)
7. [データフロー設計](#7-データフロー設計)
8. [実装詳細](#8-実装詳細)
9. [トラブルシューティング](#9-トラブルシューティング)
10. [API リファレンス](#10-api-リファレンス)
11. [付録](#11-付録)

---

## 1. 概要

### 1.1 本書の目的

本設計書は、FiftyOne における**アノテーション機能**の有効化、2D/3D データセットの構築、
および Label Schema システムの詳細な設計・実装ガイドを提供します。

### 1.2 対象読者

- FiftyOne を使用したアノテーションシステムを構築するエンジニア
- NuScenes 等の自動運転データセットを FiftyOne に取り込む開発者
- 3D 点群アノテーション機能を実装する担当者

### 1.3 前提条件

| 項目 | 要件 |
|------|------|
| Python | 3.9+ |
| FiftyOne | v1.x |
| 追加パッケージ | `nuscenes-devkit`, `open3d`, `pyquaternion` |

---

## 2. アーキテクチャ概観

### 2.1 システム構成図

```
┌─────────────────────────────────────────────────────────────────────┐
│                         FiftyOne App (Web UI)                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                   │
│  │  Grid View  │  │ Sample View │  │ Annotate Tab│ ← VFF_EXP_ANNOTATION│
│  └─────────────┘  └─────────────┘  └──────┬──────┘                   │
└─────────────────────────────────────────────┼───────────────────────┘
                                               │
                  ┌────────────────────────────┼────────────────────────┐
                  │                            ▼                        │
                  │         Label Schema System                         │
                  │  ┌─────────────────────────────────────────┐        │
                  │  │  generate_label_schemas()               │        │
                  │  │  set_label_schemas()                    │        │
                  │  │  activate_label_schemas()               │        │
                  │  │  validate_label_schemas()               │        │
                  │  └─────────────────────────────────────────┘        │
                  │                                                     │
                  │         Media Type Support Matrix                   │
                  │  ┌─────────────────────────────────────────┐        │
                  │  │  IMAGE  → Detections, Classifications   │        │
                  │  │  THREE_D → Polylines (points3d)         │        │
                  │  └─────────────────────────────────────────┘        │
                  └─────────────────────────────────────────────────────┘
                                               │
                                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                           Dataset Storage                            │
│  ┌───────────────────┐  ┌───────────────────┐                        │
│  │ 2D Dataset        │  │ 3D Dataset        │                        │
│  │ - filepath: .jpg  │  │ - filepath: .fo3d │                        │
│  │ - detections      │  │ - cuboids         │                        │
│  │ - label_schemas   │  │ - label_schemas   │                        │
│  └───────────────────┘  └───────────────────┘                        │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 主要コンポーネント

| コンポーネント | 役割 | ソースファイル |
|--------------|------|---------------|
| Feature Flag Manager | 環境変数からフラグを取得 | `fiftyone/internal/features/environment_manager.py` |
| Label Schema Generator | フィールドからスキーマを自動生成 | `fiftyone/core/annotation/generate_label_schemas.py` |
| Label Schema Validator | スキーマの妥当性検証 | `fiftyone/core/annotation/validate_label_schemas.py` |
| Annotation Constants | メディアタイプ別サポート定義 | `fiftyone/core/annotation/constants.py` |
| Scene (3D) | .fo3d シーン管理 | `fiftyone/core/threed/scene_3d.py` |
| PointCloud | PCD ファイル管理 | `fiftyone/core/threed/pointcloud.py` |

---

## 3. 環境設定と機能フラグ

### 3.1 VFF_EXP_ANNOTATION フラグ

#### 3.1.1 概要

`VFF_EXP_ANNOTATION` は FiftyOne App のアノテーション機能を有効化する**実験的機能フラグ**です。

#### 3.1.2 設定方法

**PowerShell（一時的）:**
```powershell
$env:VFF_EXP_ANNOTATION = "1"
```

**PowerShell（永続的）:**
```powershell
setx VFF_EXP_ANNOTATION 1
```

**Python スクリプト内:**
```python
import os
os.environ.setdefault("VFF_EXP_ANNOTATION", "1")
```

> ⚠️ **注意:** Python スクリプト内で設定する場合、`fiftyone` をインポートする**前**に設定する必要があります。

#### 3.1.3 フラグの内部処理

**フラグ定義（TypeScript）:**

ファイル: `source/app/packages/feature-flags/src/client/flags.ts`

```typescript
export const VFF_EXP_ANNOTATION = "VFF_EXP_ANNOTATION";

export const featureFlags: FeatureFlag[] = [
  {
    name: VFF_EXP_ANNOTATION,
    description: "Enable experimental annotation features",
    defaultValue: false,
    source: "environment",
  },
];
```

**フラグ取得ロジック（Python）:**

ファイル: `source/fiftyone/internal/features/environment_manager.py`

```python
def get_feature_flag(name: str) -> bool:
    value = os.environ.get(name, "").lower()
    return value in ("1", "true", "yes", "on")
```

---

## 4. メディアタイプとラベルタイプの対応関係

### 4.1 サポートマトリックス

ファイル: `source/fiftyone/core/annotation/constants.py`

```python
# 全メディアタイプ共通でサポートされるラベル
SUPPORTED_LABEL_TYPES = {
    fol.Classification,    # 単一クラス分類
    fol.Classifications,   # 複数クラス分類
}

# メディアタイプ別の追加サポート
SUPPORTED_LABEL_TYPES_BY_MEDIA_TYPE = {
    fom.IMAGE: {
        fol.Detection,     # 2D バウンディングボックス（単一）
        fol.Detections,    # 2D バウンディングボックス（複数）
    },
    fom.THREE_D: {
        fol.Polyline,      # 3D ポリライン（単一）
        fol.Polylines,     # 3D ポリライン（複数）
    },
}

# サポート対象のメディアタイプ
SUPPORTED_MEDIA_TYPES = {fom.IMAGE, fom.THREE_D}
```

### 4.2 メディアタイプの判定

| ファイル拡張子 | メディアタイプ | アノテーション可能なラベル |
|--------------|---------------|------------------------|
| `.jpg`, `.png`, `.bmp` | `image` | `Detection`, `Detections`, `Classification`, `Classifications` |
| `.fo3d` | `3d` (THREE_D) | `Polyline`, `Polylines`, `Classification`, `Classifications` |
| `.pcd` | `point-cloud` | **アノテーション非対応** |

> ⚠️ **重要:** `.pcd` ファイルを直接 `filepath` に指定すると `point-cloud` タイプとなり、
> アノテーション機能が使えません。必ず `.fo3d` 経由でロードしてください。

### 4.3 ラベルタイプ詳細

#### 4.3.1 fo.Detection / fo.Detections

**用途:** 2D 画像上のバウンディングボックス

**構造:**
```python
fo.Detection(
    label="car",                           # クラス名
    bounding_box=[x, y, width, height],    # 正規化座標 [0,1]
    confidence=0.95,                       # 任意: 信頼度
    instance=fo.Instance(),                # 任意: インスタンス追跡用
)

fo.Detections(detections=[detection1, detection2, ...])
```

**フィールドスキーマ:**
| フィールド | 型 | 説明 |
|-----------|-----|------|
| `label` | `str` | クラス名 |
| `bounding_box` | `list[float]` | `[x, y, width, height]` (0-1 正規化) |
| `confidence` | `float` | 信頼度スコア |
| `instance` | `fo.Instance` | インスタンス追跡用オブジェクト |

#### 4.3.2 fo.Polyline / fo.Polylines

**用途:** 2D/3D でのポリゴン・ポリライン

**2D 構造:**
```python
fo.Polyline(
    label="car",
    points=[
        [(x1, y1), (x2, y2), (x3, y3), (x4, y4)],  # ポリゴン頂点
    ],
    closed=True,   # 閉じたポリゴン
    filled=False,  # 塗りつぶしなし
)
```

**3D 構造（points3d 必須）:**
```python
polyline = fo.Polyline(
    label="car",
    points=[],      # 2D は空
    closed=True,
    filled=False,
)
polyline.points3d = [
    [[x1, y1, z1], [x2, y2, z2], [x3, y3, z3], [x4, y4, z4]],  # 3D 頂点
]
```

> ⚠️ **重要:** 3D アノテーションでは `points3d` 属性を**必ず設定**する必要があります。

---

## 5. Label Schema システム詳細

### 5.1 Label Schema とは

Label Schema は、データセットのフィールドに対してアノテーション UI の動作を定義する設定です。
これにより、App の Annotate タブでどのフィールドを編集可能にするか、
どのような入力コンポーネントを使用するかを制御できます。

### 5.2 Label Schema の構造

```python
label_schema = {
    "field_name": {
        "type": "detections",           # フィールドの型
        "component": "dropdown",        # UI コンポーネント
        "classes": ["car", "truck"],    # 選択可能なクラス
        "attributes": {                 # サブフィールドの設定
            "confidence": {
                "type": "float",
                "component": "slider",
                "range": [0.0, 1.0],
            },
            "id": {
                "type": "id",
                "component": "text",
                "read_only": True,
            },
        },
    },
}
```

### 5.3 主要 API メソッド

#### 5.3.1 generate_label_schemas()

**目的:** データセットのフィールドから自動的に Label Schema を生成する

**シグネチャ:**
```python
def generate_label_schemas(
    sample_collection,
    fields=None,
    scan_samples=True
) -> dict:
```

**パラメータ:**

| パラメータ | 型 | デフォルト | 説明 |
|-----------|-----|----------|------|
| `sample_collection` | `SampleCollection` | 必須 | 対象のデータセットまたはビュー |
| `fields` | `str` or `list[str]` or `None` | `None` | 対象フィールド。`None`の場合は全サポートフィールド |
| `scan_samples` | `bool` | `True` | サンプルをスキャンして値の範囲等を取得するか |

**戻り値:**
```python
{
    "detections": {
        "type": "detections",
        "component": "dropdown",
        "classes": ["car", "pedestrian", "bicycle"],
        "attributes": {...},
    },
}
```

**使用例:**
```python
# 全サポートフィールドのスキーマを生成（サンプルスキャンあり）
schema = dataset.generate_label_schemas()

# 特定フィールドのみ
schema = dataset.generate_label_schemas(fields=["detections"])

# サンプルスキャンなし（高速だが値の範囲等は取得されない）
schema = dataset.generate_label_schemas(scan_samples=False)
```

**内部処理フロー:**

```
generate_label_schemas()
    │
    ├─→ list_valid_annotation_fields()  # サポート対象フィールドを列挙
    │       │
    │       └─→ _is_supported_label()   # メディアタイプ別の判定
    │
    ├─→ _generate_field_label_schema()  # 各フィールドのスキーマ生成
    │       │
    │       ├─→ get_type()              # フィールド型の判定
    │       │
    │       ├─→ _handle_str()           # 文字列フィールドの処理
    │       │       └─→ collection.distinct() # 値の列挙
    │       │
    │       ├─→ _handle_float_or_int()  # 数値フィールドの処理
    │       │       └─→ collection.bounds()   # 範囲の取得
    │       │
    │       └─→ _handle_bool()          # ブールフィールドの処理
    │
    └─→ validate_label_schemas()        # 生成結果の検証
```

#### 5.3.2 set_label_schemas()

**目的:** データセットに Label Schema を設定する

**シグネチャ:**
```python
def set_label_schemas(self, label_schemas: dict) -> None:
```

**パラメータ:**

| パラメータ | 型 | 説明 |
|-----------|-----|------|
| `label_schemas` | `dict` | フィールド名をキーとしたスキーマ辞書 |

**使用例:**
```python
# 自動生成したスキーマを設定
dataset.set_label_schemas(
    dataset.generate_label_schemas(fields=["detections"], scan_samples=True)
)

# 手動でスキーマを定義して設定
custom_schema = {
    "detections": {
        "type": "detections",
        "component": "dropdown",
        "classes": ["car", "truck", "bus"],
    }
}
dataset.set_label_schemas(custom_schema)
```

**内部処理:**

```python
def set_label_schemas(self, label_schemas):
    if label_schemas is None:
        label_schemas = {}

    # 1. スキーマの検証
    foa.validate_label_schemas(self, label_schemas)
    
    # 2. データベースドキュメントに保存
    self._doc.label_schemas = label_schemas
    
    # 3. アクティブスキーマの更新（削除されたフィールドを除去）
    self._doc.active_label_schemas = [
        field
        for field in self.active_label_schemas
        if field in label_schemas
    ]
    
    # 4. 永続化
    self.save()
```

#### 5.3.3 activate_label_schemas()

**目的:** Label Schema を有効化し、App の Annotate タブに表示する

**シグネチャ:**
```python
def activate_label_schemas(self, fields=None) -> None:
```

**パラメータ:**

| パラメータ | 型 | デフォルト | 説明 |
|-----------|-----|----------|------|
| `fields` | `str` or `list[str]` or `None` | `None` | 有効化するフィールド。`None`の場合は全スキーマ |

**使用例:**
```python
# 全ての Label Schema を有効化
dataset.activate_label_schemas()

# 特定フィールドのみ有効化
dataset.activate_label_schemas(fields=["detections"])
dataset.activate_label_schemas(fields=["detections", "classifications"])
```

**内部処理:**

```python
def activate_label_schemas(self, fields=None):
    # 1. 対象フィールドの決定
    if fields is None:
        fields = sorted(self.label_schemas)
    
    fields = _as_str_list(fields)
    
    # 2. 現在のアクティブリストを取得
    result = self.active_label_schemas
    
    for field in fields:
        # 3. スキーマ存在チェック
        if field not in self._doc.label_schemas:
            raise ValueError(f"field '{field}' is not in the label schema")
        
        # 4. 重複チェック
        if field in result:
            raise ValueError(f"field '{field}' is already active")
        
        # 5. アクティブリストに追加
        result.append(field)
    
    # 6. 保存
    self._doc.active_label_schemas = result
    self.save()
```

#### 5.3.4 deactivate_label_schemas()

**目的:** Label Schema を無効化し、App の Annotate タブから非表示にする

**シグネチャ:**
```python
def deactivate_label_schemas(self, fields=None) -> None:
```

**使用例:**
```python
# 全ての Label Schema を無効化
dataset.deactivate_label_schemas()

# 特定フィールドのみ無効化
dataset.deactivate_label_schemas(fields=["classifications"])
```

#### 5.3.5 update_label_schema()

**目的:** 単一フィールドの Label Schema を更新する

**シグネチャ:**
```python
def update_label_schema(self, field: str, label_schema: dict) -> None:
```

**使用例:**
```python
# 単一フィールドのスキーマを更新
dataset.update_label_schema(
    "ground_truth",
    dataset.generate_label_schemas("ground_truth")
)
```

#### 5.3.6 delete_label_schemas()

**目的:** Label Schema を削除する

**シグネチャ:**
```python
def delete_label_schemas(self, fields=None) -> None:
```

**使用例:**
```python
# 全ての Label Schema を削除
dataset.delete_label_schemas()

# 特定フィールドのスキーマを削除
dataset.delete_label_schemas(fields=["old_annotations"])
```

### 5.4 validate_label_schemas()

**目的:** Label Schema の妥当性を検証する

**ファイル:** `source/fiftyone/core/annotation/validate_label_schemas.py`

**シグネチャ:**
```python
def validate_label_schemas(
    sample_collection,
    label_schema,
    fields=None,
    _allow_default=False
) -> None:
```

**検証項目:**

| 検証 | 説明 |
|------|------|
| フィールド存在確認 | 指定フィールドがデータセットに存在するか |
| サポート確認 | フィールドがアノテーション対象として有効か |
| 型一致確認 | `type` がフィールドの実際の型と一致するか |
| コンポーネント確認 | `component` が型に対して有効か |
| 必須設定確認 | 必要な設定（`range`, `values` 等）が存在するか |
| 読み取り専用確認 | `read_only` フィールドの設定が正しいか |

**例外:**
```python
class ValidationErrors(ExceptionGroup):
    """複数の検証エラーをまとめて報告"""
```

### 5.5 型とコンポーネントの対応

ファイル: `source/fiftyone/core/annotation/constants.py`

#### 5.5.1 プリミティブ型

| 型 | コンポーネント | デフォルト | 追加設定 |
|----|--------------|-----------|---------|
| `bool` | `checkbox`, `toggle` | `toggle` | - |
| `int` | `dropdown`, `radio`, `slider`, `text` | `text` | `range` (slider時) |
| `float` | `dropdown`, `radio`, `slider`, `text` | `text` | `range`, `precision` |
| `str` | `dropdown`, `radio`, `text` | `text` | `values` (dropdown/radio時) |
| `date` | `datepicker` | `datepicker` | - |
| `datetime` | `datepicker` | `datepicker` | - |
| `dict` | `json` | `json` | - |
| `id` | `text` | `text` | `read_only: true` 必須 |

#### 5.5.2 リスト型

| 型 | コンポーネント | デフォルト | 追加設定 |
|----|--------------|-----------|---------|
| `list<bool>` | `checkboxes`, `dropdown`, `text` | `text` | `values` |
| `list<int>` | `checkboxes`, `dropdown`, `text` | `text` | `values` |
| `list<float>` | `checkboxes`, `dropdown`, `text` | `text` | `values` |
| `list<str>` | `checkboxes`, `dropdown`, `text` | `text` | `values` |

#### 5.5.3 コンポーネント選択のヒューリスティック

`scan_samples=True` 時の自動選択ロジック:

```python
CHECKBOXES_OR_RADIO_THRESHOLD = 5   # 5個以下: radio/checkboxes
VALUES_THRESHOLD = 1000              # 1000個以下: dropdown

def _handle_str(collection, field_name, is_list, settings, scan_samples):
    values = collection.distinct(field_name)  # ユニーク値を取得
    
    if values:
        if len(values) <= 5:
            # 5個以下: radio（単数）または checkboxes（複数）
            settings["component"] = "checkboxes" if is_list else "radio"
        elif len(values) <= 1000:
            # 1000個以下: dropdown
            settings["component"] = "dropdown"
        # それ以外: デフォルトの text
        
        if settings["component"] in {"checkboxes", "dropdown", "radio"}:
            settings["values"] = values
    
    return settings
```

### 5.6 完全な使用パターン

#### パターン1: 最も一般的な使用法

```python
import fiftyone as fo

# データセット準備
dataset = fo.Dataset("my_dataset")
dataset.add_samples([...])

# Label Schema の設定と有効化（3行で完結）
dataset.set_label_schemas(
    dataset.generate_label_schemas(
        fields=["detections"],
        scan_samples=True,
    )
)
dataset.activate_label_schemas()

# App 起動
fo.launch_app(dataset)
```

#### パターン2: カスタムクラスを指定

```python
custom_schema = {
    "detections": {
        "type": "detections",
        "component": "dropdown",
        "classes": ["car", "truck", "bus", "motorcycle", "bicycle"],
        "attributes": {
            "confidence": {
                "type": "float",
                "component": "slider",
                "range": [0.0, 1.0],
            },
            "occluded": {
                "type": "bool",
                "component": "toggle",
            },
        },
    },
}

dataset.set_label_schemas(custom_schema)
dataset.activate_label_schemas(fields=["detections"])
```

#### パターン3: 既存スキーマの部分更新

```python
# 既存スキーマを取得
schemas = dataset.label_schemas

# 特定フィールドを更新
schemas["detections"]["classes"].append("new_class")

# 再設定
dataset.set_label_schemas(schemas)
```

---

## 6. 3Dシーン（.fo3d）の構造と設計

### 6.1 .fo3d ファイルフォーマット

`.fo3d` は FiftyOne 独自の 3D シーン記述フォーマットで、JSON 形式で保存されます。

**ファイル構造:**

```json
{
  "fo3dVersion": "1.0",
  "uuid": "unique-identifier",
  "name": "Scene",
  "visible": true,
  "camera": {
    "position": [0, 0, 10],
    "look_at": [0, 0, 0],
    "up": [0, 1, 0],
    "fov": 50
  },
  "lights": [...],
  "background": null,
  "children": [
    {
      "name": "lidar",
      "visible": true,
      "pcdPath": "point_cloud.pcd",  // 相対パス推奨
      "centerGeometry": false,
      "flagForProjection": false,
      "defaultMaterial": {
        "type": "PointCloudMaterial",
        "size": 1.0,
        "opacity": 1.0
      }
    }
  ]
}
```

### 6.2 Scene クラス

ファイル: `source/fiftyone/core/threed/scene_3d.py`

```python
class Scene(Object3D):
    """3D シーングラフを表現するクラス
    
    Args:
        camera: デフォルトカメラ。None の場合は PerspectiveCamera が作成される
        lights: シーン内のライトリスト。None の場合はデフォルトライトセット
        background: シーン背景。色、画像、スカイボックスが指定可能
    """
    
    def __init__(
        self,
        camera: Optional[PerspectiveCamera] = None,
        lights: Optional[List[Light]] = None,
        background: Optional[SceneBackground] = None,
    ):
        ...
    
    def add(self, child: Object3D) -> None:
        """子オブジェクトを追加"""
        ...
    
    def write(
        self,
        fo3d_path: str,
        resolve_relative_paths: bool = False,
        pprint: bool = False
    ) -> None:
        """シーンを .fo3d ファイルに書き出し
        
        Args:
            fo3d_path: 出力パス（.fo3d 拡張子必須）
            resolve_relative_paths: True の場合、相対パスを絶対パスに解決
            pprint: True の場合、JSON を整形出力
        """
        ...
    
    @staticmethod
    def from_fo3d(path: str) -> "Scene":
        """既存の .fo3d ファイルからシーンを読み込み"""
        ...
```

### 6.3 PointCloud クラス

ファイル: `source/fiftyone/core/threed/pointcloud.py`

```python
class PointCloud(Object3D):
    """点群を表現するクラス
    
    Args:
        name: 点群の名前
        pcd_path: .pcd ファイルへのパス（相対または絶対）
        material: 点群のマテリアル設定
        center_geometry: ジオメトリを中心に配置するか
        flag_for_projection: 正射影投影に使用するか
        visible: 可視性
        position: 位置 (x, y, z)
        quaternion: 回転（クォータニオン）
        scale: スケール (x, y, z)
    
    Raises:
        ValueError: pcd_path が .pcd で終わらない場合
    """
    
    _asset_path_fields = ["pcd_path"]  # アセットパスを持つフィールド
    
    def __init__(
        self,
        name: str,
        pcd_path: str,
        material: Optional[PointCloudMaterial] = None,
        center_geometry: bool = False,
        flag_for_projection: bool = False,
        visible: bool = True,
        position: Optional[Vec3UnionType] = None,
        scale: Optional[Vec3UnionType] = None,
        quaternion: Optional[Quaternion] = None,
    ):
        if not pcd_path.lower().endswith(".pcd"):
            raise ValueError("Point cloud must be a .pcd file")
        ...
```

### 6.4 パス解決の仕組み

#### 6.4.1 相対パス vs 絶対パス

| パスタイプ | 例 | 推奨 |
|-----------|-----|------|
| 相対パス | `"point_cloud.pcd"` | ✅ **推奨** |
| 絶対パス（Unix） | `"/home/user/data/point_cloud.pcd"` | ⚠️ 注意が必要 |
| 絶対パス（Windows） | `"C:\\FiftyOne\\dataset\\point_cloud.pcd"` | ❌ **非推奨** |

#### 6.4.2 フロントエンドでのパス解決

ファイル: `source/app/packages/looker-3d/src/fo3d/utils.ts`

```typescript
export function getResolvedUrlForFo3dAsset(
  assetPath: string,
  fo3dRoot: string
): string {
  // HTTP/S3 URL はそのまま返す
  if (assetPath.startsWith("http://") || 
      assetPath.startsWith("https://") ||
      assetPath.startsWith("s3://")) {
    return assetPath;
  }
  
  // 絶対パスはそのまま返す（問題の原因）
  if (isAbsolutePath(assetPath)) {
    return assetPath;
  }
  
  // 相対パスは fo3dRoot と結合
  return joinPaths(fo3dRoot, assetPath);
}

function isAbsolutePath(path: string): boolean {
  // Unix 絶対パス
  if (path.startsWith("/")) return true;
  // Windows 絶対パス（C:\, D:\ など）
  if (/^[A-Za-z]:[\\/]/.test(path)) return true;
  return false;
}
```

> ⚠️ **Windows パスの問題:** `C:\FiftyOne\...` のような Windows 絶対パスは
> `isAbsolutePath()` で true を返すため、そのまま返されてしまいます。
> しかし、Web 環境では `C:\...` を解決できないため、エラーになります。

#### 6.4.3 正しい .fo3d 生成方法

```python
def build_fo3d_scene(pcd_path: str) -> str:
    """PCD パスから .fo3d シーンを生成
    
    Args:
        pcd_path: .pcd ファイルへの絶対パス
        
    Returns:
        生成された .fo3d ファイルへの絶対パス
    """
    scene = fo.Scene()
    
    # 重要: 絶対パスからファイル名のみを取得
    pcd_filename = os.path.basename(pcd_path)
    
    # ファイル名（相対パス）でPointCloudを追加
    scene.add(fo.PointCloud("lidar", pcd_filename))
    
    # .fo3d は .pcd と同じディレクトリに保存
    fo3d_path = os.path.splitext(pcd_path)[0] + ".fo3d"
    scene.write(fo3d_path)
    
    return fo3d_path
```

---

## 7. データフロー設計

### 7.1 NuScenes データ取り込みフロー

```
┌─────────────────────────────────────────────────────────────────┐
│                     NuScenes Dataset                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │ Camera (.jpg)│  │ LiDAR (.bin)│  │ Annotations │              │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘              │
└─────────┼────────────────┼────────────────┼─────────────────────┘
          │                │                │
          ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Processing Pipeline                          │
│                                                                  │
│  ┌──────────────────┐    ┌──────────────────┐                    │
│  │ camera_sample()  │    │  load_lidar()    │                    │
│  │                  │    │                  │                    │
│  │ - filepath: .jpg │    │ - .bin → .pcd    │                    │
│  │ - detections     │    │ - color mapping  │                    │
│  │ - cuboids (2D)   │    │ - segmentation   │                    │
│  └────────┬─────────┘    └────────┬─────────┘                    │
│           │                       │                              │
│           │              ┌────────┴─────────┐                    │
│           │              │ build_fo3d_scene()│                    │
│           │              │                  │                    │
│           │              │ - .pcd → .fo3d   │                    │
│           │              │ - relative path  │                    │
│           │              └────────┬─────────┘                    │
│           │                       │                              │
│           │              ┌────────┴─────────┐                    │
│           │              │  lidar_sample()  │                    │
│           │              │                  │                    │
│           │              │ - filepath: .fo3d│                    │
│           │              │ - cuboids (3D)   │                    │
│           │              │   └─ points3d    │                    │
│           │              └────────┬─────────┘                    │
│           │                       │                              │
└───────────┼───────────────────────┼──────────────────────────────┘
            │                       │
            ▼                       ▼
┌───────────────────────┐  ┌───────────────────────┐
│ nuscenes_annotations  │  │nuscenes_lidar_annotations│
│                       │  │                       │
│ media_type: image     │  │ media_type: 3d        │
│ fields:               │  │ fields:               │
│   - detections        │  │   - cuboids           │
│     └─ Detections     │  │     └─ Polylines      │
│   - cuboids (2D)      │  │       └─ points3d     │
│     └─ Polylines      │  │                       │
└───────────────────────┘  └───────────────────────┘
            │                       │
            ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Label Schema Setup                           │
│                                                                  │
│  set_label_schemas(                                              │
│      generate_label_schemas(fields=["detections"], scan=True)   │
│  )                                                               │
│  activate_label_schemas()                                        │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 7.2 処理シーケンス図

```
User           Script              NuScenes API          FiftyOne
  │               │                     │                    │
  │ Run script    │                     │                    │
  │──────────────>│                     │                    │
  │               │ get_sample_data()   │                    │
  │               │────────────────────>│                    │
  │               │<────────────────────│                    │
  │               │                     │                    │
  │               │ [Camera Processing]                      │
  │               │ camera_sample()────────────────────────>│
  │               │ ├─ Create fo.Sample(filepath=.jpg)      │
  │               │ ├─ Create fo.Detections()               │
  │               │ └─ Create fo.Polylines()                │
  │               │                     │                    │
  │               │ [LiDAR Processing]                       │
  │               │ load_lidar()       │                    │
  │               │ ├─ Load .bin       │                    │
  │               │ ├─ Apply colormap  │                    │
  │               │ └─ Save .pcd       │                    │
  │               │                     │                    │
  │               │ build_fo3d_scene() │                    │
  │               │ ├─ Create fo.Scene()                    │
  │               │ ├─ Add PointCloud(relative_path)        │
  │               │ └─ Write .fo3d     │                    │
  │               │                     │                    │
  │               │ lidar_sample()────────────────────────>│
  │               │ ├─ Create fo.Sample(filepath=.fo3d)    │
  │               │ └─ Create fo.Polylines(points3d)       │
  │               │                     │                    │
  │               │ [Dataset Creation]                       │
  │               │ add_samples()─────────────────────────>│
  │               │                     │                    │
  │               │ [Label Schema Setup]                     │
  │               │ set_label_schemas()───────────────────>│
  │               │ activate_label_schemas()───────────────>│
  │               │                     │                    │
  │               │ launch_app()─────────────────────────>│
  │               │                     │                 App│
  │<───────────────────────────────────────────────────────│
  │                                                          │
```

---

## 8. 実装詳細

### 8.1 メインスクリプト構造

ファイル: `trial/self_driving_01_loading_datasets_copilot.py`

```python
#!/usr/bin/env python3
"""
NuScenes データセットを FiftyOne にロードするスクリプト

機能:
- マルチセンサーデータ（カメラ、LiDAR、RADAR）の取り込み
- 2D/3D アノテーションデータセットの分離
- Label Schema の自動設定
"""

import os
import numpy as np
from PIL import Image

# FiftyOne インポート前に環境変数を設定
os.environ.setdefault("VFF_EXP_ANNOTATION", "1")

import fiftyone as fo
from nuscenes import NuScenes
from nuscenes.utils.geometry_utils import box_in_image, view_points, BoxVisibility
from nuscenes.utils.data_io import load_bin_file
from nuscenes.utils.color_map import get_colormap
from nuscenes.lidarseg.lidarseg_utils import paint_points_label
from nuscenes.utils.data_classes import LidarPointCloud, RadarPointCloud
import open3d as o3d
from pyquaternion import Quaternion


# -----------------------------------------------------------------------------
# インスタンス管理
# -----------------------------------------------------------------------------

def _get_instance(instance_map: dict, box, fallback_key: str) -> fo.Instance:
    """バウンディングボックスに対応するインスタンスを取得または作成
    
    Args:
        instance_map: インスタンスキャッシュ
        box: NuScenes のバウンディングボックス
        fallback_key: インスタンストークンがない場合のフォールバックキー
        
    Returns:
        fo.Instance オブジェクト
    """
    instance_key = (
        getattr(box, "instance_token", None)
        or getattr(box, "token", None)
        or fallback_key
    )
    instance = instance_map.get(instance_key)
    if instance is None:
        instance = fo.Instance()
        instance_map[instance_key] = instance
    return instance


# -----------------------------------------------------------------------------
# カメラ処理
# -----------------------------------------------------------------------------

def camera_sample(
    group: fo.Group,
    filepath: str,
    sensor: str,
    token: str,
    scene: dict,
    instance_map: dict
) -> fo.Sample:
    """カメラサンプルを生成
    
    Args:
        group: グループオブジェクト（None の場合はグループなし）
        filepath: 画像ファイルパス
        sensor: センサー名（"CAM_FRONT" など）
        token: サンプルデータトークン
        scene: シーン情報
        instance_map: インスタンスキャッシュ
        
    Returns:
        fo.Sample オブジェクト
    """
    # サンプル作成
    if group is None:
        sample = fo.Sample(filepath=filepath)
    else:
        sample = fo.Sample(filepath=filepath, group=group.element(sensor))
    
    # NuScenes API からデータ取得
    data_path, boxes, camera_intrinsic = nusc.get_sample_data(
        token, box_vis_level=BoxVisibility.NONE
    )
    
    # 画像サイズ取得
    image = Image.open(data_path)
    width, height = image.size
    shape = (height, width)
    
    # ラベルリスト初期化
    polylines = []
    detections = []
    
    # 各バウンディングボックスを処理
    for box_index, box in enumerate(boxes):
        if box_in_image(box, camera_intrinsic, shape, vis_level=BoxVisibility.ALL):
            # 3D → 2D 投影
            corners = view_points(box.corners(), camera_intrinsic, normalize=True)[:2, :]
            x_coords = corners[0] / width
            y_coords = corners[1] / height
            
            # バウンディングボックス計算
            x_min = max(0.0, float(x_coords.min()))
            x_max = min(1.0, float(x_coords.max()))
            y_min = max(0.0, float(y_coords.min()))
            y_max = min(1.0, float(y_coords.max()))
            box_w = max(0.0, x_max - x_min)
            box_h = max(0.0, y_max - y_min)
            
            # 底面ポリゴン計算
            bottom = [
                (corners[0][0]/width, corners[1][0]/height),
                (corners[0][1]/width, corners[1][1]/height),
                (corners[0][5]/width, corners[1][5]/height),
                (corners[0][4]/width, corners[1][4]/height),
            ]
            
            instance = _get_instance(instance_map, box, f"camera-{box_index}")
            
            # Detection 追加（2D アノテーション用）
            detections.append(
                fo.Detection(
                    label=box.name,
                    bounding_box=[x_min, y_min, box_w, box_h],
                    instance=instance,
                )
            )
            
            # Polyline 追加（可視化用）
            polylines.append(
                fo.Polyline(
                    label=box.name,
                    points=[bottom],
                    closed=True,
                    filled=False,
                    instance=instance,
                )
            )
    
    sample["detections"] = fo.Detections(detections=detections)
    sample["cuboids"] = fo.Polylines(polylines=polylines)
    
    return sample


# -----------------------------------------------------------------------------
# LiDAR 処理
# -----------------------------------------------------------------------------

def load_lidar(lidar_token: str) -> str:
    """LiDAR データを読み込み、カラーマップを適用して PCD 形式で保存
    
    Args:
        lidar_token: LiDAR データトークン
        
    Returns:
        保存された .pcd ファイルの絶対パス
    """
    # カラーマップ取得
    gt_from = "lidarseg"
    lidarseg_filename = dataroot + nusc.get(gt_from, lidar_token)['filename']
    colormap = get_colormap()
    name2index = nusc.lidarseg_name2idx_mapping
    
    # 点群に色を適用
    coloring = paint_points_label(lidarseg_filename, None, name2index, colormap=colormap)
    
    # ファイルパス処理（二重拡張子防止）
    filepath = dataroot + nusc.get("sample_data", lidar_token)['filename']
    root, extension = os.path.splitext(filepath)
    
    if extension.lower() == ".bin" and root.lower().endswith(".pcd"):
        pcd_path = os.path.abspath(root)
    elif extension.lower() == ".pcd":
        pcd_path = os.path.abspath(filepath)
    else:
        pcd_path = os.path.abspath(root + ".pcd")
    
    # 点群読み込み
    cloud = LidarPointCloud.from_file(filepath)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(cloud.points[:3, :].T)
    
    # 色設定
    colors = coloring[:, :3]
    pcd.colors = o3d.utility.Vector3dVector(colors)
    
    # PCD 保存
    o3d.io.write_point_cloud(pcd_path, pcd)
    
    return pcd_path


def build_fo3d_scene(pcd_path: str) -> str:
    """PCD ファイルから .fo3d シーンを生成
    
    重要: アセットパスは相対パス（ファイル名のみ）で指定
    
    Args:
        pcd_path: .pcd ファイルの絶対パス
        
    Returns:
        生成された .fo3d ファイルの絶対パス
    """
    scene = fo.Scene()
    
    # 絶対パスからファイル名のみ取得（重要！）
    abs_pcd_path = os.path.abspath(pcd_path)
    pcd_filename = os.path.basename(abs_pcd_path)
    
    # 相対パスで PointCloud を追加
    scene.add(fo.PointCloud("lidar", pcd_filename))
    
    # .fo3d パス生成
    fo3d_path = os.path.splitext(abs_pcd_path)[0] + ".fo3d"
    scene.write(fo3d_path)
    
    return fo3d_path


def lidar_sample(
    group: fo.Group,
    filepath: str,
    sensor: str,
    lidar_token: str,
    scene: dict,
    instance_map: dict
) -> fo.Sample:
    """LiDAR サンプルを生成
    
    Args:
        group: グループオブジェクト
        filepath: .fo3d ファイルパス
        sensor: センサー名
        lidar_token: LiDAR データトークン
        scene: シーン情報
        instance_map: インスタンスキャッシュ
        
    Returns:
        fo.Sample オブジェクト
    """
    # サンプル作成
    if group is None:
        sample = fo.Sample(filepath=filepath)
    else:
        sample = fo.Sample(filepath=filepath, group=group.element(sensor))
    
    # NuScenes API からバウンディングボックス取得
    data_path, boxes, _ = nusc.get_sample_data(lidar_token, box_vis_level=BoxVisibility.NONE)
    
    # 3D Polylines 生成
    polylines = []
    for box_index, box in enumerate(boxes):
        instance = _get_instance(instance_map, box, f"lidar-{box_index}")
        
        # 3D バウンディングボックスの頂点
        corners_3d = box.corners().T
        bottom = corners_3d[[0, 1, 5, 4], :]
        
        # Polyline 作成
        polyline = fo.Polyline(
            label=box.name,
            points=[],      # 2D は空
            closed=True,
            filled=False,
            instance=instance,
        )
        # 3D 頂点を設定（重要！）
        polyline.points3d = [bottom.tolist()]
        
        polylines.append(polyline)
    
    sample["cuboids"] = fo.Polylines(polylines=polylines)
    
    return sample


# -----------------------------------------------------------------------------
# メイン処理
# -----------------------------------------------------------------------------

# NuScenes 初期化
dataroot = 'dataset/'
nusc = NuScenes(version='v1.0-mini', dataroot=dataroot, verbose=True)

# データセット作成
all_sensor_dataset = fo.Dataset("nuscenes_sensors2", overwrite=True)
all_sensor_dataset.add_group_field("group", default="CAM_FRONT")

annotation_dataset = fo.Dataset("nuscenes_annotations", overwrite=True)
lidar_annotation_dataset = fo.Dataset("nuscenes_lidar_annotations", overwrite=True)

# センサーグループ定義
groups = [
    "CAM_FRONT", "CAM_FRONT_RIGHT", "CAM_BACK_RIGHT", "CAM_BACK",
    "CAM_BACK_LEFT", "CAM_FRONT_LEFT", "LIDAR_TOP"
]

# サンプル収集
samples = []
annotation_samples = []
lidar_annotation_samples = []

# シーン反復処理
for scene in nusc.scene:
    token = scene['first_sample_token']
    my_sample = nusc.get('sample', token)
    
    sample_count = 0
    while my_sample["next"]:
        if sample_count >= 2:
            break
        sample_count += 1
        
        group = fo.Group()
        instance_map = {}
        
        for sensor in groups:
            data = nusc.get('sample_data', my_sample['data'][sensor])
            filepath = dataroot + data["filename"]
            
            if data["sensor_modality"] == "lidar":
                # LiDAR 処理
                filepath = load_lidar(my_sample["data"]["LIDAR_TOP"])
                sample = lidar_sample(group, filepath, sensor, ...)
                
                # .fo3d 生成
                fo3d_path = build_fo3d_scene(filepath)
                lidar_annotation_samples.append(
                    lidar_sample(None, fo3d_path, sensor, ...)
                )
                
            elif data["sensor_modality"] == "camera":
                # カメラ処理
                sample = camera_sample(group, filepath, sensor, ...)
                annotation_samples.append(
                    camera_sample(None, filepath, sensor, ...)
                )
            
            samples.append(sample)
        
        my_sample = nusc.get('sample', my_sample["next"])

# データセットにサンプル追加
all_sensor_dataset.add_samples(samples)
annotation_dataset.add_samples(annotation_samples)
lidar_annotation_dataset.add_samples(lidar_annotation_samples)

# Label Schema 設定
# 2D アノテーション用
annotation_dataset.set_label_schemas(
    annotation_dataset.generate_label_schemas(
        fields=["detections"],
        scan_samples=True,
    )
)
annotation_dataset.activate_label_schemas()

# 3D アノテーション用
lidar_annotation_dataset.set_label_schemas(
    lidar_annotation_dataset.generate_label_schemas(
        fields=["cuboids"],
        scan_samples=True,
    )
)
lidar_annotation_dataset.activate_label_schemas()

# App 起動
session = fo.launch_app(lidar_annotation_dataset)
session.wait()
```

### 8.2 コード詳細解説

#### 8.2.1 環境変数設定のタイミング

```python
# ✅ 正しい: fiftyone インポート前に設定
os.environ.setdefault("VFF_EXP_ANNOTATION", "1")
import fiftyone as fo

# ❌ 間違い: fiftyone インポート後に設定（効果なし）
import fiftyone as fo
os.environ.setdefault("VFF_EXP_ANNOTATION", "1")
```

#### 8.2.2 二重拡張子防止ロジック

```python
filepath = "dataset/samples/LIDAR_TOP/some_file.pcd.bin"

root, extension = os.path.splitext(filepath)
# root = "dataset/samples/LIDAR_TOP/some_file.pcd"
# extension = ".bin"

if extension.lower() == ".bin" and root.lower().endswith(".pcd"):
    # 既に .pcd が含まれている → そのまま使用
    pcd_path = os.path.abspath(root)
    # pcd_path = "C:\FiftyOne\dataset\samples\LIDAR_TOP\some_file.pcd"
elif extension.lower() == ".pcd":
    # 既に .pcd → そのまま
    pcd_path = os.path.abspath(filepath)
else:
    # .bin など → .pcd に変換
    pcd_path = os.path.abspath(root + ".pcd")
```

#### 8.2.3 points3d の設定

```python
# ✅ 正しい: points3d を設定
polyline = fo.Polyline(label="car", points=[], closed=True, filled=False)
polyline.points3d = [[[x1,y1,z1], [x2,y2,z2], [x3,y3,z3], [x4,y4,z4]]]

# ❌ 間違い: points だけ設定（3D では表示されない）
polyline = fo.Polyline(
    label="car",
    points=[[[x1,y1,z1], [x2,y2,z2], ...]]  # これは 2D 用
)
```

---

## 9. トラブルシューティング

### 9.1 エラー一覧と対処法

#### 9.1.1 `field 'cuboids' is not supported`

**発生条件:**
```python
# .pcd を直接 filepath に指定
sample = fo.Sample(filepath="point_cloud.pcd")
sample["cuboids"] = fo.Polylines(...)  # ← エラー
```

**原因:** 
`.pcd` ファイルは `point-cloud` メディアタイプとして認識され、
`SUPPORTED_LABEL_TYPES_BY_MEDIA_TYPE` に `point-cloud` が含まれていないため。

**解決策:**
```python
# .fo3d を使用
fo3d_path = build_fo3d_scene(pcd_path)
sample = fo.Sample(filepath=fo3d_path)
sample["cuboids"] = fo.Polylines(...)  # ✅ OK
```

#### 9.1.2 `can't access property "toLocaleLowerCase", ... is undefined`

**発生条件:**
```json
// .fo3d 内の pcdPath が絶対パス
{
  "pcdPath": "C:\\FiftyOne\\dataset\\samples\\LIDAR_TOP\\file.pcd"
}
```

**原因:** 
フロントエンドの `getResolvedUrlForFo3dAsset` が Windows 絶対パスを解決できない。

**解決策:**
```python
# 相対パス（ファイル名のみ）を使用
pcd_filename = os.path.basename(pcd_path)  # "file.pcd"
scene.add(fo.PointCloud("lidar", pcd_filename))
```

#### 9.1.3 3D ビューに何も表示されない

**チェックリスト:**

| チェック項目 | 確認方法 |
|-------------|---------|
| `.fo3d` が存在するか | `os.path.exists(fo3d_path)` |
| `.pcd` が存在するか | `.fo3d` と同じディレクトリにあるか確認 |
| `.fo3d` 内のパスが相対か | JSON を開いて `pcdPath` を確認 |
| `media_type` が `3d` か | `dataset.media_type` で確認 |

**デバッグコマンド:**
```python
# データセット情報確認
print(f"Media type: {dataset.media_type}")
print(f"Sample count: {len(dataset)}")

# サンプル確認
sample = dataset.first()
print(f"Filepath: {sample.filepath}")
print(f"Filepath exists: {os.path.exists(sample.filepath)}")

# .fo3d 内容確認
import json
with open(sample.filepath, 'r') as f:
    fo3d_content = json.load(f)
print(json.dumps(fo3d_content, indent=2))
```

#### 9.1.4 Annotate タブが表示されない

**チェックリスト:**

| チェック項目 | 確認方法 |
|-------------|---------|
| `VFF_EXP_ANNOTATION` が設定されているか | `echo $env:VFF_EXP_ANNOTATION` |
| Label Schema が設定されているか | `dataset.label_schemas` |
| Label Schema が有効化されているか | `dataset.active_label_schemas` |

**確認コマンド:**
```python
print(f"Label schemas: {dataset.label_schemas}")
print(f"Active schemas: {dataset.active_label_schemas}")
```

### 9.2 デバッグ用ユーティリティ

```python
def debug_dataset(dataset):
    """データセットの状態をデバッグ出力"""
    print("=" * 50)
    print(f"Dataset: {dataset.name}")
    print(f"Media type: {dataset.media_type}")
    print(f"Sample count: {len(dataset)}")
    print(f"Fields: {list(dataset.get_field_schema().keys())}")
    print(f"Label schemas: {list(dataset.label_schemas.keys())}")
    print(f"Active schemas: {dataset.active_label_schemas}")
    print("=" * 50)
    
    if len(dataset) > 0:
        sample = dataset.first()
        print(f"First sample filepath: {sample.filepath}")
        print(f"Filepath exists: {os.path.exists(sample.filepath)}")


def debug_fo3d(fo3d_path):
    """FO3D ファイルの内容をデバッグ出力"""
    import json
    
    print("=" * 50)
    print(f"FO3D file: {fo3d_path}")
    print(f"Exists: {os.path.exists(fo3d_path)}")
    
    if os.path.exists(fo3d_path):
        with open(fo3d_path, 'r') as f:
            content = json.load(f)
        
        # アセットパス抽出
        for child in content.get('children', []):
            pcd_path = child.get('pcdPath')
            if pcd_path:
                print(f"PCD path in fo3d: {pcd_path}")
                
                # 相対パスの解決
                fo3d_dir = os.path.dirname(fo3d_path)
                resolved_path = os.path.join(fo3d_dir, pcd_path)
                print(f"Resolved path: {resolved_path}")
                print(f"Resolved exists: {os.path.exists(resolved_path)}")
    
    print("=" * 50)
```

---

## 10. API リファレンス

### 10.1 Dataset メソッド（Label Schema 関連）

| メソッド | 説明 |
|---------|------|
| `dataset.label_schemas` | 現在の Label Schema を取得（プロパティ） |
| `dataset.active_label_schemas` | 有効な Label Schema フィールドを取得（プロパティ） |
| `dataset.generate_label_schemas(fields, scan_samples)` | Label Schema を自動生成 |
| `dataset.set_label_schemas(schemas)` | Label Schema を設定 |
| `dataset.update_label_schema(field, schema)` | 単一フィールドのスキーマを更新 |
| `dataset.activate_label_schemas(fields)` | Label Schema を有効化 |
| `dataset.deactivate_label_schemas(fields)` | Label Schema を無効化 |
| `dataset.delete_label_schemas(fields)` | Label Schema を削除 |

### 10.2 Label 関連クラス

| クラス | 用途 | メディアタイプ |
|--------|------|--------------|
| `fo.Detection` | 単一 2D バウンディングボックス | `image` |
| `fo.Detections` | 複数 2D バウンディングボックス | `image` |
| `fo.Polyline` | 単一ポリライン/ポリゴン | `image`, `3d` |
| `fo.Polylines` | 複数ポリライン/ポリゴン | `image`, `3d` |
| `fo.Classification` | 単一クラス分類 | 全メディア |
| `fo.Classifications` | 複数クラス分類 | 全メディア |

### 10.3 3D 関連クラス

| クラス | 用途 |
|--------|------|
| `fo.Scene` | 3D シーングラフのルート |
| `fo.PointCloud` | 点群オブジェクト |
| `fo.PerspectiveCamera` | パースペクティブカメラ |
| `fo.PointCloudMaterial` | 点群のマテリアル設定 |

---

## 11. 付録

### 11.1 ファイル一覧

| パス | 説明 |
|------|------|
| `source/fiftyone/core/annotation/constants.py` | アノテーション定数定義 |
| `source/fiftyone/core/annotation/generate_label_schemas.py` | スキーマ生成ロジック |
| `source/fiftyone/core/annotation/validate_label_schemas.py` | スキーマ検証ロジック |
| `source/fiftyone/core/annotation/utils.py` | アノテーションユーティリティ |
| `source/fiftyone/core/dataset.py` | Dataset クラス定義 |
| `source/fiftyone/core/threed/scene_3d.py` | Scene クラス定義 |
| `source/fiftyone/core/threed/pointcloud.py` | PointCloud クラス定義 |
| `source/app/packages/feature-flags/src/client/flags.ts` | フィーチャーフラグ定義 |
| `source/app/packages/looker-3d/src/fo3d/utils.ts` | FO3D ユーティリティ |
| `trial/self_driving_01_loading_datasets_copilot.py` | NuScenes ロードスクリプト |

### 11.2 用語集

| 用語 | 説明 |
|------|------|
| **Label Schema** | フィールドのアノテーション設定を定義するスキーマ |
| **Media Type** | サンプルのメディア種別（`image`, `3d`, `video` など） |
| **FO3D** | FiftyOne 3D シーンフォーマット（`.fo3d`） |
| **PCD** | Point Cloud Data フォーマット（`.pcd`） |
| **Active Schema** | App の Annotate タブで編集可能な状態のスキーマ |
| **points3d** | 3D 空間でのポリライン頂点座標 |

### 11.3 関連リンク

- [FiftyOne 公式ドキュメント](https://docs.voxel51.com/)
- [NuScenes データセット](https://www.nuscenes.org/)
- [Open3D ドキュメント](http://www.open3d.org/docs/)

### 11.4 変更履歴

| 日付 | バージョン | 変更内容 |
|------|-----------|---------|
| 2026-01-19 | 1.0 | 初版作成 |

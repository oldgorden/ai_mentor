# 数据接口

所有数据通过 `lib.experiment_env` 加载，禁止自己调 load_dataset 或生成合成数据。

## API

```python
from lib.experiment_env import (
    list_available,          # -> dict: 列出可用数据集
    get_dataloaders,         # (dataset_name, batch_size=16) -> (train_loader, val_loader, train_size, val_size)
    get_depth,               # (dataset_name, idx) -> depth tensor
    get_dynamic_class_names, # -> dict {id: name}
)
```

## 当前可用数据集
- `cityscapes_train` — Cityscapes 训练集 (2975 样本, 80% split → train_size=2380)
- `cityscapes_val` — Cityscapes 验证集 (500 样本, 80% split → val_size=100)

## 数据格式
- 返回的 batch dict: `{"image": (B,3,128,128) float32, "mask": (B,1,128,128) float32}`
- key 是 `mask` 不是 `segmentation`
- mask 是二值掩码 (1.0=目标, 0.0=背景)

## 动态物体类别（Cityscapes）
- person(11), rider(12), car(13), truck(14), bus(15), train(16), motorcycle(17), bicycle(18)
- 动态类别 ID 集合: {11, 12, 13, 14, 15, 16, 17, 18}
- 约 98% 样本包含动态物体

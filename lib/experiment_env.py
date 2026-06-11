import os
import torch
import numpy as np
from torch.utils.data import Dataset, DataLoader, random_split
from datasets import load_from_disk

CITYSCAPES_DYNAMIC_IDS = {11, 12, 13, 14, 15, 16, 17, 18}

DATASET_REGISTRY = {
    "cityscapes_train": "/tmp/datasets/cityscapes/train",
    "cityscapes_val": "/tmp/datasets/cityscapes/validation",
}

_TARGET_SIZE = 128


def list_available():
    available = {}
    for name, path in DATASET_REGISTRY.items():
        available[name] = {"path": path, "exists": os.path.exists(path)}
        if os.path.exists(path):
            ds = load_from_disk(path)
            available[name]["num_samples"] = len(ds)
    return available


def _to_tensor(sample):
    import torch.nn.functional as F

    raw_img = np.array(sample["image"], dtype=np.float32)
    if raw_img.ndim == 3 and raw_img.shape[0] in (1, 3):
        img = raw_img
        if img.shape[0] == 1:
            img = np.repeat(img, 3, axis=0)
    elif raw_img.ndim == 3 and raw_img.shape[2] in (1, 3):
        img = raw_img.transpose(2, 0, 1)
        if img.shape[0] == 1:
            img = np.repeat(img, 3, axis=0)
    elif raw_img.ndim == 2:
        img = np.stack([raw_img] * 3, axis=0)
    else:
        img = raw_img
    if img.max() > 1.5:
        img = img / 255.0

    _, h, w = img.shape
    mask = np.zeros((h, w), dtype=np.float32)
    if "segmentation_19" in sample:
        seg = np.array(sample["segmentation_19"])
        if seg.ndim == 2:
            for cid in CITYSCAPES_DYNAMIC_IDS:
                mask[seg == cid] = 1.0
    elif "annotation" in sample:
        ann = np.array(sample["annotation"])
        for lbl in np.unique(ann):
            if lbl > 0:
                mask[ann == lbl] = 1.0

    t_img = torch.FloatTensor(img).unsqueeze(0)
    t_img = F.interpolate(
        t_img, size=(_TARGET_SIZE, _TARGET_SIZE), mode="bilinear", align_corners=False
    ).squeeze(0)
    t_mask = torch.FloatTensor(mask).unsqueeze(0).unsqueeze(0)
    t_mask = F.interpolate(
        t_mask, size=(_TARGET_SIZE, _TARGET_SIZE), mode="nearest"
    ).squeeze(0).squeeze(0).unsqueeze(0)
    return {"image": t_img, "mask": t_mask}


class SegDataset(Dataset):
    def __init__(self, dataset_name, num_samples=None):
        path = DATASET_REGISTRY.get(dataset_name)
        if not path or not os.path.exists(path):
            raise FileNotFoundError(
                f"Dataset '{dataset_name}' not found. Available: {list(DATASET_REGISTRY.keys())}"
            )
        self.data = load_from_disk(path)
        if num_samples and num_samples < len(self.data):
            self.data = self.data.select(range(num_samples))

    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        return _to_tensor(self.data[idx])


def get_dataloaders(dataset_name, num_samples=None, batch_size=16, train_ratio=0.8):
    ds = SegDataset(dataset_name, num_samples)
    train_size = int(train_ratio * len(ds))
    val_size = len(ds) - train_size
    train_ds, val_ds = random_split(
        ds, [train_size, val_size], generator=torch.Generator().manual_seed(42)
    )
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False, num_workers=0)
    return train_loader, val_loader, train_size, val_size


def get_depth(dataset_name, idx):
    path = DATASET_REGISTRY.get(dataset_name)
    if not path or not os.path.exists(path):
        return None
    ds = load_from_disk(path)
    sample = ds[idx]
    if "depth" in sample:
        return np.array(sample["depth"])
    return None


def get_dynamic_class_names():
    return {
        11: "person",
        12: "rider",
        13: "car",
        14: "truck",
        15: "bus",
        16: "train",
        17: "motorcycle",
        18: "bicycle",
    }


if __name__ == "__main__":
    info = list_available()
    for name, meta in info.items():
        print(f"  {name}: {meta.get('num_samples', '?')} samples, exists={meta['exists']}")

    train_loader, val_loader, n_train, n_val = get_dataloaders("cityscapes_train", num_samples=100)
    batch = next(iter(train_loader))
    print(f"  train: {n_train}, val: {n_val}")
    print(f"  batch: image={batch['image'].shape}, mask={batch['mask'].shape}")
    print(f"  dynamic%: {batch['mask'].mean():.3f}")

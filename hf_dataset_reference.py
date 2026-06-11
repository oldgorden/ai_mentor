import os
from datasets import load_from_disk

LOCAL_DATASETS = {
    "scene_parse150": "/tmp/datasets/scene_parse150",
    "voc2012": "/tmp/datasets/voc2012",
    "coco_stuff": "/tmp/datasets/coco_stuff",
}


def load_local_dataset(name, num_samples=None):
    if name in LOCAL_DATASETS and os.path.exists(LOCAL_DATASETS[name]):
        print(f"  Loading LOCAL dataset: {name} from {LOCAL_DATASETS[name]}")
        ds = load_from_disk(LOCAL_DATASETS[name])
        if num_samples and num_samples < len(ds):
            ds = ds.select(range(num_samples))
        return ds, False
    return None, None


def try_load_huggingface_dataset_orig(dataset_name, num_samples=300):
    from datasets import load_dataset
    import signal

    def timeout_handler(signum, frame):
        raise TimeoutError("Loading timed out")

    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(120)
    try:
        if dataset_name == "scene_parse150":
            ds = load_dataset(
                "scene_parse_150", "scene_parsing", split=f"train[:{num_samples}]"
            )
        elif dataset_name == "voc2012":
            ds = load_dataset("hf/voc2012", split=f"train[:{num_samples}]")
        elif dataset_name == "coco_stuff":
            ds = load_dataset("merve/coco_stuff", split=f"train[:{num_samples}]")
        else:
            raise ValueError(f"Unknown dataset: {dataset_name}")
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
        print(f"  Loaded {dataset_name}: {len(ds)} samples from HuggingFace")
        return ds, False
    except (TimeoutError, Exception) as e:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)
        raise e

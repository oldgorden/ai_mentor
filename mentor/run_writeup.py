#!/usr/bin/env python3
"""直接对已有实验结果进行论文撰写（跳过实验阶段）"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def gather_citations(exp_dir, num_rounds, model):
    """收集引用"""
    from lib.writeup import gather_citations
    return gather_citations(exp_dir, num_rounds, small_model=model)


def _build_summaries_from_journals(exp_dir):
    """Build baseline/research/ablation summaries from journal.json files.

    The normal AI Scientist pipeline generates these summaries via
    ``overall_summarize``, but that module is not available.  When
    summaries are missing we construct them directly from the best
    journal nodes so that ``aggregate_plots`` and ``perform_writeup``
    have real data to work with.
    """
    import glob as _glob

    logs_dir = exp_dir / "logs"
    if not logs_dir.exists():
        return

    target_dir = logs_dir / "0-run"
    target_dir.mkdir(parents=True, exist_ok=True)

    already_has = (
        (target_dir / "baseline_summary.json").exists()
        and (target_dir / "research_summary.json").exists()
        and (target_dir / "ablation_summary.json").exists()
    )
    if already_has:
        print("Summaries already exist, skipping journal scan.")
        return

    stage_map = {
        "stage_1": "BASELINE_SUMMARY",
        "stage_2": "BASELINE_SUMMARY",
        "stage_3": "RESEARCH_SUMMARY",
        "stage_4": "ABLATION_SUMMARY",
    }
    summaries = {"BASELINE_SUMMARY": {}, "RESEARCH_SUMMARY": {}, "ABLATION_SUMMARY": {}}

    for journal_path in sorted(logs_dir.rglob("journal.json")):
        rel = journal_path.relative_to(logs_dir)
        parts = rel.parts
        if len(parts) < 2:
            continue
        stage_dir_name = parts[1]
        stage_prefix = stage_dir_name.split("_")[0] + "_" + stage_dir_name.split("_")[1]
        summary_key = stage_map.get(stage_prefix)
        if not summary_key:
            continue

        try:
            with open(journal_path) as f:
                data = json.load(f)
        except Exception:
            continue

        nodes = data.get("nodes", []) if isinstance(data, dict) else data
        good_nodes = [n for n in nodes if not n.get("is_buggy")]
        if not good_nodes:
            continue

        best = good_nodes[-1]
        bucket = summaries[summary_key].setdefault("best node", {})
        for k in ("overall_plan", "analysis", "metric", "plot_plan",
                   "plot_analyses"):
            if best.get(k) and k not in bucket:
                bucket[k] = best[k]

        npy_files = list(journal_path.parent.parent.rglob("experiment_results/**/*.npy"))
        existing_npy = set(bucket.get("exp_results_npy_files", []))
        for p in npy_files:
            existing_npy.add(str(p.relative_to(exp_dir)))
        if existing_npy:
            bucket["exp_results_npy_files"] = sorted(existing_npy)

    for key, filename in [
        ("BASELINE_SUMMARY", "baseline_summary.json"),
        ("RESEARCH_SUMMARY", "research_summary.json"),
        ("ABLATION_SUMMARY", "ablation_summary.json"),
    ]:
        out_path = target_dir / filename
        with open(out_path, "w") as f:
            json.dump(summaries[key], f, indent=2)
        bucket = summaries[key].get("best node", {})
        n_npy = len(bucket.get("exp_results_npy_files", []))
        n_fields = len(bucket)
        print(f"Wrote {out_path} ({n_fields} fields, {n_npy} npy files)")
    parser = argparse.ArgumentParser(description="Direct writeup for existing experiment")
    parser.add_argument("--exp_dir", required=True, help="Experiment directory with journal results")
    parser.add_argument("--group", type=str, default=str(ROOT / "group_member.json"),
                        help="Group config file (auto-fills model params)")
    parser.add_argument("--model_writeup", default=None)
    parser.add_argument("--model_citation", default=None)
    parser.add_argument("--model_writeup_small", default=None)
    parser.add_argument("--max_tokens", type=int, default=None)
    parser.add_argument("--num_cite_rounds", type=int, default=5)
    parser.add_argument("--writeup_type", default="normal", choices=["normal", "icbinb"])
    parser.add_argument("--skip_citation", action="store_true")
    args = parser.parse_args()

    group_cfg_path = Path(args.group)
    if group_cfg_path.exists():
        with open(group_cfg_path) as f:
            group_cfg = json.load(f)
        for m in group_cfg.get("members", []):
            if m.get("name") == "writer_postgrad":
                defaults = m
                break
        else:
            defaults = {}
    else:
        defaults = {}

    if args.model_writeup is None:
        args.model_writeup = defaults.get("model", "custom/mimo-v2.5-pro")
    if args.model_citation is None:
        args.model_citation = defaults.get("model", "custom/mimo-v2.5-pro")
    if args.model_writeup_small is None:
        args.model_writeup_small = defaults.get("model", "custom/mimo-v2.5-pro")
    if args.max_tokens is None:
        args.max_tokens = defaults.get("max_tokens", 128000)

    exp_dir = Path(args.exp_dir)
    if not exp_dir.exists():
        print(f"Experiment directory not found: {exp_dir}")
        sys.exit(1)

    # 统计好节点
    total_good = 0
    for root_dir, dirs, files in os.walk(exp_dir):
        for f in files:
            if f == "journal.json":
                try:
                    with open(os.path.join(root_dir, f)) as fh:
                        data = json.load(fh)
                    nodes = data if isinstance(data, list) else data.get("nodes", [])
                    total_good += sum(1 for n in nodes if not n.get("is_buggy"))
                except:
                    pass

    print(f"Experiment: {exp_dir}")
    print(f"Good nodes: {total_good}")

    if total_good == 0:
        print("No good nodes found, cannot write paper.")
        sys.exit(1)

    # Build experiment summaries from journals if they don't exist
    _build_summaries_from_journals(exp_dir)

    # Copy experiment results for aggregation (merge ALL run directories)
    import shutil
    dest = exp_dir / "experiment_results"
    for run_dir in sorted((exp_dir / "logs").glob("*-run")):
        results_subdir = run_dir / "experiment_results"
        if results_subdir.exists():
            shutil.copytree(results_subdir, dest, dirs_exist_ok=True)

    # Aggregate plots
    from lib.plotting import aggregate_plots
    try:
        aggregate_plots(base_folder=exp_dir, model=args.model_writeup_small)
        print("Plots aggregated.")
    except Exception as e:
        print(f"Plot aggregation failed: {e}")

    # Clean up
    tmp_results = exp_dir / "experiment_results"
    if tmp_results.exists():
        shutil.rmtree(tmp_results)

    # Gather citations
    citations_text = ""
    if not args.skip_citation:
        try:
            print("Gathering citations...")
            citations_text = gather_citations(
                exp_dir,
                num_rounds=args.num_cite_rounds,
                model=args.model_citation,
            )
            print(f"Citations gathered: {len(citations_text)} chars")
        except Exception as e:
            print(f"Citation gathering failed: {e}")

    # Perform writeup
    print("Starting paper writeup...")
    if args.writeup_type == "normal":
        from lib.writeup import perform_writeup
        success = perform_writeup(
            base_folder=exp_dir,
            small_model=args.model_writeup_small,
            big_model=args.model_writeup,
            page_limit=8,
            citations_text=citations_text,
        )
    else:
        from lib.icbinb_writeup import perform_writeup
        success = perform_writeup(
            base_folder=exp_dir,
            small_model=args.model_writeup_small,
            big_model=args.model_writeup,
            page_limit=4,
            citations_text=citations_text,
        )

    if success:
        print("Paper writeup completed!")
        pdf = list(exp_dir.glob("*.pdf"))
        if pdf:
            print(f"PDF: {pdf[0]}")
    else:
        print("Paper writeup failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()

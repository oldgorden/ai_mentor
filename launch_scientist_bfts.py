import os
import os.path as osp
import json
import argparse
import shutil
import torch
import re
import sys
from datetime import datetime
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
from lib.llm import create_client

from contextlib import contextmanager
from lib.perform_experiments_bfts import (
    perform_experiments_bfts,
)
from lib.bfts_utils import (
    idea_to_markdown,
    edit_bfts_config_file,
)
from lib.plotting import aggregate_plots
from lib.writeup import perform_writeup
from lib.icbinb_writeup import (
    perform_writeup as perform_icbinb_writeup,
    gather_citations,
)
from lib.llm_review import perform_review, load_paper
from lib.vlm_review import perform_imgs_cap_ref_review
from lib.token_tracker import token_tracker


def print_time():
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


def save_token_tracker(idea_dir):
    with open(osp.join(idea_dir, "token_tracker.json"), "w") as f:
        json.dump(token_tracker.get_summary(), f)
    with open(osp.join(idea_dir, "token_tracker_interactions.json"), "w") as f:
        json.dump(token_tracker.get_interactions(), f)


def parse_arguments():
    parser = argparse.ArgumentParser(description="Run AI scientist experiments")
    parser.add_argument(
        "--writeup-type",
        type=str,
        default="icbinb",
        choices=["normal", "icbinb"],
        help="Type of writeup to generate (normal=8 page, icbinb=4 page)",
    )
    parser.add_argument(
        "--load_ideas",
        type=str,
        default="ideas/i_cant_believe_its_not_better.json",
        help="Path to a JSON file containing pregenerated ideas",
    )
    parser.add_argument(
        "--load_code",
        action="store_true",
        help="If set, load a Python file with same name as ideas file but .py extension",
    )
    parser.add_argument(
        "--idea_idx",
        type=int,
        default=0,
        help="Index of the idea to run",
    )
    parser.add_argument(
        "--add_dataset_ref",
        action="store_true",
        help="If set, add a HF dataset reference to the idea",
    )
    parser.add_argument(
        "--continue_from",
        type=str,
        default=None,
        help="Path to existing experiment directory to continue from",
    )
    parser.add_argument(
        "--writeup-retries",
        type=int,
        default=3,
        help="Number of writeup attempts to try",
    )
    parser.add_argument(
        "--attempt_id",
        type=int,
        default=0,
        help="Attempt ID, used to distinguish same idea in different attempts in parallel runs",
    )
    parser.add_argument(
        "--model_agg_plots",
        type=str,
        default="o3-mini-2025-01-31",
        help="Model to use for plot aggregation",
    )
    parser.add_argument(
        "--model_writeup",
        type=str,
        default="o1-preview-2024-09-12",
        help="Model to use for writeup",
    )
    parser.add_argument(
        "--model_citation",
        type=str,
        default="gpt-4o-2024-11-20",
        help="Model to use for citation gathering",
    )
    parser.add_argument(
        "--num_cite_rounds",
        type=int,
        default=20,
        help="Number of citation rounds to perform",
    )
    parser.add_argument(
        "--model_writeup_small",
        type=str,
        default="gpt-4o-2024-05-13",
        help="Smaller model to use for writeup",
    )
    parser.add_argument(
        "--model_review",
        type=str,
        default="gpt-4o-2024-11-20",
        help="Model to use for review main text and captions",
    )
    parser.add_argument(
        "--skip_writeup",
        action="store_true",
        help="If set, skip the writeup process",
    )
    parser.add_argument(
        "--skip_review",
        action="store_true",
        help="If set, skip the review process",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Inject best code but restart from stage 1 (re-run with real data)",
    )
    return parser.parse_args()


def _scan_previous_experiment(exp_dir):
    """扫描已有实验，返回 (最佳代码, 最高完成 stage 编号)"""
    best_code = ""
    best_metric_val = None
    last_stage = 0

    logs_dir = os.path.join(exp_dir, "logs")
    if not os.path.exists(logs_dir):
        return "", 0

    for run_dir in sorted(os.listdir(logs_dir)):
        run_path = os.path.join(logs_dir, run_dir)
        if not os.path.isdir(run_path):
            continue
        for stage_dir in sorted(os.listdir(run_path)):
            stage_path = os.path.join(run_path, stage_dir)
            if not os.path.isdir(stage_path):
                continue
            # 解析 stage 编号: "stage_1_..." -> 1
            parts = stage_dir.split("_")
            if len(parts) >= 2 and parts[0] == "stage":
                try:
                    stage_num = int(parts[1])
                except ValueError:
                    continue
                if stage_num > last_stage:
                    last_stage = stage_num

            journal_path = os.path.join(stage_path, "journal.json")
            if not os.path.exists(journal_path):
                continue
            try:
                with open(journal_path) as f:
                    data = json.load(f)
                nodes = data if isinstance(data, list) else data.get("nodes", [])
                for n in nodes:
                    if n.get("is_buggy"):
                        continue
                    code = n.get("code", "")
                    if not code or len(code) < 100:
                        continue
                    # 比较 metric
                    metric = n.get("metric", {})
                    metric_val = None
                    if isinstance(metric, dict):
                        val = metric.get("value")
                        if isinstance(val, dict):
                            for m in val.get("metric_names", []):
                                for d in m.get("data", []):
                                    v = d.get("final_value")
                                    if v is not None and (metric_val is None or v > metric_val):
                                        metric_val = v
                        elif isinstance(val, (int, float)):
                            metric_val = val
                    if metric_val is not None and (best_metric_val is None or metric_val > best_metric_val):
                        best_metric_val = metric_val
                        best_code = code
                    elif not best_code and code:
                        best_code = code  # fallback: 用第一个好代码
            except Exception:
                pass

    return best_code, last_stage


def get_available_gpus(gpu_ids=None):
    if gpu_ids is not None:
        return [int(gpu_id) for gpu_id in gpu_ids.split(",")]
    return list(range(torch.cuda.device_count()))


def find_pdf_path_for_review(idea_dir):
    pdf_files = [f for f in os.listdir(idea_dir) if f.endswith(".pdf")]
    reflection_pdfs = [f for f in pdf_files if "reflection" in f]
    if reflection_pdfs:
        # First check if there's a final version
        final_pdfs = [f for f in reflection_pdfs if "final" in f.lower()]
        if final_pdfs:
            # Use the final version if available
            pdf_path = osp.join(idea_dir, final_pdfs[0])
        else:
            # Try to find numbered reflections
            reflection_nums = []
            for f in reflection_pdfs:
                match = re.search(r"reflection[_.]?(\d+)", f)
                if match:
                    reflection_nums.append((int(match.group(1)), f))

            if reflection_nums:
                # Get the file with the highest reflection number
                highest_reflection = max(reflection_nums, key=lambda x: x[0])
                pdf_path = osp.join(idea_dir, highest_reflection[1])
            else:
                # Fall back to the first reflection PDF if no numbers found
                pdf_path = osp.join(idea_dir, reflection_pdfs[0])
    return pdf_path


@contextmanager
def redirect_stdout_stderr_to_file(log_file_path):
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    log = open(log_file_path, "a")
    sys.stdout = log
    sys.stderr = log
    try:
        yield
    finally:
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        log.close()


if __name__ == "__main__":
    args = parse_arguments()
    os.environ["AI_SCIENTIST_ROOT"] = os.path.dirname(os.path.abspath(__file__))
    print(f"Set AI_SCIENTIST_ROOT to {os.environ['AI_SCIENTIST_ROOT']}")

    # Check available GPUs and adjust parallel processes if necessary
    available_gpus = get_available_gpus()
    print(f"Using GPUs: {available_gpus}")

    with open(args.load_ideas, "r") as f:
        ideas = json.load(f)
        print(f"Loaded {len(ideas)} pregenerated ideas from {args.load_ideas}")

    idea = ideas[args.idea_idx]

    # 如果指定了 --continue_from，继续已有实验
    if args.continue_from and os.path.exists(args.continue_from):
        idea_dir = args.continue_from
        print(f"继续已有实验: {idea_dir}")
        if args.restart:
            idea["_start_stage"] = 1
            print("强制从 stage 1 重跑 (--restart)")
            idea_md_path = os.path.join(idea_dir, "idea.md")
            if os.path.exists(idea_md_path):
                with open(idea_md_path, "r") as f:
                    idea["Code"] = f.read()
                print(f"使用当前 idea.md 代码 ({len(idea['Code'])} chars)")
        else:
            best_code, last_stage = _scan_previous_experiment(idea_dir)
            if best_code:
                idea["Code"] = best_code
                print(f"注入旧实验最佳代码 ({len(best_code)} chars)")
            if last_stage > 0:
                start_stage = min(last_stage + 1, 4)
                idea["_start_stage"] = start_stage
                print(f"从 stage {start_stage} 继续")
    else:
        date = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        idea_dir = f"experiments/{date}_{idea['Name']}_attempt_{args.attempt_id}"
        print(f"Results will be saved in {idea_dir}")
        os.makedirs(idea_dir, exist_ok=True)

    # Convert idea json to markdown file
    idea_path_md = osp.join(idea_dir, "idea.md")

    # If load_code is True, get the Python file with same name as JSON
    code = None
    if args.load_code:
        code_path = args.load_ideas.rsplit(".", 1)[0] + ".py"
        if os.path.exists(code_path):
            with open(code_path, "r") as f:
                code = f.read()
        else:
            print(f"Warning: Code file {code_path} not found")
    else:
        code_path = None

    if args.restart and os.path.exists(idea_path_md):
        print(f"--restart: keeping existing idea.md ({os.path.getsize(idea_path_md)} bytes)")
    else:
        idea_to_markdown(ideas[args.idea_idx], idea_path_md, code_path)

    dataset_ref_code = None
    if args.add_dataset_ref:
        dataset_ref_path = "hf_dataset_reference.py"
        if os.path.exists(dataset_ref_path):
            with open(dataset_ref_path, "r") as f:
                dataset_ref_code = f.read()
        else:
            print(f"Warning: Dataset reference file {dataset_ref_path} not found")
            dataset_ref_code = None

    if dataset_ref_code is not None and code is not None:
        added_code = dataset_ref_code + "\n" + code
    elif dataset_ref_code is not None and code is None:
        added_code = dataset_ref_code
    elif dataset_ref_code is None and code is not None:
        added_code = code
    else:
        added_code = None

    print(added_code)

    # Add code to idea json if it was loaded
    if added_code is not None:
        ideas[args.idea_idx]["Code"] = added_code

    # Store raw idea json
    idea_path_json = osp.join(idea_dir, "idea.json")
    with open(idea_path_json, "w") as f:
        json.dump(ideas[args.idea_idx], f, indent=4)

    start_stage = ideas[args.idea_idx].pop("_start_stage", 1)
    os.environ["AI_SCIENTIST_START_STAGE"] = str(start_stage)
    config_path = "bfts_config.yaml"
    idea_config_path = edit_bfts_config_file(
        config_path,
        idea_dir,
        idea_path_json,
    )

    perform_experiments_bfts(idea_config_path)
    experiment_results_dir = osp.join(idea_dir, "logs/0-run/experiment_results")
    if os.path.exists(experiment_results_dir):
        shutil.copytree(
            experiment_results_dir,
            osp.join(idea_dir, "experiment_results"),
            dirs_exist_ok=True,
        )

    aggregate_plots(base_folder=idea_dir, model=args.model_agg_plots)

    shutil.rmtree(osp.join(idea_dir, "experiment_results"))

    save_token_tracker(idea_dir)

    if not args.skip_writeup:
        writeup_success = False
        citations_text = gather_citations(
            idea_dir,
            num_cite_rounds=args.num_cite_rounds,
            small_model=args.model_citation,
        )
        for attempt in range(args.writeup_retries):
            print(f"Writeup attempt {attempt+1} of {args.writeup_retries}")
            if args.writeup_type == "normal":
                writeup_success = perform_writeup(
                    base_folder=idea_dir,
                    small_model=args.model_writeup_small,
                    big_model=args.model_writeup,
                    page_limit=8,
                    citations_text=citations_text,
                )
            else:
                writeup_success = perform_icbinb_writeup(
                    base_folder=idea_dir,
                    small_model=args.model_writeup_small,
                    big_model=args.model_writeup,
                    page_limit=4,
                    citations_text=citations_text,
                )
            if writeup_success:
                break

        if not writeup_success:
            print("Writeup process did not complete successfully after all retries.")

    save_token_tracker(idea_dir)

    if not args.skip_review and not args.skip_writeup:
        # Perform paper review if the paper exists
        pdf_path = find_pdf_path_for_review(idea_dir)
        if os.path.exists(pdf_path):
            print("Paper found at: ", pdf_path)
            paper_content = load_paper(pdf_path)
            client, client_model = create_client(args.model_review)
            review_text = perform_review(paper_content, client_model, client)
            review_img_cap_ref = perform_imgs_cap_ref_review(
                client, client_model, pdf_path
            )
            with open(osp.join(idea_dir, "review_text.txt"), "w") as f:
                f.write(json.dumps(review_text, indent=4))
            with open(osp.join(idea_dir, "review_img_cap_ref.json"), "w") as f:
                json.dump(review_img_cap_ref, f, indent=4)
            print("Paper review completed.")

    print("Start cleaning up processes")
    # Kill all mp and torch processes associated with this experiment
    import psutil
    import signal

    # Get the current process and all its children
    current_process = psutil.Process()
    children = current_process.children(recursive=True)

    # First try graceful termination
    for child in children:
        try:
            child.send_signal(signal.SIGTERM)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Wait briefly for processes to terminate
    gone, alive = psutil.wait_procs(children, timeout=3)

    # If any processes remain, force kill them
    for process in alive:
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Additional cleanup: find orphaned child processes of this experiment only
    exp_dir_name = idea_dir if 'idea_dir' in dir() else ""
    keywords = ["bfts", "experiment"]
    for proc in psutil.process_iter(["name", "cmdline", "ppid"]):
        try:
            cmdline = " ".join(proc.cmdline()).lower()
            if any(keyword in cmdline for keyword in keywords):
                if exp_dir_name and exp_dir_name.lower() not in cmdline:
                    continue
                if proc.pid == current_process.pid:
                    continue
                if proc.ppid() == current_process.pid:
                    proc.send_signal(signal.SIGTERM)
                    proc.wait(timeout=3)
                    if proc.is_running():
                        proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired):
            continue

    # Finally, terminate the current process
    # current_process.send_signal(signal.SIGTERM)
    # try:
    #     current_process.wait(timeout=3)
    # except psutil.TimeoutExpired:
    #     current_process.kill()

    # exit the program
    sys.exit(0)

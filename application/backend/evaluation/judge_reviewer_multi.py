import argparse
import os
import pickle
from tqdm import tqdm
import concurrent.futures
from judge_reviewer import JudgeReviewer

"""
Compare a list of PDF files pairwise using JudgeReviewer and save results to a pickle.
Output is a tuple: (comparison_matrix, filenames_list)
- comparison_matrix is an NxN list where matrix[i][j] == 1 means file i is preferred over file j,
  0 means file j preferred over file i, and None means no decision/unknown.
- filenames_list is the list of file paths in the same order as the matrix indices.
"""


def compare_all(files=None, query_files=None, target_files=None, vote_number=3, model="gpt-5", outfile="pairwise_results.pkl", logdir=None, num_workers=2):
    """
    Compare files pairwise. Two modes supported:
    - Square mode: provide `files` (list) and it compares all pairs among them (NxN symmetric matrix stored as upper-triangle comparisons filled symmetrically).
    - Rectangular mode: provide `query_files` and `target_files` (both lists). Compares every query against every target and returns an MxN matrix (no symmetry).

    Returns the comparison matrix.
    """

    rectangular = False
    pairs = []

    if query_files is not None and target_files is not None:
        queries = list(query_files)
        targets = list(target_files)
        m = len(queries)
        n = len(targets)
        matrix = [[None for _ in range(n)] for _ in range(m)]

        for i in range(m):
            for j in range(n):
                pairs.append((i, j, queries[i], targets[j]))

        print(f"Running {len(pairs)} pairwise comparisons (queries: {m} x targets: {n}) with up to {num_workers} workers...")
        rectangular = True

    elif files is not None:
        files = list(files)
        n = len(files)
        matrix = [[None for _ in range(n)] for _ in range(n)]

        for i in range(n):
            for j in range(i + 1, n):
                pairs.append((i, j, files[i], files[j]))

        print(f"Running {len(pairs)} pairwise comparisons with up to {num_workers} workers...")

    else:
        raise ValueError("Either provide `files` or both `query_files` and `target_files`.")

    def run_pair(item):
        i, j, file_i, file_j = item
        # Display indices relative to their respective lists when rectangular
        if rectangular:
            print(f"Comparing query ({i+1}) '{file_i}' vs target ({j+1}) '{file_j}'")
        else:
            print(f"Comparing ({i+1}/{n}) '{file_i}' vs ({j+1}/{n}) '{file_j}'")

        logfile = None
        if logdir:
            os.makedirs(logdir, exist_ok=True)
            logfile = os.path.join(logdir, f"judge_{os.path.basename(file_i)}_v_{os.path.basename(file_j)}.log")

        judge = JudgeReviewer(file_i, file_j, vote_number=vote_number, model=model, logfile=logfile)
        try:
            score_dict, cost, reasonings = judge.majority_vote()
        except Exception as e:
            print(f"Error comparing '{file_i}' and '{file_j}': {e}")
            score_dict = None
            cost = 0

        return (i, j, score_dict, cost)

    total_cost = 0
    for p in tqdm(pairs):
        i, j, score_dict, cost = run_pair(p)
        if score_dict is None:
            matrix[i][j] = None
            if not rectangular:
                matrix[j][i] = None
        else:
            matrix[i][j] = score_dict[0] - score_dict[1]
            if not rectangular:
                matrix[j][i] = score_dict[1] - score_dict[0]
        total_cost += cost

    print(f"Total cost: {total_cost}")



    # with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
    #     future_to_pair = {executor.submit(run_pair, p): p for p in pairs}
    #     for future in concurrent.futures.as_completed(future_to_pair):
    #         i, j, majority = future.result()

    #         if majority is None:
    #             matrix[i][j] = None
    #             matrix[j][i] = None
    #         elif majority == 0:
    #             matrix[i][j] = 1
    #             matrix[j][i] = 0
    #         elif majority == 1:
    #             matrix[i][j] = 0
    #             matrix[j][i] = 1
    #         else:
    #             matrix[i][j] = None
    #             matrix[j][i] = None

    # Save results. For rectangular comparisons we save (matrix, queries, targets), otherwise (matrix, files)
    with open(outfile, "wb") as f:
        if rectangular:
            pickle.dump((matrix, queries, targets), f)
        else:
            pickle.dump((matrix, files), f)

    print(f"Saved pairwise results to '{outfile}'")
    return matrix


def main():
    parser = argparse.ArgumentParser(description="Compare multiple scientific PDFs pairwise using an LLM judge.")
    parser.add_argument("--files", nargs="+", help="List of PDF file paths to compare (at least 2).", required=False)
    parser.add_argument("--query_files", nargs="+", help="Optional list of query PDF file paths.")
    parser.add_argument("--target_files", nargs="+", help="Optional list of target PDF file paths.")
    parser.add_argument("--vote_number", type=int, default=3, help="Number of votes per order (AB and BA)")
    parser.add_argument("--model", type=str, default="gpt-5", help="Model name to use for the judge")
    parser.add_argument("--outfile", type=str, default="pairwise_results.pkl", help="Pickle file to save results")
    parser.add_argument("--logdir", type=str, default=None, help="Optional directory to write per-pair LLM outputs")
    parser.add_argument("--num_workers", type=int, default=2, help="Maximum number of worker threads for parallel pairwise comparisons")
    args = parser.parse_args()

    # Determine mode: rectangular if both query_files and target_files provided, otherwise square using `files`.
    if args.query_files is not None or args.target_files is not None:
        if args.query_files is None or args.target_files is None:
            parser.error("Provide both --query_files and --target_files for rectangular comparisons.")
        if len(args.query_files) < 1 or len(args.target_files) < 1:
            parser.error("Provide at least one query and one target file for rectangular comparisons.")

        compare_all(query_files=args.query_files, target_files=args.target_files, vote_number=args.vote_number, model=args.model, outfile=args.outfile, logdir=args.logdir, num_workers=args.num_workers)
    else:
        if len(args.files) < 2:
            parser.error("Provide at least two files to compare.")

        compare_all(files=args.files, vote_number=args.vote_number, model=args.model, outfile=args.outfile, logdir=args.logdir, num_workers=args.num_workers)


if __name__ == "__main__":
    main() 
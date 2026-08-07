import argparse
import os
import pickle
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

"""
Load a pickle saved by `judge_reviewer_multi.py` (tuple: (matrix, files)) and plot a heatmap.
- The matrix may contain None entries; these are shown as blank/NaN in the heatmap.
- X and Y axis labels use the basename of each file.
"""


def plot_matrix(matrix, row_files, col_files=None, outfile=None, annotate=False, cmap="coolwarm"):
    """
    Plot a matrix where rows correspond to `row_files` and columns to `col_files`.
    If `col_files` is None, assumes a square matrix and uses `row_files` for both axes.
    """
    arr = np.array(matrix, dtype=float)
    # Replace None (object -> nan) if present
    arr = np.where(np.equal(arr, None), np.nan, arr).astype(float)

    row_labels = [os.path.basename(f) for f in row_files]
    if col_files is None:
        col_labels = row_labels
    else:
        col_labels = [os.path.basename(f) for f in col_files]

    plt.figure(figsize=(max(6, len(col_labels) * 0.6), max(5, len(row_labels) * 0.6)), dpi=200)
    sns.set(style="white")

    mask = np.isnan(arr)

    ax = sns.heatmap(arr, mask=mask, cmap=cmap, center=0, annot=arr if annotate else None,
                     fmt=".0f" if annotate else None, linewidths=.5, cbar_kws={"label": "Row win - Col win"})

    ax.set_xticks(np.arange(len(col_labels)) + 0.5)
    ax.set_yticks(np.arange(len(row_labels)) + 0.5)
    ax.set_xticklabels(col_labels, rotation=45, ha="right")
    ax.set_yticklabels(row_labels, rotation=0)

    plt.tight_layout()

    if outfile:
        plt.savefig(outfile, dpi=300)
        print(f"Saved heatmap to {outfile}")
    else:
        plt.show()


def main():
    parser = argparse.ArgumentParser(description="Plot pairwise comparison heatmap saved as a pickle.")
    parser.add_argument("input", help="Path to the pickle file containing (matrix, files) or (matrix, queries, targets)")
    parser.add_argument("--outfile", help="If provided, save the plot to this path instead of showing it")
    parser.add_argument("--annotate", action="store_true", help="Annotate cells with numeric values")
    parser.add_argument("--cmap", default="coolwarm", help="Matplotlib colormap to use")
    args = parser.parse_args()

    with open(args.input, "rb") as f:
        data = pickle.load(f)

    # Support two formats: (matrix, files) or (matrix, queries, targets)
    if isinstance(data, tuple) and len(data) == 2:
        matrix, files = data
        plot_matrix(matrix, files, outfile=args.outfile, annotate=args.annotate, cmap=args.cmap)
    elif isinstance(data, tuple) and len(data) == 3:
        matrix, queries, targets = data
        plot_matrix(matrix, queries, col_files=targets, outfile=args.outfile, annotate=args.annotate, cmap=args.cmap)
    else:
        raise ValueError("Unrecognized pickle format. Expected (matrix, files) or (matrix, queries, targets).")


if __name__ == "__main__":
    main() 
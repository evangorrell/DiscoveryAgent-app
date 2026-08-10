# Evaluation Scripts

Scripts for evaluating and comparing scientific papers using LLM judges.

## Scripts

### `judge_reviewer.py`
Compare two scientific PDFs using an LLM judge.

```bash
python judge_reviewer.py paper1.pdf paper2.pdf --vote_number 3
```

**Options:**
- `--vote_number`: Number of votes per order (default: 3)
- `--logfile`: Optional file to write LLM outputs

### `judge_reviewer_multi.py`
Compare multiple PDFs pairwise and save results to a pickle file.

```bash
python judge_reviewer_multi.py paper1.pdf paper2.pdf paper3.pdf --outfile results.pkl
```

**Options:**
- `--vote_number`: Number of votes per order (default: 3)
- `--model`: Model name (default: gpt-5)
- `--outfile`: Output pickle file (default: pairwise_results.pkl)
- `--logdir`: Directory for per-pair logs
- `--num_workers`: Number of parallel workers (default: 2)

### `plot_pairwise_results.py`
Visualize pairwise comparison results as a heatmap.

```bash
python plot_pairwise_results.py results.pkl --outfile heatmap.png
```

**Options:**
- `--outfile`: Save plot to file (default: show interactively)
- `--annotate`: Show numeric values in cells
- `--cmap`: Colormap name (default: coolwarm)

## Workflow

1. **Compare papers**: `judge_reviewer_multi.py paper1.pdf paper2.pdf paper3.pdf`
2. **Visualize results**: `plot_pairwise_results.py pairwise_results.pkl`

## Output

- **Pickle file**: Contains comparison matrix and file list
- **Heatmap**: Shows which papers are preferred over others
- **Logs**: Detailed LLM reasoning for each comparison 
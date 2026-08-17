# AIR Workflow Runner

This directory contains a general AIR workflow runner (`run_air.py`) that can be used to analyze any dataset, along with example scripts for specific datasets.

## General Script: `run_air.py`

The `run_air.py` script is a general-purpose AIR workflow runner that takes configurable parameters for dataset description, working directory, and analysis name.

### Usage

```bash
python run_air.py --working_directory <path> --analysis_name <name> [options]
```

### Required Arguments

- `--working_directory`: Path to the directory containing the dataset
- `--analysis_name`: Name for the analysis (e.g., 'eeg_analysis', 'dam_analysis')

### Dataset Description (choose one)

- `--dataset_description`: Dataset description as text
- `--data_description_file`: Path to file containing dataset description

### Optional Arguments

- `--num_queries`: Number of queries to generate (default: 5)
- `--execution_max_turns`: Maximum turns for execution agent. `0` (the default) lets it run until it finishes the task
- `--hypothesis_model`: Model for hypothesis generation (default: claude-sonnet-4-6)
- `--omit_deepresearch`: Omit deep research agent
- `--omit_singlesearch`: Omit single search agent

## Example Scripts

### Python Examples

#### 1. EEG Analysis (`run_eeg_example.py`)

Runs AIR workflow on the epilepsy EEG dataset.

```bash
python run_eeg_example.py
```

**Dataset**: Single-channel EEG measurements from 500 subjects with seizure detection labels.

#### 2. Protein-Peptide Analysis (`run_protein_peptide_example.py`)

Runs AIR workflow on the ACE2 protein-peptide interaction dataset.

```bash
python run_protein_peptide_example.py
```

**Dataset**: Protein-peptide interaction data with 58 ACE2 binders and 5072 non-binders.

#### 3. Dam Analysis (`run_dam_example.py`)

Runs AIR workflow on the US dams dataset using a file-based description.

```bash
python run_dam_example.py
```

**Dataset**: Information on every dam in the United States.

### Shell Script Examples

For easier execution, shell script versions are available in the `examples/` directory:

```bash
# EEG Analysis
./examples/run_eeg_analysis.sh

# Protein-Peptide Analysis  
./examples/run_protein_peptide_analysis.sh

# Dam Analysis
./examples/run_dam_analysis.sh
```

The shell scripts provide the same functionality as the Python examples but with better error handling and path resolution.

## Creating Your Own Analysis

To create a new analysis for a different dataset:

1. **Create a dataset description** (either as text or in a file)
2. **Set up the working directory** with your data files
3. **Run the general script** with appropriate parameters

### Example: Custom Dataset

```bash
python run_air.py \
    --working_directory "/path/to/your/data" \
    --analysis_name "my_analysis" \
    --dataset_description "Your dataset description here..." \
    --num_queries 10 \
    --execution_max_turns 15
```

### Example: Using File-based Description

```bash
python run_air.py \
    --working_directory "/path/to/your/data" \
    --analysis_name "my_analysis" \
    --data_description_file "dataset_description.txt"
```

## Key Features

- **Flexible dataset descriptions**: Support for both inline text and file-based descriptions
- **Configurable agents**: Option to omit deep research or single search agents
- **Customizable parameters**: Adjust number of queries, execution turns, and model choices
- **Automatic output organization**: Creates timestamped output directories
- **Error handling**: Robust error handling for file operations and workflow execution

## Migration from Original Scripts

The original dataset-specific scripts have been consolidated into this general approach:

- **Original**: `eeg_modular.py`, `pp_modular.py`, `dam_modular.py`
- **New**: `run_air.py` + example scripts

The general script maintains all the functionality of the original scripts while providing a unified interface for any dataset. 
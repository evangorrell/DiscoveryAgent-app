# AIR Workflow Examples

This directory contains shell script examples that demonstrate how to use the general `run_air.py` script for different datasets.

## Available Examples

### 1. EEG Analysis
**Script**: `run_eeg_analysis.sh`
**Dataset**: Epilepsy EEG measurements from 500 subjects
**Description**: Single-channel EEG data with seizure detection labels

```bash
./run_eeg_analysis.sh
```

### 2. Protein-Peptide Analysis
**Script**: `run_protein_peptide_analysis.sh`
**Dataset**: ACE2 protein-peptide interaction data
**Description**: 58 ACE2 binders and 5072 non-binders

```bash
./run_protein_peptide_analysis.sh
```

### 3. Dam Analysis
**Script**: `run_dam_analysis.sh`
**Dataset**: US dams information
**Description**: Comprehensive data on every dam in the United States

```bash
./run_dam_analysis.sh
```

## Usage

All scripts are executable and can be run directly from this directory:

```bash
cd AIR/scripts/examples
./run_eeg_analysis.sh
```

Or from any location:

```bash
AIR/scripts/examples/run_eeg_analysis.sh
```

## Script Features

- **Error handling**: Scripts exit on any error (`set -e`)
- **Path resolution**: Automatically finds the correct AIR scripts directory
- **File validation**: Dam analysis checks for required data files
- **Clear output**: Provides informative messages about what's happening

## Customization

To modify these examples for your own datasets:

1. **Copy a script** and rename it for your dataset
2. **Update the dataset description** in the script
3. **Change the working directory** path
4. **Modify the analysis name**
5. **Adjust parameters** like `--num_queries` or `--execution_max_turns`

**Important**: Before running these scripts, you need to update the paths to match your system:

- **Working directory paths**: Change `/Users/owenqueen/Desktop/stanford_research/ai_researcher/AIR/...` to your actual AIR installation path
- **Data file paths**: Update paths to point to your actual dataset locations
- **AIR scripts directory**: Ensure the script can find the `run_air.py` file

### Example Customization

```bash
# Copy an existing script
cp run_eeg_analysis.sh run_my_dataset_analysis.sh

# Edit the script to change:
# - EEG_DESCRIPTION variable
# - --working_directory path
# - --analysis_name
# - Any other parameters
```

## Prerequisites

- Python environment with AIR dependencies installed
- Access to the dataset directories specified in each script
- For dam analysis: `nid_data_fields.md` file must exist in the dam data directory

## Troubleshooting

- **Permission denied**: Make sure scripts are executable (`chmod +x *.sh`)
- **File not found**: Check that dataset directories and files exist
- **Python errors**: Ensure AIR dependencies are installed and Python path is correct 
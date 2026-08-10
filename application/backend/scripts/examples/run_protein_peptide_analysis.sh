#!/bin/bash

# Protein-Peptide Analysis Example Script
# This script demonstrates how to use the general run_air.py script for protein-peptide data analysis.
# It runs the AIR workflow on the ACE2 protein-peptide interaction dataset.

set -e  # Exit on any error

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AIR_SCRIPTS_DIR="$(dirname "$SCRIPT_DIR")"

echo "Running protein-peptide analysis..."
echo "Script directory: $SCRIPT_DIR"
echo "AIR scripts directory: $AIR_SCRIPTS_DIR"
echo

# Run the AIR workflow
cd "$AIR_SCRIPTS_DIR"
python run_air.py \
    --working_directory "/Users/owenqueen/Desktop/stanford_research/ai_researcher/AIR/data/protpep_data" \
    --analysis_name "protein_peptide_analysis" \
    --num_queries 5 \
    --execution_max_turns 10 \
    --hypothesis_model "claude-sonnet-4-6"

echo
echo "Protein-peptide analysis completed successfully!" 
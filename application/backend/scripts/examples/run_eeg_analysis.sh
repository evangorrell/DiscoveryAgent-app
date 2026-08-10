#!/bin/bash

# EEG Analysis Example Script
# This script demonstrates how to use the general run_air.py script for EEG data analysis.
# It runs the AIR workflow on the epilepsy EEG dataset.

python3 scripts/run_air.py \
    --working_directory "/home/users/oqueen/discoveryagent/data/epilepsy_eeg_data" \
    --analysis_name "eeg_analysis" \
    --num_queries 5 \
    --execution_max_turns 10 \
    --hypothesis_model "claude-sonnet-4-6" \
    --omit_deepresearch
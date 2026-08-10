#!/bin/bash

# Dam Analysis Example Script
# This script demonstrates how to use the general run_air.py script for dam data analysis.
# It runs the AIR workflow on the US dams dataset using a file-based description.
python3 scripts/run_air.py \
    --working_directory "/home/users/oqueen/discoveryagent/data/dam_data/" \
    --analysis_name "dam_analysis" \
    --num_queries 5 \
    --execution_max_turns 10 \
    --hypothesis_model "claude-sonnet-4-6"


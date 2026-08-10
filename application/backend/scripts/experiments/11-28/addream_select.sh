#!/usr/bin/bash
#SBATCH --job-name=AD_q5_r30
#SBATCH --output=/home/groups/jamesz/oqueen/discoveryagent/scripts/experiments/11-28/out/%x.%j.out
#SBATCH --error=/home/groups/jamesz/oqueen/discoveryagent/scripts/experiments/11-28/out/%x.%j.out
#SBATCH --time=04:00:00
#SBATCH -p jamesz
#SBATCH -c 1
#SBATCH --mem=128GB

cd /home/groups/jamesz/oqueen/discoveryagent

# Run everything inside the container with srun + singularity exec:
srun singularity exec /home/groups/jamesz/oqueen/node_22.sif bash -c "
    cd /home/groups/jamesz/oqueen/discoveryagent

    # Export PATH
    export PATH=\"\$HOME/discoveryagent/node_modules/.bin:\$PATH\"

    # Activate env
    source /home/groups/jamesz/oqueen/discoveryagent/da_cc_venv/bin/activate

    python3 -m scripts.run_hb_sherlock \
        --dataset_name "ad_split" \
        --analysis_name "addream_select_q5_e20" \
        --num_rounds 1 \
        --num_branches 1 \
        --num_exploration_rounds 1 \
        --num_queries 5 \
        --execution_max_turns 30 \
        --hypothesis_model "claude-sonnet-4-5-20250929" \
        --execution_model "claude-sonnet-4-5-20250929" \
        --review_search \
        --review_optimization_steps 2 \
        --review_design \
        --human-selection \
        --n-candidate-hypotheses 3 \
        --sub_agent_architecture \
        --hold_reviews_in_exploratory_phase \
        --git_tracking \
        --omit_report_summarization \
        --no-auto-hypothesis
    "
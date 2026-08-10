#!/usr/bin/bash
#SBATCH --job-name=se_hb_n5_q6_e30_r2
#SBATCH --output=/home/groups/jamesz/oqueen/discoveryagent/scripts/experiments/11-08_labmeeting/se_n5_q5/out/%x.%j.out
#SBATCH --error=/home/groups/jamesz/oqueen/discoveryagent/scripts/experiments/11-08_labmeeting/se_n5_q5/out/%x.%j.out
#SBATCH --time=12:00:00
#SBATCH -p jamesz
#SBATCH -c 1
#SBATCH --mem=16GB

cd /home/groups/jamesz/oqueen/discoveryagent

# Run everything inside the container with srun + singularity exec:
srun singularity exec /home/groups/jamesz/oqueen/node_22.sif bash -c "
    cd /home/groups/jamesz/oqueen/discoveryagent

    # Export PATH
    export PATH=\"\$HOME/discoveryagent/node_modules/.bin:\$PATH\"

    # Activate env
    source /home/groups/jamesz/oqueen/discoveryagent/da_cc_venv/bin/activate

    python3 -m scripts.run_hb_sherlock \
        --dataset_name "starembed_clf" \
        --analysis_name "starembed_hb_n5_q6_e30_r2" \
        --num_rounds 5 \
        --num_branches 1 \
        --num_exploration_rounds 1 \
        --num_queries 7 \
        --execution_max_turns 30 \
        --hypothesis_model "claude-sonnet-4-5-20250929" \
        --execution_model "claude-sonnet-4-5-20250929" \
        --review_search \
        --review_optimization_steps 2 \
        --review_design \
        --pressure_multiturn_prompt \
        --sub_agent_architecture \
        --git_tracking \
        --omit_report_summarization
    "
#!/usr/bin/bash
#SBATCH --job-name=AD_resume3
#SBATCH --output=/home/groups/jamesz/oqueen/discoveryagent/scripts/experiments/11-28/out/%x.%j.out
#SBATCH --error=/home/groups/jamesz/oqueen/discoveryagent/scripts/experiments/11-28/out/%x.%j.out
#SBATCH --time=06:00:00
#SBATCH -p jamesz
#SBATCH -c 1
#SBATCH --mem=128GB

cd /home/groups/jamesz/oqueen/discoveryagent

CHECKPOINT_DIR=/scratch/users/oqueen/AIR_project/active/LOGS/ad_split_data/addream_select_q5_e20_20251130_210148/checkpoints/round_2

# Run everything inside the container with srun + singularity exec:
srun singularity exec /home/groups/jamesz/oqueen/node_22.sif bash -c "
    cd /home/groups/jamesz/oqueen/discoveryagent

    # Export PATH
    export PATH=\"\$HOME/discoveryagent/node_modules/.bin:\$PATH\"

    # Activate env
    source /home/groups/jamesz/oqueen/discoveryagent/da_cc_venv/bin/activate

    python3 -m scripts.run_hb_sherlock \
        --config-file ${CHECKPOINT_DIR}/config.json \
        --num_rounds 1 \
        --selected-hypothesis-file ${CHECKPOINT_DIR}/selected_hypothesis.md \
        --background_report_path ${CHECKPOINT_DIR}/memory_report.md
    "
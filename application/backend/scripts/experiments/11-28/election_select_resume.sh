CHECKPOINT_DIR=/scratch/users/oqueen/AIR_project/active/LOGS/election_data/election_select_q5_e20_20251130_130400/checkpoints/round_2

python3 -m scripts.run_hb_sherlock \
    --config-file ${CHECKPOINT_DIR}/config.json \
    --num_rounds 1 \
    --selected-hypothesis-file ${CHECKPOINT_DIR}/selected_hypothesis.md \
    --background_report_path ${CHECKPOINT_DIR}/memory_report.md
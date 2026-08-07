#!/bin/bash

# ==============================================================================
# SAFE_ENV.SH: Process Shield for Claude-Agent-SDK in Singularity
# ==============================================================================

# 1. ENVIRONMENT STABILITY
# Load system-level bash settings if they exist to maintain terminal behavior
if [ -f /etc/bash.bashrc ]; then
    source /etc/bash.bashrc
fi

# IMPORTANT: Allow aliases to be expanded even in non-interactive shells
# (e.g., when the agent runs 'bash -c "pkill python"')
shopt -s expand_aliases

# 2. IDENTITY TRACKING
# Protect the Python Agent (Parent of this shell) and the current Shell (Self)
export AGENT_PID=$PPID
export SHELL_PID=$$
export PROTECTED_PIDS="$AGENT_PID $SHELL_PID"

# 3. SAFETY LOGIC
# This function wraps 'kill' commands to filter out the agent's PIDs
safe_kill_wrapper() {
    local cmd_type=$1
    shift
    
    # Check if the command is trying to kill everything (-1) or the current group (0)
    # These are the most common ways an agent accidentally commits suicide.
    for arg in "$@"; do
        if [[ "$arg" == "-1" || "$arg" == "0" ]]; then
            echo "[Guard] BLOCKING broad kill signal: 'kill $arg'. Use specific PIDs instead."
            return 1
        fi
    done

    # For pkill/killall, we find the PIDs first, then filter, then kill
    if [[ "$cmd_type" == "pkill" || "$cmd_type" == "killall" ]]; then
        # Find all PIDs matching the pattern
        local targets=$(pgrep "$@")
        
        # Filter out the protected PIDs using grep -v
        # We use -E (regex) to match any of the space-separated PIDs in PROTECTED_PIDS
        local safe_targets=$(echo "$targets" | grep -v -E "($(echo $PROTECTED_PIDS | tr ' ' '|'))")
        
        if [ -n "$safe_targets" ]; then
            echo "[Guard] Redirecting $cmd_type to safe targets: $safe_targets"
            kill -9 $safe_targets
        else
            echo "[Guard] No safe targets found for '$cmd_type $@'. Shielding agent."
        fi
    else
        # Standard 'kill [PID]' command: Check if the target is protected
        for pid in "$@"; do
            if [[ " $PROTECTED_PIDS " =~ " $pid " ]]; then
                echo "[Guard] BLOCKING attempt to kill protected PID: $pid"
            else
                command kill "$@"
            fi
        done
    fi
}

# 4. ALIAS INJECTION
# Overwrite standard commands with our safe wrapper
alias pkill='safe_kill_wrapper pkill'
alias killall='safe_kill_wrapper killall'
alias kill='safe_kill_wrapper kill'

# 5. USER FEEDBACK
echo "----------------------------------------------------------------"
echo "🛡️  AGENT SELF-PRESERVATION ACTIVE"
echo "Protected Agent PID: $AGENT_PID"
echo "Protected Shell PID: $SHELL_PID"
echo "----------------------------------------------------------------"
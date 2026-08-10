# Singularity Security Guide

Written by Gemini 3 pro.

## 🛡️ Agent Self-Preservation: Implementation Guide

### 1. Hardened Singularity Execution

To isolate the agent, use the `--pid` and `--no-privs` flags. This ensures the container has its own private process tree and cannot affect the host or other users' jobs.

**Recommended Command:**

```bash
srun --pids-limit=200 singularity exec --nv --pid --no-privs \
    /path/to/your_container.sif \
    bash --init-file /path/to/safe_env.sh -c "
        source /path/to/your_env/bin/activate
        python3 your_agent_entrypoint.py
    "

```

* **`--pid`**: Creates an isolated PID namespace. The agent will see itself as a low-numbered PID (e.g., PID 7) and cannot see processes outside the container.
* **`--pids-limit`**: A Slurm-level guardrail to prevent the agent from spawning infinite processes (fork-bombing).

---

### 2. The Shell-Level "Shield" (`safe_env.sh`)

Create a shell script that the container loads upon entry. This script aliases destructive commands to "self-aware" versions that exclude the agent’s own PID.

**File:** `safe_env.sh`

```bash
#!/bin/bash
# Protect the Python Agent (Parent) and the current Shell (Child)
export PROTECTED_PIDS="$PPID $$"

safe_kill() {
    local cmd=$1
    shift
    echo "[Guard] Intercepting $cmd... filtering protected PIDs: $PROTECTED_PIDS"
    
    # Identify targets, exclude the protected PIDs, and kill only the remainder
    local targets=$(pgrep "$@" | grep -v -E "($(echo $PROTECTED_PIDS | tr ' ' '|'))")
    
    if [ -n "$targets" ]; then
        kill -9 $targets
    else
        echo "[Guard] No safe targets found. Command ignored."
    fi
}

alias pkill='safe_kill pkill'
alias killall='safe_kill killall'

```

---

### 3. The SDK Interception Hook

The most deterministic defense is the `PreToolUse` hook. This logic runs inside your Python code **before** the agent sends a command to the shell.

**Implementation Logic:**

1. Intercept any call to the **`Bash`** tool.
2. Use a regex to check for broad patterns like `pkill python` or `killall`.
3. Automatically rewrite the command to explicitly exclude the agent's current PID using `os.getpid()`.

---

### 4. Background Task Detachment (`setsid`)

When the agent starts a long-running Python process, it should not run it as a direct child in its own process group. Using `setsid` detaches the process, so signals sent to the agent's group won't propagate to the background task (and vice versa).

**Correct Agent Command Pattern:**

```bash
setsid python3 long_running_script.py > output.log 2>&1 &

```

---

### 5. Summary of Protection Layers

| Layer | Mechanism | Protection Provided |
| --- | --- | --- |
| **Cluster (Slurm)** | `--pids-limit` | Prevents the agent from crashing the node with too many threads. |
| **Container (Singularity)** | `--pid` namespace | Restricts the "blast radius" to only the container's internals. |
| **SDK (Python Hook)** | `PreToolUse` Regex | Rewrites dangerous logic before it is even executed. |
| **Shell (Aliases)** | `safe_env.sh` | Final fallback if the agent tries to call `pkill` directly. |
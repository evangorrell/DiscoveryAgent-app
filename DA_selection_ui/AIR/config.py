"""
Configuration classes for AIR agents.

This module contains configuration classes that centralize the configuration
for different AIR agents, making it easier to manage and modify settings.
"""

import os
from dataclasses import dataclass, field
from typing import Optional, List, Any

# Load environment variables from .env file if it exists
from dotenv import load_dotenv
load_dotenv()

from .prompts.hyp_generation import HYP_GEN_SYSTEM_PROMPT_MODULAR


def get_slurm_resource_info():
    """
    Parse SLURM environment variables to get allocated resource information.
    
    Returns:
        str: Formatted string describing available resources, or None if not on SLURM
    """
    # Check if running on SLURM
    if not os.getenv('SLURM_JOB_ID'):
        return None
    
    resources = []
    
    # Get CPU information
    cpus = os.getenv('SLURM_CPUS_ON_NODE')
    if cpus:
        resources.append(f"CPUs: {cpus} cores")
    
    # Get memory information
    mem = os.getenv('SLURM_MEM_PER_NODE')
    if mem:
        # Convert to GB if it's in MB (SLURM typically provides in MB)
        try:
            mem_mb = int(mem)
            mem_gb = mem_mb / 1024
            resources.append(f"Memory: {mem_gb:.1f} GB ({mem_mb} MB)")
        except (ValueError, TypeError):
            resources.append(f"Memory: {mem}")
    
    # Get GPU information
    gpus = os.getenv('SLURM_JOB_GPUS') or os.getenv('SLURM_GPUS')
    cuda_devices = os.getenv('CUDA_VISIBLE_DEVICES')
    
    if gpus:
        resources.append(f"GPUs (SLURM): {gpus}")
    if cuda_devices:
        # Count the number of GPUs from CUDA_VISIBLE_DEVICES
        gpu_count = len(cuda_devices.split(',')) if cuda_devices else 0
        resources.append(f"GPUs (CUDA): {gpu_count} device(s) [{cuda_devices}]")
    
    if not resources:
        return None
    
    resource_str = "## Allocated Computing Resources\n\n"
    resource_str += "You are running on an HPC cluster with the following allocated resources:\n"
    resource_str += "\n".join(f"- {r}" for r in resources)
    resource_str += "\n\nPlease be mindful of these resource constraints when designing your analysis. "
    resource_str += "Consider parallelization opportunities if multiple CPUs are available, "
    resource_str += "and leverage GPU acceleration if GPUs are available and appropriate for your task."
    
    return resource_str


def setup_claude_code_environment():
    """
    Set up environment variables needed for Claude Agent SDK.
    """
    if not os.getenv('BASH_DEFAULT_TIMEOUT_MS'):
        os.environ['BASH_DEFAULT_TIMEOUT_MS'] = '1800000'  # 30 minutes

    if not os.getenv('BASH_MAX_TIMEOUT_MS'):
        os.environ['BASH_MAX_TIMEOUT_MS'] = '7200000'  # 2 hours


@dataclass
class LLMConfig:
    """Configuration for LLM settings."""
    model_name: str
    api_key: Optional[str] = None
    api_provider: Optional[str] = None
    # Model runtime settings
    temperature: float = 0.2
    frequency_penalty: float = 0.0
    max_tokens: Optional[int] = None  # Maximum tokens for output; None means use API default
    # MCP (Model Context Protocol) servers
    mcp_servers: Optional[List[dict]] = None
    system_prompt: Optional[str] = None # May not be used by some agents
    
    def __post_init__(self):
        """Auto-detect API provider if not specified."""
        if self.api_provider is None:
            if self.model_name.startswith(('gpt-', 'o1-', 'o3-')):
                self.api_provider = 'openai'
            elif self.model_name.startswith('claude-'):
                self.api_provider = 'anthropic'
            else:
                # Default to OpenAI for backward compatibility
                self.api_provider = 'openai'
        
        # Get API keys from environment if not provided
        if self.api_key is None:
            if self.api_provider == 'openai':
                self.api_key = os.getenv('OPENAI_API_KEY')
            elif self.api_provider == 'anthropic':
                self.api_key = os.getenv('ANTHROPIC_API_KEY')
            elif self.api_provider == 'perplexity':
                # Perplexity uses PERPLEXITY_API_KEY in the environment
                self.api_key = os.getenv('PERPLEXITY_API_KEY')
        
        # Validate API provider
        if self.api_provider not in ['openai', 'anthropic', 'perplexity']:
            raise ValueError("api_provider must be 'openai', 'anthropic' or 'perplexity'")
        
        # TODO: add support for TOGETHER and other LLMs if we need them



@dataclass
class ExecutionAgentConfig:
    """Configuration for ExecutionAgent."""
    llm_config: LLMConfig
    analysis_name: str
    num_analyses: int = 5
    max_iterations: int = 6
    output_home: str = "."
    log_home: str = "."
    available_packages: str = "numpy, pandas, matplotlib, seaborn, scipy"
    use_xml_context: bool = False
    use_code_context_for_nextstep: bool = True
    max_fix_attempts: int = 3
    
    # Image model configuration (optional)
    image_model_config: Optional[LLMConfig] = None
    
    def __post_init__(self):
        """Set up image model config if not provided."""
        if self.image_model_config is None:
            # Use the same config as the main LLM config
            self.image_model_config = self.llm_config


@dataclass
class AIRWorkflowConfig:
    """Configuration for AIRWorkflow."""
    execution_config: ExecutionAgentConfig
    use_report_manager: bool = True
    report_name: Optional[str] = None
    use_xml_context: bool = True


@dataclass
class ContextManagerConfig:
    """Configuration for context managers."""
    analysis_name: str
    output_home: str = "."
    log_home: str = "."
    available_packages: str = "numpy, pandas, matplotlib, seaborn, scipy"
    code_memory_max_size: Optional[int] = None


@dataclass
class HypothesisGenerationConfig:
    """Configuration for HypothesisGenerationAgent."""
    llm_config: LLMConfig
    log_dir: str = "logs"
    max_tokens: int = 8192
    system_prompt: str = field(default=HYP_GEN_SYSTEM_PROMPT_MODULAR)
    mcp_servers: Optional[List[dict]] = None
    multiturn_prompt: str = None # Is an intermediate prompt to drive the model to find more interesting hypotheses mid-research

    # Human selection mode parameters
    human_selection_mode: bool = False  # Enable human hypothesis selection
    n_candidate_hypotheses: int = 3     # Number of candidates to generate (when human_selection_mode=True)
    selected_hypothesis_file: Optional[str] = None  # Path to file containing human-selected hypothesis
    no_auto_hypothesis: bool = False    # Disable automatic hypothesis generation at end of rounds

    allow_director_to_stop: bool = False # Condition for automatic stop of directors
    generate_plan: bool = False # Generate a plan for each hypothesis
    # Citations support (Anthropic only)
    enable_citations: bool = False
    cache_documents: bool = False

# Default configurations
def get_default_hypothesis_config(
    model_name: str = "gpt-4o", 
    mcp_servers: Optional[List[dict]] = None
) -> HypothesisGenerationConfig:
    """Get default hypothesis generation configuration."""
    llm_config = LLMConfig(model_name=model_name)
    return HypothesisGenerationConfig(llm_config=llm_config, mcp_servers=mcp_servers)


def get_default_execution_config(
    analysis_name: str,
    model_name: str = "gpt-4o",
    image_model_name: Optional[str] = None
) -> ExecutionAgentConfig:
    """Get default execution agent configuration."""
    llm_config = LLMConfig(model_name=model_name)
    
    # Set up image model config if specified
    image_model_config = None
    if image_model_name:
        image_model_config = LLMConfig(model_name=image_model_name)
    
    return ExecutionAgentConfig(
        llm_config=llm_config,
        analysis_name=analysis_name,
        image_model_config=image_model_config
    )


def get_default_air_workflow_config(
    analysis_name: str,
    model_name: str = "gpt-4o",
    image_model_name: Optional[str] = None
) -> AIRWorkflowConfig:
    """Get default AIR workflow configuration."""
    execution_config = get_default_execution_config(
        analysis_name=analysis_name,
        model_name=model_name,
        image_model_name=image_model_name
    )
    return AIRWorkflowConfig(execution_config=execution_config)


def get_default_context_manager_config(analysis_name: str) -> ContextManagerConfig:
    """Get default context manager configuration."""
    return ContextManagerConfig(analysis_name=analysis_name)

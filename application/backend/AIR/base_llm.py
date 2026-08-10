import os, copy
from openai import OpenAI
from openai import BadRequestError
from openai.types.chat import ChatCompletionMessage
import anthropic
from anthropic import AnthropicBedrock
from .config import LLMConfig

from together import Together

MODEL_API_MAP = {
    "gpt-4o": "OPENAI",
    "gpt-4o-2024-11-20": "OPENAI",
    "gpt-4o-mini": "OPENAI",
    "o1": "OPENAI",
    "o1-mini": "OPENAI",
    "o3": "OPENAI",
    "o3-mini": "OPENAI",
    "o4-mini": "OPENAI",
    "o3-preview": "OPENAI",
    "claude-3-5-sonnet": "ANTHROPIC",
    "claude-3-haiku": "ANTHROPIC",
    # "llama-4-maverick": "TOGETHER",
    # "llama-3.3-70b": "TOGETHER",
    # "deepseek-r1": "TOGETHER",
    "claude-3-7-sonnet-20250219": "ANTHROPIC",
    "llama-4-maverick": "TOGETHER",
    "qwen2.5-72b":      "TOGETHER",
    "deepseek-r1":      "TOGETHER",
    "google/gemini-2.5-flash-preview": "OPENROUTER",
    "google/gemini-2.5-pro-preview-03-25": "OPENROUTER",
    # AWS Bedrock models (Claude via AWS)
    "anthropic.claude-3-5-sonnet-20241022-v2:0": "BEDROCK",
    "anthropic.claude-3-5-haiku-20241022-v1:0": "BEDROCK",
    "anthropic.claude-3-sonnet-20240229-v1:0": "BEDROCK",
    "anthropic.claude-3-haiku-20240307-v1:0": "BEDROCK",
    "anthropic.claude-3-opus-20240229-v1:0": "BEDROCK",
    # Convenience aliases for Bedrock (will be mapped to full model IDs)
    "bedrock-claude-3-5-sonnet": "BEDROCK",
    "bedrock-claude-3-5-haiku": "BEDROCK",
    "bedrock-claude-3-sonnet": "BEDROCK",
    "bedrock-claude-3-haiku": "BEDROCK",
    "bedrock-claude-3-opus": "BEDROCK",
}

# Mapping from convenience aliases to full Bedrock model IDs
BEDROCK_MODEL_ALIASES = {
    "bedrock-claude-3-5-sonnet": "anthropic.claude-3-5-sonnet-20241022-v2:0",
    "bedrock-claude-3-5-haiku": "anthropic.claude-3-5-haiku-20241022-v1:0",
    "bedrock-claude-3-sonnet": "anthropic.claude-3-sonnet-20240229-v1:0",
    "bedrock-claude-3-haiku": "anthropic.claude-3-haiku-20240307-v1:0",
    "bedrock-claude-3-opus": "anthropic.claude-3-opus-20240229-v1:0",
}

# TODO: update these costs
def openai_api_calculate_cost(response,model="gpt-4o"):
    # Pricing as of early March 2025
    pricing = {
        'o1': {
            'prompt': 15.0,
            'completion': 60.0,
        },
        'o3': {
            'prompt': 10.0,
            'completion': 40.0,
        },
        'gpt-4o': {
            'prompt': 2.5,
            'completion': 10.0,
        },
        'gpt-4o-2024-11-20': {
            'prompt': 2.5,
            'completion': 10.0,
        },
        'o3-mini': {
            'prompt': 1.10,
            'completion': 4.40,
        },
        'gpt-4o-mini': {
            'prompt': 0.30,
            'completion': 1.20,
        },
        "o4-mini": {
            "prompt": 1.10,
            "completion": 4.40,
        }
    }

    try:
        model_pricing = pricing[model]
    except KeyError:
        raise ValueError("Invalid model specified")

    prompt_cost = response.usage.prompt_tokens * model_pricing['prompt'] / 1000000
    completion_cost = response.usage.completion_tokens * model_pricing['completion'] / 1000000

    total_cost = prompt_cost + completion_cost
    # print(f"\nTokens used:  {usage['prompt_tokens']:,} prompt + {usage['completion_tokens']:,} completion = {usage['total_tokens']:,} tokens")
    # print(f"Total cost for {model}: ${total_cost:.4f}\n")

    return total_cost

def anthropic_api_calculate_cost(response, model="claude-3-5-sonnet"):
    # Pricing as of early 2024
    pricing = {
        "claude-3-7-sonnet-20250219": {
            'prompt': 3.0,
            'completion': 15.0,
        },
        "claude-opus-4-6": {
            'prompt': 5.0,
            'completion': 25.0,
        },
        "claude-3-5-sonnet": {
            'prompt': 3.0,
            'completion': 15.0,
        },
        "claude-3-haiku": {
            'prompt': 0.25,
            'completion': 1.25,
        },
        "claude-haiku-4-5-20251001": {
            'prompt': 1.0,
            'completion': 5.0,
        },
        "claude-sonnet-4-5-20250929": {
            'prompt': 3.0,
            'completion': 15.0,
        },
        "claude-sonnet-4-6": {
            'prompt': 3.0,
            'completion': 15.0,
        },
    }

    try:
        model_pricing = pricing[model]
    except KeyError:
        raise ValueError("Invalid model specified")

    prompt_cost = response.usage.input_tokens * model_pricing['prompt'] / 1000000
    completion_cost = response.usage.output_tokens * model_pricing['completion'] / 1000000

    total_cost = prompt_cost + completion_cost
    # print(f"\nTokens used: {response.usage.prompt_tokens:,} prompt + {response.usage.completion_tokens:,} completion = {response.usage.total_tokens:,} tokens")
    # print(f"Total cost for {model}: ${total_cost:.4f}\n")

    return total_cost

TOGETHER_MODEL_STRINGS = {
    "llama-4-maverick": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
    "qwen2.5-72b":      "Qwen/Qwen2.5-72B-Instruct-Turbo",
    "deepseek-r1":      "deepseek-ai/DeepSeek-R1",
}

TOGETHER_PRICING = {
    "llama-4-maverick": {"prompt": 0.27, "completion": 0.85},
    "qwen2.5-72b":      {"prompt": 0.60, "completion": 0.60},
    "deepseek-r1":      {"prompt": 3.00, "completion": 7.00},
}

def together_api_calculate_cost(response, model="llama-4-maverick"):
    try:
        price = TOGETHER_PRICING[model]
    except KeyError:
        raise ValueError("Invalid Together model for cost calc")

    prompt_cost = response.usage.prompt_tokens     * price["prompt"]     / 1_000_000
    completion_cost = response.usage.completion_tokens * price["completion"] / 1_000_000
    return prompt_cost + completion_cost

OPENROUTER_PRICING = {
    "google/gemini-2.5-flash-preview": {"prompt": 0.15, "completion": 0.60},
    "google/gemini-2.5-flash-preview:thinking": {"prompt": 0.15, "completion": 3.50},
    "google/gemini-2.5-pro-preview-03-25": {"prompt": 1.25, "output": 1.00},
}
def openrouter_api_calculate_cost(resp, model):
    price = OPENROUTER_PRICING[model]
    return (resp.usage.prompt_tokens  * price["prompt"] +
            resp.usage.completion_tokens * price["completion"]) / 1_000_000

# AWS Bedrock pricing (per 1M tokens) - prices may vary by region
BEDROCK_PRICING = {
    "anthropic.claude-3-5-sonnet-20241022-v2:0": {"prompt": 3.0, "completion": 15.0},
    "anthropic.claude-3-5-haiku-20241022-v1:0": {"prompt": 0.80, "completion": 4.0},
    "anthropic.claude-3-sonnet-20240229-v1:0": {"prompt": 3.0, "completion": 15.0},
    "anthropic.claude-3-haiku-20240307-v1:0": {"prompt": 0.25, "completion": 1.25},
    "anthropic.claude-3-opus-20240229-v1:0": {"prompt": 15.0, "completion": 75.0},
}

def bedrock_api_calculate_cost(response, model):
    """Calculate cost for AWS Bedrock API calls."""
    # Resolve alias to full model ID if needed
    resolved_model = BEDROCK_MODEL_ALIASES.get(model, model)

    try:
        price = BEDROCK_PRICING[resolved_model]
    except KeyError:
        # Default to Claude 3.5 Sonnet pricing if model not found
        price = BEDROCK_PRICING["anthropic.claude-3-5-sonnet-20241022-v2:0"]

    prompt_cost = response.usage.input_tokens * price["prompt"] / 1_000_000
    completion_cost = response.usage.output_tokens * price["completion"] / 1_000_000

    return prompt_cost + completion_cost

class OnePassLLM(object):
    def __init__(
            self,
            system_prompt: str = None,
            llm_config: LLMConfig = None,
        ):

        # Prefer an explicit LLMConfig if provided; otherwise construct a default one
        if llm_config is not None:
            self.llm_config = llm_config
        else:
            # default model_name kept for backward compatibility
            self.llm_config = LLMConfig(model_name="gpt-4o")

        # Expose model-level runtime settings from the config
        self.model_name = self.llm_config.model_name
        self.temperature = getattr(self.llm_config, "temperature", 0.2)
        self.frequency_penalty = getattr(self.llm_config, "frequency_penalty", 0.0)
        self.max_tokens = getattr(self.llm_config, "max_tokens", None)
        self.mcp_servers = getattr(self.llm_config, "mcp_servers", None)

        # Map config api_provider to the internal framework constants used in this file
        provider = (self.llm_config.api_provider or "").lower()
        if provider == "openai":
            self.api_framework = "OPENAI"
        elif provider == "anthropic":
            self.api_framework = "ANTHROPIC"
        elif provider == "together":
            self.api_framework = "TOGETHER"
        elif provider == "openrouter":
            self.api_framework = "OPENROUTER"
        elif provider == "bedrock":
            self.api_framework = "BEDROCK"
        else:
            # Fallback to trying to infer from model name for backwards compat
            if self.model_name in MODEL_API_MAP:
                self.api_framework = MODEL_API_MAP[self.model_name]
            else:
                raise NotImplementedError(f"{self.model_name} / {provider} not supported")

        # Set system prompt if provided
        if system_prompt is not None:
            self.set_system_prompt(system_prompt)

        # Initialize API clients using keys from the config (if provided) or fall back to env vars
        if self.api_framework == "OPENAI":
            self.api_system = "openai"
            openai_key = self.llm_config.api_key or os.getenv('ZOULAB_OPENAI_API_KEY') or os.getenv('OPENAI_API_KEY')
            self.client = OpenAI(api_key=openai_key)
        elif self.api_framework == "ANTHROPIC":
            self.api_system = "anthropic"
            anthropic_key = self.llm_config.api_key or os.getenv('ANTHROPIC_API_KEY')
            self.client = anthropic.Anthropic(api_key=anthropic_key)
        elif self.api_framework == "TOGETHER":
            self.api_system = "together"
            together_key = self.llm_config.api_key or os.getenv("TOGETHER_API_KEY")
            self.client = Together(api_key=together_key)
            # Swap the human-friendly alias for the real model string
            self.together_model = TOGETHER_MODEL_STRINGS.get(self.model_name, self.model_name)
        elif self.api_framework == "OPENROUTER":
            self.api_system = "openrouter"
            openrouter_key = self.llm_config.api_key or os.getenv("OPENROUTER_API_KEY")
            self.client = OpenAI(
                api_key=openrouter_key,
                base_url="https://openrouter.ai/api/v1",
                # optional – makes your calls show up in OR rankings
                default_headers={
                    "HTTP-Referer": "https://your-site.com/",
                    "X-Title":      "OnePassAgent Demo"
                }
            )
        elif self.api_framework == "BEDROCK":
            self.api_system = "bedrock"
            # Get AWS credentials from config or environment
            aws_access_key = getattr(self.llm_config, 'aws_access_key_id', None) or os.getenv('AWS_ACCESS_KEY_ID')
            aws_secret_key = getattr(self.llm_config, 'aws_secret_access_key', None) or os.getenv('AWS_SECRET_ACCESS_KEY')
            aws_session_token = getattr(self.llm_config, 'aws_session_token', None) or os.getenv('AWS_SESSION_TOKEN')
            aws_region = getattr(self.llm_config, 'aws_region', None) or os.getenv('AWS_REGION', os.getenv('AWS_DEFAULT_REGION', 'us-east-1'))

            # Resolve model alias to full Bedrock model ID
            self.bedrock_model = BEDROCK_MODEL_ALIASES.get(self.model_name, self.model_name)

            # Initialize Bedrock client - supports both explicit credentials and default credential chain
            client_kwargs = {"aws_region": aws_region}
            if aws_access_key and aws_secret_key:
                client_kwargs["aws_access_key"] = aws_access_key
                client_kwargs["aws_secret_key"] = aws_secret_key
                if aws_session_token:
                    client_kwargs["aws_session_token"] = aws_session_token

            self.client = AnthropicBedrock(**client_kwargs)
        else:
            raise NotImplementedError(f"{self.model_name} not supported")

    def set_system_prompt(self, system_prompt: str):
        # ("o4" in self.model_name)
        if self.api_framework == "OPENAI" and (not (("o1" in self.model_name) or ("o3" in self.model_name) or ("o4" in self.model_name))) or (self.api_framework == "TOGETHER"):
            self.system_prompt = {"role": "system", "content": system_prompt}
        else:
            self.system_prompt = system_prompt

    def forward(self, 
            input: str, 
            max_tokens: int = None, 
            response_format = None, 
            get_cost: bool = False
        ):
        # Use the config's max_tokens if not explicitly provided
        if max_tokens is None:
            max_tokens = self.max_tokens if self.max_tokens is not None else 2000
        
        if self.api_framework == "OPENAI":
            if ("o1" in self.model_name) or ("o3" in self.model_name) or ("o4" in self.model_name):
                message_input = [{"role": "user", "content": self.system_prompt + "\n" + input}]
                #message_input["content"] += "\n" + input
                #message_input = [message_input]

                if response_format is None:
                    response = self.client.chat.completions.create(
                            model = self.model_name,
                            messages = message_input,
                        )
                    model_out = response.choices[0].message.content
                else:
                    response = self.client.beta.chat.completions.parse(
                            model = self.model_name,
                            messages = message_input,
                            response_format = response_format
                        )
                    model_out = response.choices[0].message.parsed
            else:
                message_input = [self.system_prompt, {"role": "user", "content": input}]

                if response_format is None:

                    response = self.client.chat.completions.create(
                            model = self.model_name,
                            messages = message_input,
                            temperature = self.temperature,
                            max_tokens = max_tokens,
                            frequency_penalty = self.frequency_penalty,
                    )
                    model_out = response.choices[0].message.content
                else:

                    response = self.client.beta.chat.completions.parse(
                            model = self.model_name,
                            messages = message_input,
                            temperature = self.temperature,
                            max_tokens = max_tokens,
                            frequency_penalty = self.frequency_penalty,
                            response_format = response_format,
                        )
                
                    model_out = response.choices[0].message.parsed
        elif self.api_framework == "ANTHROPIC":
            # For Anthropic models, we need to handle the system prompt differently
            # if isinstance(self.system_prompt, dict):
            #     system_content = self.system_prompt["content"]
            # else:
            system_content = self.system_prompt
            
            # Handle JSON structured input for Anthropic models
            if response_format is not None:
                # For Anthropic models, we need to use the tools parameter for JSON structured output
                # Extract the schema from the response_format
                # if hasattr(response_format, 'schema'):
                #     schema = response_format.schema
                # else:
                #     # If schema is not directly available, try to extract it from the response_format
                #     schema = response_format
                
                # Create the message for Anthropic API with tools parameter
                api_params = {
                    "model": self.model_name,
                    "max_tokens": max_tokens,
                    "temperature": self.temperature,
                    "system": system_content,
                    "messages": [
                        {"role": "user", "content": input}
                    ],
                    "tools": [{
                        "name": "analyze_experimental_evidence",
                        "description": "Analyze experimental evidence and provide structured findings",
                        "input_schema": response_format
                    }],
                    "tool_choice": {"type": "tool", "name": "analyze_experimental_evidence"}
                }
                
                # Add MCP servers and betas if configured
                if self.mcp_servers:
                    api_params["mcp_servers"] = self.mcp_servers
                    api_params["betas"] = ["mcp-client-2025-04-04"]
                
                response = self.client.beta.messages.create(**api_params) if self.mcp_servers else self.client.messages.create(**api_params)

                # Extract the tool call result
                if response.content and len(response.content) > 0:
                    if response.content[0].type == "tool_use":
                        tool_call = response.content[0]
                        # Check if the tool call has input data
                        if hasattr(tool_call, 'input') and tool_call.input:
                            model_out = tool_call.input
                        else:
                            # If input is empty, try to get text content or provide a default
                            if hasattr(response, 'text') and response.text:
                                model_out = response.text
                            else:
                                # If no valid output is found, return a default or error message
                                model_out = {"error": "No valid output generated by the model"}
                                import ipdb; ipdb.set_trace()
                                raise ValueError("No valid output generated by the model")
                    else:
                        # Handle other content types
                        model_out = response.content[0].text if hasattr(response.content[0], 'text') else str(response.content[0])
                else:
                    # Fallback if no content is available
                    model_out = {"error": "No content in the response"}
                    raise ValueError("No content in the response")

            else:
                # Standard text output for Anthropic models
                api_params = {
                    "model": self.model_name,
                    "max_tokens": max_tokens,
                    "temperature": self.temperature,
                    "system": system_content,
                    "messages": [
                        {"role": "user", "content": input}
                    ]
                }
                
                # Add MCP servers and betas if configured
                if self.mcp_servers:
                    api_params["mcp_servers"] = self.mcp_servers
                    api_params["betas"] = ["mcp-client-2025-04-04"]
                
                response = self.client.beta.messages.create(**api_params) if self.mcp_servers else self.client.messages.create(**api_params)
                
                model_out = response.content[0].text
        elif self.api_framework == "TOGETHER":
            # ensure the system prompt is a dict
            if isinstance(self.system_prompt, str):
                sys_msg = {"role": "system", "content": self.system_prompt}
            else:
                sys_msg = self.system_prompt

            message_input = [sys_msg, {"role": "user", "content": input}]
            if "deepseek" in self.model_name:
                max_tokens = None
                response = self.client.chat.completions.create(
                    model=self.together_model,
                    messages=message_input,
                    temperature=self.temperature,
                    frequency_penalty=self.frequency_penalty,
                )
            else:
                response = self.client.chat.completions.create(
                    model=self.together_model,
                    messages=message_input,
                    temperature=self.temperature,
                    max_tokens=max_tokens,
                    frequency_penalty=self.frequency_penalty,
                )
            model_out = response.choices[0].message.content

        elif self.api_framework == "OPENROUTER":
            message_input = [self.system_prompt, {"role": "user", "content": input}]
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=message_input,
                temperature=self.temperature,
                max_tokens=max_tokens,
                frequency_penalty=self.frequency_penalty,
            )
            model_out = response.choices[0].message.content

        elif self.api_framework == "BEDROCK":
            # AWS Bedrock uses the same messages API as Anthropic
            system_content = self.system_prompt

            # Handle JSON structured output for Bedrock models
            if response_format is not None:
                # Use tools parameter for structured output (same as Anthropic)
                api_params = {
                    "model": self.bedrock_model,
                    "max_tokens": max_tokens,
                    "temperature": self.temperature,
                    "system": system_content,
                    "messages": [
                        {"role": "user", "content": input}
                    ],
                    "tools": [{
                        "name": "analyze_experimental_evidence",
                        "description": "Analyze experimental evidence and provide structured findings",
                        "input_schema": response_format
                    }],
                    "tool_choice": {"type": "tool", "name": "analyze_experimental_evidence"}
                }

                response = self.client.messages.create(**api_params)

                # Extract the tool call result
                if response.content and len(response.content) > 0:
                    if response.content[0].type == "tool_use":
                        tool_call = response.content[0]
                        if hasattr(tool_call, 'input') and tool_call.input:
                            model_out = tool_call.input
                        else:
                            if hasattr(response, 'text') and response.text:
                                model_out = response.text
                            else:
                                model_out = {"error": "No valid output generated by the model"}
                                raise ValueError("No valid output generated by the model")
                    else:
                        model_out = response.content[0].text if hasattr(response.content[0], 'text') else str(response.content[0])
                else:
                    model_out = {"error": "No content in the response"}
                    raise ValueError("No content in the response")
            else:
                # Standard text output for Bedrock models
                api_params = {
                    "model": self.bedrock_model,
                    "max_tokens": max_tokens,
                    "temperature": self.temperature,
                    "system": system_content,
                    "messages": [
                        {"role": "user", "content": input}
                    ]
                }

                response = self.client.messages.create(**api_params)
                model_out = response.content[0].text

        if get_cost:
            if self.api_framework == "OPENAI":
                return model_out, openai_api_calculate_cost(response, model=self.model_name)
            elif self.api_framework == "ANTHROPIC":
                return model_out, anthropic_api_calculate_cost(response, model=self.model_name)
            elif self.api_framework == "TOGETHER":
                return model_out, together_api_calculate_cost(response, model=self.model_name)
            elif self.api_framework == "OPENROUTER":
                return model_out, openrouter_api_calculate_cost(response, model=self.model_name)
            elif self.api_framework == "BEDROCK":
                return model_out, bedrock_api_calculate_cost(response, model=self.bedrock_model)
        else:
            return model_out
    
    def __call__(self, input, max_tokens = None, get_cost = False):
        return self.forward(input, max_tokens = max_tokens, get_cost = get_cost)

class AnthropicCitationsLLM(object):
    """
    Minimal Anthropic client wrapper that issues a citations-enabled request.
    Kept separate from the standard OnePassLLM path so beta citation usage does
    not interfere with existing generation code paths.
    """

    def __init__(self, llm_config: LLMConfig, system_prompt: str, max_tokens: int = None):
        if llm_config.api_provider != "anthropic":
            raise ValueError("AnthropicCitationsLLM requires an anthropic api_provider.")

        api_key = llm_config.api_key or os.getenv("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model_name = llm_config.model_name
        self.temperature = getattr(llm_config, "temperature", 0.2)
        self.max_tokens = max_tokens or getattr(llm_config, "max_tokens", 1024)
        self.system_prompt = system_prompt

    def forward_with_documents(
        self,
        *,
        user_text: str,
        documents: list,
        betas: list = None,
        additional_messages: list = None,
        max_tokens: int = None,
    ):
        """
        Issue a citations-enabled request with plain-text documents.

        Args:
            user_text: Main user instruction or prompt.
            documents: List of document block dicts (each must set citations.enabled=True).
            betas: Deprecated. Beta headers are not needed for citations and are rejected
                   on some deployments. If provided, a ValueError is raised.
            additional_messages: Optional pre-existing messages to prepend.
            max_tokens: Optional override for max_tokens.
        """
        content_blocks = list(documents)
        content_blocks.append({"type": "text", "text": user_text})

        messages = list(additional_messages) if additional_messages else []
        messages.append({"role": "user", "content": content_blocks})

        api_params = {
            "model": self.model_name,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": self.temperature,
            "system": self.system_prompt,
            "messages": messages,
        }

        if betas:
            raise ValueError(
                "Citations do not require beta headers. Remove betas to proceed."
            )

        response = self.client.messages.create(**api_params)

        # Collect raw text blocks for convenience; callers can still inspect the full response
        text_parts = []
        for block in getattr(response, "content", []):
            if getattr(block, "type", None) == "text" and hasattr(block, "text"):
                text_parts.append(block.text)
        return "\n".join(text_parts), response
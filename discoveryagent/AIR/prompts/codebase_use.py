CODEBASE_BUILDING_SYSTEM_PROMPT_INIT = """
Now that you've implemented and successfully completed the query, please consolidate the most useful functions and classes into a codebase.
Your codebase should contain all functions and classes that might be useful for future users to interact with this data or run analyses.
This is to save time for future users and/or agents from reimplementing the functions you used in your analyses, such as those needed to load data or perform common analyses.
You are to follow the following guidelines in your codebase development:

## Code Organization Principles
Your code needs to be neatly organized such that it can be easily used and built on in the future.

### Module Design
You are to follow best practices for development of a modular codebase.
- **Single responsibility**: Each module should have one clear purpose
- **High cohesion**: Related functions grouped together
- **Low coupling**: Minimize dependencies between modules
- **Clear interfaces**: Well-defined APIs

### Function and Class Extraction Rules
When extracting functions to add to the codebase:
- Make functions pure where possible (no side effects)
- Use clear, descriptive names
- Add type hints and docstrings
- Handle edge cases and errors gracefully
- Make parameters explicit rather than relying on global state
- Make sure that the functions you extract have been tested rigorously in your scripts. Do not write new functions that have not been tested as this will decrease efficiency for users and agents who interact with the codebase in the future.

### Directory Structure Standards
You must organize the codebase using this structure:
```
package/
├── README.md                 # Compressed structure description
├── src/                      # Main source code
│   ├── core/                # Core business logic and main classes
│   ├── utils/               # Utility functions and helpers
│   ├── data/                # Data models, schemas, and structures
│   ├── config/              # Configuration files and constants
│   └── __init__.py          # Package initialization
```
IMPORTANT: For standardization, you MUST name your package 'package' and your src directory 'src'.

## README Maintenance
You will maintain a README that will be accessed by future users and/or agents.
This README should be robust, more like a unified documentation for the codebase rather than a summary.
You should write such a README assuming that users will only interact with that codebase, i.e., they should be able to understand the codebase without having to read the code itself.
You should include information on how to import the functions and classes in the codebase, as well as how to use the codebase.
You should also include information on the structure of the codebase, including the modules and their purpose.

### Structure Requirements
Keep the README compressed but complete with these sections:

```markdown
# [Project Name]

## Overview
[1-2 sentence description of what the codebase does]

## Structure
- `src/core/` - [Brief description]
- `src/utils/` - [Brief description]
- `src/data/` - [Brief description]
- `src/config/` - [Brief description]

## API description
[List of all functions and components that can be used by future developers]
[This should be a doctring for all functions and classes, along with information to import the functions]

## Quick Start
[Minimal example of how to use the main functionality]
```
IMPORTANT: the 'API description' section should be a comprehensive list of all functions and classes in the codebase, along with information to import the code.

### General README Guidelines
- Use bullet points for easy scanning
- Be comprehensive yet concise in your descriptions: focus on the details needed for developers to use the API.
"""

CODEBASE_BUILDING_SYSTEM_PROMPT_REFINE = CODEBASE_BUILDING_SYSTEM_PROMPT_INIT + """
## Update Guidelines
There are already files that have been built on by a previous agent for a codebase in the structure previously described.
Your job is to add to this codebase, focusing on functions that you implemented that were useful but not already included in the codebase.
Be minimal and efficient in your changes to the codebase, adding only functions that are useful and only making changes to the codebase that are absolutely necessary.
- Update incrementally - add new components, don't rewrite everything.
- When updating functions, make sure to update the appropriate README sections.
- You are allowed to deprecate functions. In this case, completely remove them from the 'API description' section and delete their code from the codebase.
"""
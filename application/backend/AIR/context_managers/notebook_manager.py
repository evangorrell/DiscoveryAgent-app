import nbformat as nbf
import os
import tempfile
import numpy as np
import gc
import datetime
import base64
import re
import shutil
from PIL import Image
import io
from jupyter_client import KernelManager
from jupyter_client.kernelspec import KernelSpecManager
from nbformat.v4 import new_notebook, new_code_cell, new_output
from ..utils.logger import Logger

class NotebookManager:
    """Generalized notebook management system for AI research"""
    
    def __init__(self, analysis_name, output_home=".", log_home=".", available_packages="numpy, pandas, matplotlib, seaborn, scipy", code_memory_max_size = None):
        self.analysis_name = analysis_name
        self.available_packages = available_packages
        
        # Create unique output directory based on analysis name and timestamp
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = os.path.join(output_home, "outputs", f"{analysis_name}_{timestamp}")
        
        # Initialize code memory to track the last few cells of code
        self.code_memory = []
        self.code_memory_size = code_memory_max_size  # Increased to capture more context including setup
        self.import_memory = set()  # Track imported packages

        # Create output directory if it doesn't exist
        os.makedirs(self.output_dir, exist_ok=True)

        # Initialize logger
        self.logger = Logger(self.analysis_name, log_dir=os.path.join(log_home, "logs"))
        
        # Initialize persistent kernel for efficient cell execution
        self.kernel_manager = None
        self.kernel_client = None
        self.kernel_running = False  # Track kernel state

    def update_code_memory(self, notebook_cells):
        """Update the code memory with the latest code cells from the notebook"""
        # Handle None or empty notebook_cells
        if not notebook_cells:
            self.code_memory = []
            return
            
        # Extract code cells from the notebook
        code_cells = []
        for cell in notebook_cells:
            if cell.get('cell_type') == 'code':
                code_cells.append(cell['source'])
                
        # Keep only the most recent cells up to code_memory_size
        if self.code_memory_size is not None:
            self.code_memory = code_cells[-self.code_memory_size:] if len(code_cells) > 0 else []
        else:
            self.code_memory = code_cells
        
        # Update import memory
        self._update_import_memory()
        
    def _update_import_memory(self):
        """Extract and track imported packages from code memory"""
        import_patterns = [
            r'^import\s+(\w+)',  # Start of line import
            r'^from\s+(\w+)\s+import',  # Start of line from import
            r'^import\s+(\w+)\s+as',  # Start of line import as
            r'^from\s+(\w+\.\w+)\s+import'  # Start of line from package.subpackage import
        ]
        
        self.import_memory.clear()
        for code in self.code_memory:
            # Split code into lines and check each line
            lines = code.split('\n')
            for line in lines:
                line = line.strip()
                # Skip empty lines and comments
                if not line or line.startswith('#'):
                    continue
                    
                for pattern in import_patterns:
                    match = re.match(pattern, line)
                    if match:
                        # Extract the base package name
                        base_package = match.group(1).split('.')[0]
                        # Only add if it's a valid package name (not a common word)
                        if base_package and len(base_package) > 1:
                            self.import_memory.add(base_package)
                        break  # Found a match, no need to check other patterns
        
    def get_recent_code_context(self, notebook_cells):
        """Get recent code context for model prompts with import information"""
        self.update_code_memory(notebook_cells)
        
        # Create structured context
        context_parts = []
        
        # Add import information
        if self.import_memory:
            imports_str = ", ".join(sorted(self.import_memory))
            context_parts.append(f"# Already imported packages: {imports_str}")
            context_parts.append("")
        
        # Add recent code
        if self.code_memory:
            context_parts.append("Recent code cells:")
            for i, code in enumerate(self.code_memory): # Change 1: Reversed code memory back
                context_parts.append(f"# Cell {i}:")
                context_parts.append(code)
                context_parts.append("")

        #import ipdb; ipdb.set_trace()
        
        return "\n".join(context_parts)

    def cleanup(self):
        """Clean up resources, including the persistent kernel"""
        try:
            self.stop_persistent_kernel()
        except Exception as e:
            print(f"⚠️ Warning: Error during cleanup: {str(e)}")

    def start_persistent_kernel(self):
        """Start a persistent kernel for efficient cell execution"""
        try:
            # Create kernel manager
            self.kernel_manager = KernelManager(kernel_name='python3')
            self.kernel_manager.start_kernel()
            
            # Create kernel client
            self.kernel_client = self.kernel_manager.client()
            self.kernel_client.start_channels()
            self.kernel_client.wait_for_ready()
            
            self.kernel_running = True  # Mark kernel as running
            print("✅ Persistent kernel started")
            return True
        except Exception as e:
            print(f"⚠️ Failed to start persistent kernel: {str(e)}")
            self.kernel_running = False
            return False
    
    def stop_persistent_kernel(self):
        """Stop the persistent kernel"""
        if not self.kernel_running:
            return  # Already stopped
            
        try:
            if self.kernel_client:
                self.kernel_client.stop_channels()
            if self.kernel_manager:
                self.kernel_manager.shutdown_kernel()
            print("✅ Persistent kernel stopped")
        except Exception as e:
            print(f"⚠️ Warning: Error stopping kernel: {str(e)}")
        finally:
            self.kernel_running = False
            self.kernel_client = None
            self.kernel_manager = None

    def run_last_cell(self, nb):
        """Executes the most recently added code cell and updates its outputs."""
        if not nb.cells:
            raise ValueError("No cells in notebook to run.")

        # Find the last code cell
        last_code_cell = None
        for cell in reversed(nb.cells):
            if cell.cell_type == 'code':
                last_code_cell = cell
                break
        
        if not last_code_cell:
            raise ValueError("No code cells found in notebook.")
            
        code = last_code_cell.source
        msg_id = self.kernel_client.execute(code)
        outputs = []

        while True:
            try:
                msg = self.kernel_client.get_iopub_msg(timeout=300)
            except Exception:
                break

            msg_type = msg['msg_type']
            content = msg['content']
            
            # Check for execution completion first (before parent header filter)
            if msg_type == 'status' and content.get('execution_state') == 'idle':
                break

            # Filter messages that don't belong to our execution
            if msg['parent_header'].get('msg_id') != msg_id:
                continue

            if msg_type == 'stream':
                outputs.append(new_output(output_type='stream', name=content['name'], text=content['text']))
            elif msg_type == 'execute_result':
                outputs.append(new_output(output_type='execute_result',
                                        data=content['data'],
                                        execution_count=content['execution_count']))
            elif msg_type == 'display_data':
                outputs.append(new_output(output_type='display_data',
                                        data=content['data'],
                                        metadata=content.get('metadata', {})))
            elif msg_type == 'error':
                outputs.append(new_output(output_type='error',
                                        ename=content['ename'],
                                        evalue=content['evalue'],
                                        traceback=content['traceback']))

        # Update outputs in the last code cell by finding its index
        code_cell_index = nb.cells.index(last_code_cell)
        nb.cells[code_cell_index].outputs = outputs

        # Check for errors
        for output in outputs:
            if output.output_type == "error":
                error_msg = f"{output.ename}: {output.evalue}"
                return False, error_msg, nb

        return True, None, nb

    def create_initial_notebook(self, hypothesis, setup_code=""):
        """Create initial notebook with hypothesis and setup code"""
        notebook = nbf.v4.new_notebook()
        
        # Add markdown cell with hypothesis
        notebook.cells.append(nbf.v4.new_markdown_cell(f"# Analysis\n\n**Hypothesis**: {hypothesis}"))
        
        # Add setup code if provided
        if setup_code:
            notebook.cells.append(nbf.v4.new_code_cell(setup_code))
        
        self.logger.log_action("Created initial notebook", f"Hypothesis: {hypothesis}")
        return notebook

    def cleanup_notebook_outputs(self, notebook):
        """Clean notebook outputs to ensure they are proper nbformat objects"""
        for cell in notebook.cells:
            if cell.cell_type == 'code' and hasattr(cell, 'outputs'):
                cleaned_outputs = []
                for output in cell.outputs:
                    if isinstance(output, dict):
                        # Convert dict to proper nbformat output
                        if output.get('output_type') == 'stream':
                            cleaned_outputs.append(nbf.v4.new_output('stream', 
                                                                    name=output.get('name', 'stdout'),
                                                                    text=output.get('text', '')))
                        elif output.get('output_type') == 'execute_result':
                            cleaned_outputs.append(nbf.v4.new_output('execute_result',
                                                                    data=output.get('data', {}),
                                                                    execution_count=output.get('execution_count', None)))
                        elif output.get('output_type') == 'display_data':
                            cleaned_outputs.append(nbf.v4.new_output('display_data',
                                                                    data=output.get('data', {})))
                        elif output.get('output_type') == 'error':
                            cleaned_outputs.append(nbf.v4.new_output('error',
                                                                    ename=output.get('ename', ''),
                                                                    evalue=output.get('evalue', ''),
                                                                    traceback=output.get('traceback', [])))
                    else:
                        # Already a proper object
                        cleaned_outputs.append(output)
                cell.outputs = cleaned_outputs
        
        return notebook

    def save_notebook(self, notebook, filename):
        """Save notebook to file"""
        notebook_path = os.path.join(self.output_dir, filename)
        with open(notebook_path, 'w', encoding='utf-8') as f:
            # Clean notebook outputs before writing
            clean_notebook = self.cleanup_notebook_outputs(notebook)
            nbf.write(clean_notebook, f)
        print(f"💾 Saved notebook to: {notebook_path}")
        return notebook_path

    def extract_cell_outputs(self, notebook):
        """Extract text and image outputs from the last cell"""
        if not notebook.cells:
            return "", []
        
        last_cell = notebook.cells[-1]
        no_output = "No results found"

        if last_cell.get('cell_type') != 'code':
            return no_output, []
        
        #### Extract text output ####
        text_output = ""
        if 'outputs' in last_cell:
            for output in last_cell['outputs']:
                if output.get('output_type') == 'stream': # print statements
                    text_output += output.get('text', '')
                elif output.get('output_type') == 'execute_result': # variable outputs e.g. df.head()
                    text_output += str(output.get('data', {}).get('text/plain', ''))

        #### Extract image outputs ####
        image_outputs = []
        if 'outputs' in last_cell:
            for i, output in enumerate(last_cell['outputs']):
                if output.get('output_type') == 'display_data':
                    image_data = output.get('data', {}).get('image/png')
                    if image_data:
                        image_outputs.append({
                            'data': image_data,
                            'format': 'image/png'
                        })

        return text_output, image_outputs

    def add_cell_to_notebook(self, notebook, code, description=None, cell_type='code'):
        """Add a new cell to the notebook"""
        if description:
            notebook.cells.append(nbf.v4.new_markdown_cell(f"## {description}"))
        
        if cell_type == 'code':
            notebook.cells.append(new_code_cell(code))
        elif cell_type == 'markdown':
            notebook.cells.append(nbf.v4.new_markdown_cell(code))
        
        return notebook

def strip_code_markers(text):
    """Remove code block markers from text"""
    if not isinstance(text, str):
        if isinstance(text, list):
            text = "\n".join(text)
        else:
            text = str(text)
    return re.sub(r'```python|```', '', text) 
"""
Context Managers Package

This package contains the context management components for the AIR framework:
- NotebookManager: Standard notebook management
- NotebookManagerXML: XML-based notebook management
- ReportManager: Academic report management
"""

from .notebook_manager import NotebookManager, strip_code_markers
from .notebook_manager_xml import NotebookManagerXML
from .report_manager import ReportManager

__all__ = [
    "NotebookManager",
    "NotebookManagerXML", 
    "ReportManager",
    "strip_code_markers"
] 
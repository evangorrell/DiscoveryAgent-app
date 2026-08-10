import os
import datetime
import json
import base64
import re
from PIL import Image
import io
import xml.etree.ElementTree as ET
from xml.dom import minidom
from ..utils.logger import Logger

class ReportManager:
    """
    Academic report management system for AI research that handles iterative reports
    with text, numeric results, and figures in a format optimized for LLM understanding.
    """
    
    def __init__(self, report_name, output_home=".", log_home="."):
        self.report_name = report_name
        
        # Create unique output directory based on report name and timestamp
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = os.path.join(output_home, "reports", f"{report_name}_{timestamp}")
        
        # Create output directory if it doesn't exist
        os.makedirs(self.output_dir, exist_ok=True)
        
        # Initialize logger
        self.logger = Logger(report_name, log_dir=os.path.join(log_home, "logs"))
        
        # Track report sections and content
        self.sections = {
            'abstract': [],
            'introduction': [],
            'methods': [],
            'results': [],
            'discussion': [],
            'conclusion': [],
            'figures': [],
            'tables': [],
            'appendices': []
        }
        
        # Track metadata
        self.metadata = {
            'created': datetime.datetime.now().isoformat(),
            'last_updated': datetime.datetime.now().isoformat(),
            'version': 1,
            'total_iterations': 0
        }
        
        # Initialize the report file
        self.report_file = os.path.join(self.output_dir, f"{report_name}_report.md")
        self.xml_file = os.path.join(self.output_dir, f"{report_name}_report.xml")
        
        self.logger.log_action("ReportManager initialized", f"Report: {report_name}, Output dir: {self.output_dir}")

    def add_text_section(self, section_name, content, iteration=None):
        """
        Add or update text content to a specific section.
        
        Args:
            section_name (str): Section name (abstract, introduction, methods, etc.)
            content (str): Text content to add
            iteration (int, optional): Iteration number for tracking
        """
        if section_name not in self.sections:
            raise ValueError(f"Invalid section name: {section_name}")
        
        # Create section entry
        section_entry = {
            'content': content,
            'timestamp': datetime.datetime.now().isoformat(),
            'iteration': iteration or self.metadata['total_iterations'] + 1,
            'type': 'text'
        }
        
        self.sections[section_name].append(section_entry)
        self.metadata['last_updated'] = datetime.datetime.now().isoformat()
        self.metadata['total_iterations'] += 1
        
        self.logger.log_action(f"Added text to {section_name}", f"Iteration: {section_entry['iteration']}")
        
        # Update files
        self._save_report()
        self._save_xml_report()

    def add_numeric_result(self, section_name, result_name, value, unit=None, description=None, iteration=None):
        """
        Add numeric results to a section.
        
        Args:
            section_name (str): Section name (typically 'results')
            result_name (str): Name/identifier for the result
            value (float/int): Numeric value
            unit (str, optional): Unit of measurement
            description (str, optional): Description of the result
            iteration (int, optional): Iteration number
        """
        if section_name not in self.sections:
            raise ValueError(f"Invalid section name: {section_name}")
        
        # Create numeric result entry
        result_entry = {
            'name': result_name,
            'value': value,
            'unit': unit,
            'description': description,
            'timestamp': datetime.datetime.now().isoformat(),
            'iteration': iteration or self.metadata['total_iterations'] + 1,
            'type': 'numeric'
        }
        
        self.sections[section_name].append(result_entry)
        self.metadata['last_updated'] = datetime.datetime.now().isoformat()
        self.metadata['total_iterations'] += 1
        
        self.logger.log_action(f"Added numeric result to {section_name}", f"Result: {result_name} = {value} {unit or ''}")
        
        # Update files
        self._save_report()
        self._save_xml_report()

    def add_figure(self, section_name, figure_path, caption, description=None, iteration=None):
        """
        Add a figure to a section.
        
        Args:
            section_name (str): Section name (typically 'results' or 'figures')
            figure_path (str): Path to the figure file
            caption (str): Figure caption
            description (str, optional): Additional description
            iteration (int, optional): Iteration number
        """
        if section_name not in self.sections:
            raise ValueError(f"Invalid section name: {section_name}")
        
        # Copy figure to report directory
        figure_filename = os.path.basename(figure_path)
        figure_dest = os.path.join(self.output_dir, f"figures", figure_filename)
        os.makedirs(os.path.dirname(figure_dest), exist_ok=True)
        
        # Copy the file
        import shutil
        shutil.copy2(figure_path, figure_dest)
        
        # Create figure entry
        figure_entry = {
            'path': figure_dest,
            'filename': figure_filename,
            'caption': caption,
            'description': description,
            'timestamp': datetime.datetime.now().isoformat(),
            'iteration': iteration or self.metadata['total_iterations'] + 1,
            'type': 'figure'
        }
        
        self.sections[section_name].append(figure_entry)
        self.metadata['last_updated'] = datetime.datetime.now().isoformat()
        self.metadata['total_iterations'] += 1
        
        self.logger.log_action(f"Added figure to {section_name}", f"Figure: {figure_filename}, Caption: {caption}")
        
        # Update files
        self._save_report()
        self._save_xml_report()

    def add_table(self, section_name, table_data, caption, headers=None, iteration=None):
        """
        Add a table to a section.
        
        Args:
            section_name (str): Section name (typically 'results' or 'tables')
            table_data (list): List of rows (each row is a list of values)
            caption (str): Table caption
            headers (list, optional): Column headers
            iteration (int, optional): Iteration number
        """
        if section_name not in self.sections:
            raise ValueError(f"Invalid section name: {section_name}")
        
        # Create table entry
        table_entry = {
            'data': table_data,
            'headers': headers,
            'caption': caption,
            'timestamp': datetime.datetime.now().isoformat(),
            'iteration': iteration or self.metadata['total_iterations'] + 1,
            'type': 'table'
        }
        
        self.sections[section_name].append(table_entry)
        self.metadata['last_updated'] = datetime.datetime.now().isoformat()
        self.metadata['total_iterations'] += 1
        
        self.logger.log_action(f"Added table to {section_name}", f"Caption: {caption}, Rows: {len(table_data)}")
        
        # Update files
        self._save_report()
        self._save_xml_report()

    def get_report_context(self, section_filter=None, max_iterations=None):
        """
        Get the current report context in XML format for LLM consumption.
        
        Args:
            section_filter (list, optional): List of sections to include
            max_iterations (int, optional): Maximum number of iterations to include
            
        Returns:
            str: XML representation of the report
        """
        root = ET.Element('report')
        root.set('name', self.report_name)
        root.set('version', str(self.metadata['version']))
        root.set('total_iterations', str(self.metadata['total_iterations']))
        root.set('last_updated', self.metadata['last_updated'])
        
        # Add metadata
        metadata_elem = ET.SubElement(root, 'metadata')
        for key, value in self.metadata.items():
            meta_elem = ET.SubElement(metadata_elem, key)
            meta_elem.text = str(value)
        
        # Add sections
        sections_elem = ET.SubElement(root, 'sections')
        
        sections_to_process = section_filter if section_filter else self.sections.keys()
        
        for section_name in sections_to_process:
            if section_name not in self.sections:
                continue
                
            section_elem = ET.SubElement(sections_elem, 'section')
            section_elem.set('name', section_name)
            
            # Filter by iterations if specified
            entries = self.sections[section_name]
            if max_iterations:
                entries = [entry for entry in entries if entry.get('iteration', 0) <= max_iterations]
            
            for entry in entries:
                entry_elem = ET.SubElement(section_elem, 'entry')
                entry_elem.set('type', entry.get('type', 'text'))
                entry_elem.set('iteration', str(entry.get('iteration', 0)))
                entry_elem.set('timestamp', entry.get('timestamp', ''))
                
                if entry.get('type') == 'text':
                    content_elem = ET.SubElement(entry_elem, 'content')
                    content_elem.text = self._sanitize_text_for_xml(entry.get('content', ''))
                    
                elif entry.get('type') == 'numeric':
                    name_elem = ET.SubElement(entry_elem, 'name')
                    name_elem.text = self._sanitize_text_for_xml(entry.get('name', ''))
                    
                    value_elem = ET.SubElement(entry_elem, 'value')
                    value_elem.text = str(entry.get('value', ''))
                    
                    if entry.get('unit'):
                        unit_elem = ET.SubElement(entry_elem, 'unit')
                        unit_elem.text = self._sanitize_text_for_xml(entry.get('unit', ''))
                    
                    if entry.get('description'):
                        desc_elem = ET.SubElement(entry_elem, 'description')
                        desc_elem.text = self._sanitize_text_for_xml(entry.get('description', ''))
                        
                elif entry.get('type') == 'figure':
                    caption_elem = ET.SubElement(entry_elem, 'caption')
                    caption_elem.text = self._sanitize_text_for_xml(entry.get('caption', ''))
                    
                    filename_elem = ET.SubElement(entry_elem, 'filename')
                    filename_elem.text = entry.get('filename', '')
                    
                    if entry.get('description'):
                        desc_elem = ET.SubElement(entry_elem, 'description')
                        desc_elem.text = self._sanitize_text_for_xml(entry.get('description', ''))
                        
                elif entry.get('type') == 'table':
                    caption_elem = ET.SubElement(entry_elem, 'caption')
                    caption_elem.text = self._sanitize_text_for_xml(entry.get('caption', ''))
                    
                    if entry.get('headers'):
                        headers_elem = ET.SubElement(entry_elem, 'headers')
                        for header in entry.get('headers', []):
                            header_elem = ET.SubElement(headers_elem, 'header')
                            header_elem.text = self._sanitize_text_for_xml(str(header))
                    
                    data_elem = ET.SubElement(entry_elem, 'data')
                    for row in entry.get('data', []):
                        row_elem = ET.SubElement(data_elem, 'row')
                        for cell in row:
                            cell_elem = ET.SubElement(row_elem, 'cell')
                            cell_elem.text = self._sanitize_text_for_xml(str(cell))
        
        return self._xml_to_string(root)

    def _sanitize_text_for_xml(self, text):
        """Sanitize text to be safe for XML"""
        if not text:
            return ""
        
        # Convert to string if needed
        text = str(text)
        
        # Replace problematic characters that could break XML
        text = text.replace('&', '&amp;')
        text = text.replace('<', '&lt;')
        text = text.replace('>', '&gt;')
        
        # Remove or replace other problematic characters
        text = text.replace('\x00', '')  # Remove null bytes
        text = text.replace('\x01', '')  # Remove control characters
        text = text.replace('\x02', '')
        text = text.replace('\x03', '')
        text = text.replace('\x04', '')
        text = text.replace('\x05', '')
        text = text.replace('\x06', '')
        text = text.replace('\x07', '')
        text = text.replace('\x08', '')
        text = text.replace('\x0b', '')
        text = text.replace('\x0c', '')
        text = text.replace('\x0e', '')
        text = text.replace('\x0f', '')
        
        return text

    def _xml_to_string(self, xml_element):
        """Convert XML element to formatted string with error handling"""
        try:
            rough_string = ET.tostring(xml_element, encoding='unicode')
            reparsed = minidom.parseString(rough_string)
            return reparsed.toprettyxml(indent="  ")
        except Exception as e:
            print(f"⚠️ XML formatting failed, using fallback: {str(e)}")
            # Fallback: return the raw XML string without pretty formatting
            return ET.tostring(xml_element, encoding='unicode')

    def _save_report(self):
        """Save the report in Markdown format"""
        with open(self.report_file, 'w', encoding='utf-8') as f:
            f.write(f"# {self.report_name}\n\n")
            f.write(f"**Generated:** {self.metadata['created']}\n")
            f.write(f"**Last Updated:** {self.metadata['last_updated']}\n")
            f.write(f"**Total Iterations:** {self.metadata['total_iterations']}\n\n")
            
            # Write sections
            for section_name, entries in self.sections.items():
                if not entries:
                    continue
                    
                f.write(f"## {section_name.title()}\n\n")
                
                for entry in entries:
                    if entry.get('type') == 'text':
                        f.write(f"{entry.get('content', '')}\n\n")
                        
                    elif entry.get('type') == 'numeric':
                        f.write(f"**{entry.get('name', '')}:** {entry.get('value', '')}")
                        if entry.get('unit'):
                            f.write(f" {entry.get('unit', '')}")
                        f.write("\n")
                        if entry.get('description'):
                            f.write(f"*{entry.get('description', '')}*\n")
                        f.write("\n")
                        
                    elif entry.get('type') == 'figure':
                        f.write(f"![{entry.get('caption', '')}](figures/{entry.get('filename', '')})\n")
                        f.write(f"*Figure: {entry.get('caption', '')}*\n")
                        if entry.get('description'):
                            f.write(f"*{entry.get('description', '')}*\n")
                        f.write("\n")
                        
                    elif entry.get('type') == 'table':
                        f.write(f"**Table: {entry.get('caption', '')}**\n\n")
                        
                        if entry.get('headers'):
                            f.write("| " + " | ".join(str(h) for h in entry.get('headers', [])) + " |\n")
                            f.write("|" + "|".join("---" for _ in entry.get('headers', [])) + "|\n")
                        
                        for row in entry.get('data', []):
                            f.write("| " + " | ".join(str(cell) for cell in row) + " |\n")
                        f.write("\n")

    def _save_xml_report(self):
        """Save the report in XML format"""
        xml_content = self.get_report_context()
        with open(self.xml_file, 'w', encoding='utf-8') as f:
            f.write(xml_content)

    def get_latest_content(self, section_name):
        """Get the latest content for a specific section"""
        if section_name not in self.sections or not self.sections[section_name]:
            return None
        
        # Return the most recent entry
        return self.sections[section_name][-1]

    def get_section_summary(self, section_name):
        """Get a summary of all content in a section"""
        if section_name not in self.sections:
            return None
        
        entries = self.sections[section_name]
        if not entries:
            return None
        
        summary = {
            'section': section_name,
            'total_entries': len(entries),
            'latest_iteration': max(entry.get('iteration', 0) for entry in entries),
            'content_types': list(set(entry.get('type', 'text') for entry in entries))
        }
        
        return summary

    def cleanup(self):
        """Clean up resources"""
        try:
            # Save final versions
            self._save_report()
            self._save_xml_report()
            print("✅ Report saved successfully")
        except Exception as e:
            print(f"⚠️ Warning: Error during cleanup: {str(e)}") 
"""
PDF Loader Utility

This module provides utilities for loading PDF files and preparing them for
Anthropic's native PDF support. PDFs are base64-encoded and returned in a format
compatible with Claude's document content blocks.
"""

import os
import base64
from pathlib import Path
from typing import List, Dict, Optional


def encode_pdf_to_base64(pdf_path: str) -> str:
    """
    Encode a PDF file to base64 string.

    Args:
        pdf_path (str): Path to the PDF file

    Returns:
        str: Base64-encoded PDF data

    Raises:
        FileNotFoundError: If the PDF file doesn't exist
        Exception: If there's an error reading the PDF
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    try:
        with open(pdf_path, 'rb') as f:
            pdf_data = base64.standard_b64encode(f.read()).decode('utf-8')
        return pdf_data
    except Exception as e:
        raise Exception(f"Error encoding PDF {pdf_path}: {str(e)}")


def load_pdfs_from_directory(directory_path: str, max_pdfs: Optional[int] = None) -> List[Dict[str, str]]:
    """
    Load all PDF files from a directory and prepare them for Anthropic's API.

    Args:
        directory_path (str): Path to the directory containing PDFs
        max_pdfs (Optional[int]): Maximum number of PDFs to load (None for all)

    Returns:
        List[Dict[str, str]]: List of dictionaries containing:
            - 'filename': Name of the PDF file
            - 'base64_data': Base64-encoded PDF content
            - 'size_bytes': Size of the PDF file in bytes

    Raises:
        FileNotFoundError: If the directory doesn't exist
    """
    if not os.path.exists(directory_path):
        raise FileNotFoundError(f"Directory not found: {directory_path}")

    if not os.path.isdir(directory_path):
        raise ValueError(f"Path is not a directory: {directory_path}")

    pdf_files = sorted([f for f in os.listdir(directory_path) if f.endswith('.pdf')])

    if max_pdfs is not None:
        pdf_files = pdf_files[:max_pdfs]

    pdf_documents = []
    for pdf_file in pdf_files:
        pdf_path = os.path.join(directory_path, pdf_file)
        try:
            # Get file size
            file_size = os.path.getsize(pdf_path)

            # Check size limit (32MB as per Anthropic docs)
            max_size = 32 * 1024 * 1024  # 32MB in bytes
            if file_size > max_size:
                print(f"Warning: {pdf_file} exceeds 32MB limit ({file_size / (1024*1024):.1f}MB), skipping...")
                continue

            # Encode the PDF
            base64_data = encode_pdf_to_base64(pdf_path)

            pdf_documents.append({
                'filename': pdf_file,
                'base64_data': base64_data,
                'size_bytes': file_size
            })

        except Exception as e:
            print(f"Warning: Failed to load {pdf_file}: {str(e)}")
            # Continue loading other PDFs even if one fails
            continue

    return pdf_documents


def create_pdf_content_blocks(pdf_documents: List[Dict[str, str]]) -> List[Dict]:
    """
    Create Anthropic API-compatible content blocks for PDF documents.

    Args:
        pdf_documents: List of PDF document dictionaries from load_pdfs_from_directory

    Returns:
        List[Dict]: List of content blocks in Anthropic's format:
            [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": "<base64_data>"
                    },
                    "cache_control": {"type": "ephemeral"}  # Enable prompt caching
                },
                {
                    "type": "text",
                    "text": "Background reading: <filename>"
                }
            ]
    """
    content_blocks = []

    for pdf_doc in pdf_documents:
        # Add document block
        content_blocks.append({
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": pdf_doc['base64_data']
            },
            "cache_control": {"type": "ephemeral"}  # Enable prompt caching for PDFs
        })

        # Add text description
        size_mb = pdf_doc['size_bytes'] / (1024 * 1024)
        content_blocks.append({
            "type": "text",
            "text": f"Background reading document: {pdf_doc['filename']} ({size_mb:.1f}MB)"
        })

    return content_blocks

"""Configuration file for the Sphinx documentation builder."""

# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

import os
import sys

sys.path.insert(
    0, os.path.abspath("../../python-client/src")
)  # adjust as needed
sys.path.insert(0, os.path.abspath("../../server/src"))  # adjust as needed
sys.path.insert(
    0, os.path.abspath("../../protocol/python/src")
)  # adjust as needed

project = "gabriel"
copyright = "2025, Aditya Chanana"
author = "Aditya Chanana"

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "sphinx.ext.autodoc",  # Pulls docstrings from your code
    "sphinx.ext.napoleon",  # Supports Google & NumPy-style docstrings
    "sphinx.ext.viewcode",  # Adds "View Source" links
]

templates_path = ["_templates"]
exclude_patterns = []


# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "alabaster"
html_static_path = ["_static"]

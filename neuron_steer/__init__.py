"""Standalone local neuron steering for Hugging Face Transformers models.

The package is intentionally importable without torch or transformers. The
heavy runtime dependencies are imported only when a model is loaded.
"""

__version__ = "0.3.0"

__all__ = ["__version__"]

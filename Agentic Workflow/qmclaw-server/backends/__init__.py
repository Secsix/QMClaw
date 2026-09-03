"""
backends/__init__.py - Backend adapter layer for quantum measurement systems.

This module provides a unified interface for different quantum measurement
backends (LQCS, OpenSystemQ, Qiskit, etc.), enabling easy switching between
systems while maintaining a consistent API.

Usage:
    from backends import get_backend, create_backend

    # Create the current backend (from system.json)
    backend = create_backend()

    # Or get a specific backend
    backend = get_backend('lqcs')

    # Use the backend
    qubits = backend.get_qubits()
    experiments = backend.list_experiments()
"""

import json
import os
from pathlib import Path
from typing import Optional, Dict, Any, List

from .base import BackendInterface, BackendMetadata
from .backend_types import BackendStatus, QubitInfo, SessionConfig, ExperimentInfo
from .registry import (
    BackendRegistry,
    BackendFactory,
    get_factory,
    get_backend,
    create_backend as _create_backend,
    create_backend as create_backend,
)

# Initialize registry and discover backends
_registry = BackendRegistry.get_instance()

# Discover backends in this directory
_backends_dir = Path(__file__).parent
_registry.discover_backends(_backends_dir)


def load_system_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load system configuration from JSON file.

    Args:
        config_path: Path to system.json. If None, uses default location.

    Returns:
        Configuration dictionary
    """
    if config_path is None:
        config_path = _backends_dir.parent / "config" / "system.json"

    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"system": "lqcs"}


def init_backend(
    config_path: Optional[Path] = None,
    session_path: Optional[List[str]] = None,
) -> BackendInterface:
    """Initialize the configured backend.

    This is the main entry point for initializing the backend system.
    It loads the configuration from system.json and creates the
    appropriate backend instance.

    Args:
        config_path: Path to system.json configuration file
        session_path: Optional initial session path

    Returns:
        Initialized backend instance
    """
    # Load configuration
    config = load_system_config(config_path)

    # Get system name from config
    system_name = config.get("system", "lqcs")

    # Set current backend in registry
    if system_name not in _registry.list_backends():
        print(
            f"WARNING: Backend '{system_name}' not found, falling back to 'lqcs'",
            file=sys.stderr,
        )
        system_name = "lqcs"

    _registry.set_current(system_name)

    # Create backend
    backend = _create_backend(system_name, config)

    # Initialize with session path
    backend.initialize(session_path)

    return backend


def switch_system(
    system_name: str,
    config_path: Optional[Path] = None,
) -> BackendInterface:
    """Switch to a different measurement system.

    Args:
        system_name: Name of the backend system (e.g., 'lqcs', 'qiskit')
        config_path: Path to system.json

    Returns:
        New backend instance for the specified system
    """
    # Load config and set current system
    config = load_system_config(config_path)
    config["system"] = system_name

    # Save new config
    if config_path is None:
        config_path = _backends_dir.parent / "config" / "system.json"
    _registry.save_config(config_path, system_name)

    # Shutdown current backend
    current = _registry.current
    if current and current.instance:
        current.instance.shutdown()

    # Set and create new backend
    _registry.set_current(system_name)
    return _create_backend(system_name, config)


# Re-export for convenience
__all__ = [
    # Core interfaces
    "BackendInterface",
    "BackendMetadata",
    "BackendStatus",
    "SessionConfig",
    "QubitInfo",
    "ExperimentInfo",
    # Registry functions
    "BackendRegistry",
    "BackendFactory",
    "get_factory",
    "get_backend",
    "create_backend",
    # Convenience functions
    "load_system_config",
    "init_backend",
    "switch_system",
]

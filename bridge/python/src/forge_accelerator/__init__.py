"""Typed Forge accelerator bridge client."""

from .client import AcceleratorClient, BridgeError, ProtocolError
from .models import (
    BufferAccess,
    Capabilities,
    ComputeUnits,
    InlineTensor,
    Job,
    JobEventPage,
    MetalBuffer,
    ScratchReference,
    ScratchTensor,
)

__all__ = [
    "AcceleratorClient",
    "BridgeError",
    "BufferAccess",
    "Capabilities",
    "ComputeUnits",
    "InlineTensor",
    "Job",
    "JobEventPage",
    "MetalBuffer",
    "ProtocolError",
    "ScratchReference",
    "ScratchTensor",
]

__version__ = "1.0.0"

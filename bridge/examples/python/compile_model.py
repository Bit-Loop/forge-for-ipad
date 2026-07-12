"""Compile a model already copied beneath the shared Forge scratch root."""

import os
from pathlib import Path

from forge_accelerator import AcceleratorClient, ComputeUnits, ScratchReference

root = Path("/run/forge/accelerator-scratch")
model = ScratchReference.from_file(root / "models/example.mlmodel", root)
client = AcceleratorClient(os.environ["FORGE_ACCEL_TOKEN"])
verified = client.verify_scratch(model)
job = client.compile_coreml(verified, "mlmodel", ComputeUnits.ALL)
completed = client.wait(job.id, timeout=300)
print(completed.result)

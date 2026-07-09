import os
import sys
import types
import importlib.util
import torch
from PIL import Image
from typing import Dict, Any

# Suppress Hugging Face hub warnings
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"

# Create mock modules for dependencies that are not available on macOS ARM64
# before importing transformers or locateanything_worker

# Mock decord
decord_mock = types.ModuleType('decord')
decord_mock.__spec__ = importlib.util.spec_from_loader('decord', loader=None)
decord_mock.VideoReader = type('VideoReader', (), {})
decord_mock.from_numpy = lambda x: None
sys.modules['decord'] = decord_mock

# Mock cv2
cv2_mock = types.ModuleType('cv2')
cv2_mock.__spec__ = importlib.util.spec_from_loader('cv2', loader=None)
cv2_mock.IMREAD_COLOR = 1
cv2_mock.imdecode = lambda x, y: None
cv2_mock.cvtColor = lambda x, y: x
sys.modules['cv2'] = cv2_mock

# Mock lmdb
lmdb_mock = types.ModuleType('lmdb')
lmdb_mock.__spec__ = importlib.util.spec_from_loader('lmdb', loader=None)
lmdb_mock.open = lambda *args, **kwargs: None
sys.modules['lmdb'] = lmdb_mock

# Mock peft
peft_mock = types.ModuleType('peft')
peft_mock.__spec__ = importlib.util.spec_from_loader('peft', loader=None)
peft_mock.LoraConfig = type('LoraConfig', (), {})
peft_mock.get_peft_model = lambda *args, **kwargs: None
sys.modules['peft'] = peft_mock

from subagents.locate.locateanything_worker import LocateAnythingWorker

MODEL_ID = "nvidia/LocateAnything-3B"

_worker = None


def get_worker():
    global _worker
    if _worker is None:
        # Determine device: use cpu for now due to attention implementation issues on MPS
        device = "cpu"
        
        _worker = LocateAnythingWorker(
            model_path=MODEL_ID,
            device=device,
            dtype=torch.bfloat16 if device != "cpu" else torch.float32,
        )
        
        # Set _attn_implementation to sdpa on the model and all submodules
        for name, module in _worker.model.named_modules():
            if hasattr(module, '_attn_implementation'):
                module._attn_implementation = 'sdpa'
            elif hasattr(module, 'config') and hasattr(module.config, 'attn_implementation'):
                module.config.attn_implementation = 'sdpa'

        # Also set on the language model
        if hasattr(_worker.model, 'language_model'):
            for name, module in _worker.model.language_model.named_modules():
                if hasattr(module, '_attn_implementation'):
                    module._attn_implementation = 'sdpa'
                elif hasattr(module, 'config') and hasattr(module.config, 'attn_implementation'):
                    module.config.attn_implementation = 'sdpa'

    return _worker


def locate_anything_infer(image_path: str, question: str) -> str:
    try:
        worker = get_worker()
        image = Image.open(image_path).convert("RGB")

        # Use the ground_multi method for general visual questions with generation_mode="slow"
        result = worker.ground_multi(image, question, generation_mode="slow")
        return result.get("answer", "No answer returned from model.")
    except Exception as e:
        return f"Error during locate_anything inference: {str(e)}"

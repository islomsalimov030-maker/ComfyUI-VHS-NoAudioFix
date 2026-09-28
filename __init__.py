"""
ComfyUI-VHS-NoAudioFix

Fixes a hang when Load Video (VideoHelperSuite) is given a video WITHOUT an
audio track.

Why it hangs: VHS returns its `audio` output as a lazy map that runs ffmpeg the
first time anything iterates it. ComfyUI's RAM-pressure cache iterates every
cached output to measure its size, ffmpeg fails with "Output file does not
contain any stream", the exception kills the prompt_worker thread, and the UI
freezes on whatever node was running (progress stays put, GPU at 0%).

What this does: at startup it wraps VHS's get_audio in memory so a missing
audio track returns a short silence instead of raising. All other ffmpeg
errors are raised as before. No VHS files are modified, so it survives VHS
updates.

Node (optional): "VHS No-Audio Fix Status" -> STRING, wire it into Preview Any
to check from the UI that the fix is active.
"""

import logging
import sys
import threading
import time

import torch

log = logging.getLogger("VHSNoAudioFix")

_MARKERS = ("does not contain any stream", "matches no streams")
_state = {"patched": [], "calls": 0}


def _silence():
    return {"waveform": torch.zeros((1, 2, 1), dtype=torch.float32), "sample_rate": 44100}


def _patch_module(name, mod):
    orig = getattr(mod, "get_audio", None)
    if orig is None or getattr(orig, "_noaudio_patched", False):
        return False

    def get_audio(*args, **kwargs):
        try:
            return orig(*args, **kwargs)
        except Exception as e:
            if any(m in str(e) for m in _MARKERS):
                _state["calls"] += 1
                log.info("VHSNoAudioFix: video has no audio track, returning silence")
                return _silence()
            raise

    get_audio._noaudio_patched = True
    mod.get_audio = get_audio
    _state["patched"].append(name)
    print(f"VHSNoAudioFix: applied to {name}")
    return True


def _find_and_patch():
    n = 0
    for name, mod in list(sys.modules.items()):
        if mod is not None and name.endswith("videohelpersuite.utils"):
            if _patch_module(name, mod):
                n += 1
    return n


def _wait_for_vhs():
    # VHS may be imported after this package; wait for it (up to 5 minutes).
    for _ in range(300):
        if _find_and_patch() or _state["patched"]:
            return
        time.sleep(1)
    log.warning("VHSNoAudioFix: VideoHelperSuite not found, fix not applied")


if not _find_and_patch():
    threading.Thread(target=_wait_for_vhs, daemon=True).start()


class VHSNoAudioFixStatus:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("status",)
    FUNCTION = "run"
    CATEGORY = "utils"
    OUTPUT_NODE = True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")

    def run(self):
        _find_and_patch()
        if _state["patched"]:
            msg = (f"VHS no-audio fix ACTIVE on {', '.join(_state['patched'])}. "
                   f"Silent audio returned {_state['calls']} time(s) since start.")
        else:
            msg = "VHS no-audio fix NOT active: VideoHelperSuite module not found."
        return {"ui": {"text": [msg]}, "result": (msg,)}


NODE_CLASS_MAPPINGS = {"VHSNoAudioFixStatus": VHSNoAudioFixStatus}
NODE_DISPLAY_NAME_MAPPINGS = {"VHSNoAudioFixStatus": "VHS No-Audio Fix Status"}

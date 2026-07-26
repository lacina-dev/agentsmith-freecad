#!/usr/bin/env python3
"""agentsmith_voice.py -- dictate a prompt instead of typing it.

Speech never leaves the machine. Prompts describe what somebody is building --
their parts, their dimensions, sometimes their client's product -- and quietly
shipping microphone audio to a third party is not a default a CAD addon should
choose for its users. So the transcription runs locally through whisper.cpp, and
a cloud service is not wired in at all.

Two consequences of that choice, both handled here rather than hidden:

* **Nothing is bundled.** whisper.cpp and its model are the user's to install,
  and this module's job when they are missing is to say exactly what to do --
  never to fail with a traceback, and never to download half a gigabyte on its
  own initiative.
* **Czech decides the model size.** The small models are unusable for it; the
  practical floor is `medium` or `large-v3-turbo`, and the quantised builds are
  the ones worth recommending because they are a third of the size for
  essentially the same result.

Recording goes through an external tool because FreeCAD's bundled PySide6 ships
without QtMultimedia (verified on 1.1.1: the module raises ImportError). That is
also the portable choice -- every desktop has at least one of these.

Stdlib only.
"""

import array
import os
import re
import shutil
import subprocess
import sys
import wave

SAMPLE_RATE = 16000            # what whisper.cpp expects; resampling it is wasteful
CHANNELS = 1

MODEL_DIR = os.path.expanduser("~/.local/share/agentsmith/whisper")
MODEL_BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/"

#: Worth offering, with honest sizes. Ordered best-for-Czech first: the tiny and
#: base models are omitted on purpose -- they transcribe Czech badly enough that
#: offering them would mostly generate complaints about the feature.
MODELS = (
    ("ggml-large-v3-turbo-q5_0.bin", "large-v3-turbo (kvantovaný)", 547,
     "nejlepší čeština, doporučeno"),
    ("ggml-medium-q5_0.bin", "medium (kvantovaný)", 514, "solidní čeština, menší"),
    ("ggml-large-v3-turbo.bin", "large-v3-turbo (plný)", 1624,
     "o málo lepší než kvantovaný, třikrát větší"),
    ("ggml-small-q5_1.bin", "small (kvantovaný)", 181,
     "čeština slabá — jen když je málo místa"),
)

#: Voice-activity detection model. Small, and the single most effective guard
#: against the failure below -- measured: with it, silence transcribes to nothing;
#: without it, to a confident sentence.
VAD_MODEL = "ggml-silero-v5.1.2.bin"
VAD_MODEL_URL = "https://huggingface.co/ggml-org/whisper-vad/resolve/main/" + VAD_MODEL

#: whisper.cpp renamed its binary over time; all of these are the same program.
WHISPER_BINARIES = ("whisper-cli", "whisper-cpp", "whisper.cpp", "main")

#: Recorders in preference order. PipeWire and ALSA are what Linux desktops have;
#: ffmpeg covers macOS and Windows, where the input device is named differently.
RECORDERS = ("pw-record", "arecord", "ffmpeg", "sox", "rec")


class VoiceUnavailable(Exception):
    """Raised with a message meant to be shown to a person, not logged."""


# --------------------------------------------------------------------------- #
# Finding the pieces
# --------------------------------------------------------------------------- #
def find_binary(names, env_var=None, extra_dirs=()):
    if env_var and os.environ.get(env_var):
        candidate = os.path.expanduser(os.environ[env_var])
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    for directory in extra_dirs:
        for name in names:
            candidate = os.path.join(os.path.expanduser(directory), name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def whisper_binary():
    return find_binary(WHISPER_BINARIES, "AGENTSMITH_WHISPER_BIN",
                       ("~/.local/bin", "~/bin", "/usr/local/bin",
                        "~/.local/share/agentsmith/whisper.cpp/build/bin"))


def recorder_binary():
    return find_binary(RECORDERS, "AGENTSMITH_RECORDER")


def whisper_model(model_dir=None):
    """The model to use: an explicit choice, else the best one installed."""
    explicit = os.environ.get("AGENTSMITH_WHISPER_MODEL")
    if explicit and os.path.isfile(os.path.expanduser(explicit)):
        return os.path.expanduser(explicit)
    directory = model_dir or MODEL_DIR
    ranked = [name for name, _label, _mb, _note in MODELS]
    try:
        present = set(os.listdir(directory))
    except OSError:
        return None
    for name in ranked:
        if name in present:
            return os.path.join(directory, name)
    # An unrecognised .bin the user put there themselves still beats nothing.
    for name in sorted(present):
        if name.endswith(".bin"):
            return os.path.join(directory, name)
    return None


def vad_model(model_dir=None):
    path = os.path.join(model_dir or MODEL_DIR, VAD_MODEL)
    return path if os.path.isfile(path) else None


def input_muted():
    """Is the default recording input muted? True/False, or None if unknown.

    Worth asking BEFORE recording rather than diagnosing afterwards: speaking a
    whole prompt into a muted microphone and being told "I heard nothing" a
    minute later is a bad trade for a question that takes milliseconds.
    """
    probes = (
        (["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"], "MUTED"),
        (["pactl", "get-source-mute", "@DEFAULT_SOURCE@"], "yes"),
        (["amixer", "get", "Capture"], "[off]"),
    )
    for command, marker in probes:
        tool = shutil.which(command[0])
        if not tool:
            continue
        try:
            result = subprocess.run([tool] + command[1:], stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode != 0:
            continue
        return marker in result.stdout.decode("utf-8", "replace")
    return None            # no way to ask; do not guess either way


def availability(model_dir=None):
    """What is present, what is missing, and what to do about it.

    Returns a dict rather than a bool because "no microphone tool", "no
    whisper.cpp" and "no model" are three different problems with three
    different fixes, and collapsing them into "voice unavailable" leaves the
    user guessing.
    """
    recorder = recorder_binary()
    binary = whisper_binary()
    model = whisper_model(model_dir)
    problems = []
    if not recorder:
        problems.append(
            "Chybí nástroj pro nahrávání. Nainstaluj jeden z: %s "
            "(na Ubuntu stačí `sudo apt install alsa-utils`)." % ", ".join(RECORDERS))
    if not binary:
        problems.append(
            "Chybí whisper.cpp. Nainstaluj `whisper-cli` (např. "
            "`sudo apt install whisper.cpp` nebo build z github.com/ggerganov/whisper.cpp) "
            "a případně nastav AGENTSMITH_WHISPER_BIN.")
    if not model and binary:
        problems.append(
            "Chybí model pro rozpoznávání. Pro češtinu je potřeba aspoň medium — "
            "stáhni ho tlačítkem, uloží se do %s." % (model_dir or MODEL_DIR))
    muted = input_muted()
    return {
        "recorder": recorder,
        "binary": binary,
        "model": model,
        "vad": vad_model(model_dir),
        "muted": muted,
        "ready": bool(recorder and binary and model),
        "problems": problems,
    }


def model_url(name):
    return MODEL_BASE_URL + name


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #
def record_args(tool, path, rate=SAMPLE_RATE, channels=CHANNELS, device=None):
    """Command line that records mono 16 kHz WAV until it is killed.

    Each recorder is stopped by terminating it -- none of them has a "record
    until told to stop" flag that works the same way everywhere -- so the caller
    owns the process and the WAV is finalised on exit.
    """
    name = os.path.basename(tool)
    if name.startswith("pw-record"):
        return [tool, "--rate", str(rate), "--channels", str(channels), path]
    if name.startswith("arecord"):
        args = [tool, "-q", "-f", "S16_LE", "-r", str(rate), "-c", str(channels)]
        if device:
            args += ["-D", device]
        return args + [path]
    if name.startswith("ffmpeg"):
        if sys.platform == "darwin":
            source = ["-f", "avfoundation", "-i", device or ":0"]
        elif sys.platform.startswith("win"):
            source = ["-f", "dshow", "-i", "audio=%s" % (device or "default")]
        else:
            source = ["-f", "alsa", "-i", device or "default"]
        return [tool, "-loglevel", "error", "-y"] + source + [
            "-ac", str(channels), "-ar", str(rate), path]
    if name.startswith(("sox", "rec")):
        return [tool, "-q", "-d", "-r", str(rate), "-c", str(channels), "-b", "16", path]
    raise VoiceUnavailable("Neznámý nahrávací nástroj: %s" % tool)


# --------------------------------------------------------------------------- #
# Transcription
# --------------------------------------------------------------------------- #
#: Below this RMS a recording is silence. Measured on this hardware: a real
#: microphone capture of a quiet room reads 0, dictated speech reads ~4300, so
#: the threshold sits far from both and only catches genuine nothing.
SILENCE_RMS = 60.0


def signal_level(wav_path):
    """(rms, seconds) of a 16-bit WAV, or (None, 0) if it cannot be read."""
    try:
        with wave.open(wav_path, "rb") as handle:
            if handle.getsampwidth() != 2:
                return None, 0.0
            frames = handle.getnframes()
            data = array.array("h", handle.readframes(frames))
            seconds = frames / float(handle.getframerate() or 1)
    except (wave.Error, OSError, ValueError):
        return None, 0.0
    if not data:
        return 0.0, seconds
    return (sum(float(v) * v for v in data) / len(data)) ** 0.5, seconds


def transcribe_args(binary, model, wav_path, language="cs", vad=None):
    """whisper.cpp invocation that prints only the words.

    `-nt` drops timestamps and `-np` drops the progress chatter, so stdout is the
    transcript and nothing else -- no log lines to filter out of the prompt.
    """
    # Threads capped at 8: measured on a 16-core machine, 16 threads took 43 s
    # where 8 took 26 s -- past a point the threads fight over memory bandwidth
    # and each extra one makes it slower.
    threads = max(1, min(8, (os.cpu_count() or 4) // 2))
    args = [binary, "-m", model, "-f", wav_path, "-l", language,
            "-nt", "-np", "-t", str(threads)]
    if vad:
        # Voice-activity detection, and it is not optional cleverness: fed a
        # recording with no speech in it, whisper does not stay quiet, it invents
        # a fluent sentence ("Titulky vytvořil JohnyX." -- a leftover from
        # subtitle training data). Measured on this machine: with VAD the same
        # silent file transcribes to nothing at all, while dictated speech comes
        # back unchanged. --suppress-nst alone did not help.
        args += ["--vad", "-vm", vad]
    return args


#: whisper.cpp emits these bracketed markers instead of words when it hears
#: nothing useful. Left in, they would be typed into the prompt as if dictated.
NOISE_MARKERS = re.compile(
    r"\[(?:BLANK_AUDIO|SOUND|MUSIC|NOISE|INAUDIBLE|_[A-Z]+_)\]|"
    r"\((?:hudba|ticho|smích|music|silence|laughter)[^)]*\)", re.I)


def clean_transcript(text):
    """Turn whisper.cpp output into something fit to drop into a prompt box."""
    if not text:
        return ""
    lines = []
    for line in text.splitlines():
        line = NOISE_MARKERS.sub(" ", line)
        line = re.sub(r"^\s*\[[0-9:.\s\->]+\]\s*", "", line)   # stray timestamps
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return " ".join(lines).strip()


def transcribe(wav_path, binary=None, model=None, language="cs", runner=None,
               timeout=300):
    """Run the transcription and return the text. Never returns a partial guess."""
    binary = binary or whisper_binary()
    model = model or whisper_model()
    if not binary or not model:
        raise VoiceUnavailable("whisper.cpp nebo model chybí — hlas není nastavený.")
    if not os.path.isfile(wav_path) or os.path.getsize(wav_path) < 1024:
        raise VoiceUnavailable("Nahrávka je prázdná — nic jsem neslyšel.")

    # Silence is not transcribed, it is refused. Fed a silent recording, whisper
    # does not return nothing -- it invents a plausible sentence (measured here:
    # half a second of digital silence produced "Titulky vytvořil JohnyX."). That
    # is a fabricated instruction landing in a prompt box, so the audio is
    # checked for signal before the model is ever asked.
    rms, _seconds = signal_level(wav_path)
    if rms is not None and rms < SILENCE_RMS:
        # Digital silence rather than room tone almost always means the input is
        # muted, not that the room was quiet -- a live microphone always picks up
        # some noise floor. Saying so saves the user from concluding the feature
        # is broken. (Found exactly this way: the default source on the
        # development machine was muted and every recording read as a flat zero.)
        hint = (" Vypadá to, že je vstup ztlumený — zkontroluj mikrofon."
                if rms == 0 else "")
        raise VoiceUnavailable("Nahrávka je téměř tichá — nic jsem neslyšel." + hint)

    args = transcribe_args(binary, model, wav_path, language, vad=vad_model())
    runner = runner or (lambda cmd: subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout))
    result = runner(args)
    if getattr(result, "returncode", 0) != 0:
        detail = (getattr(result, "stderr", b"") or b"").decode("utf-8", "replace")
        raise VoiceUnavailable("Přepis selhal: %s" % (detail.strip()[-300:] or "?"))
    return clean_transcript(
        (getattr(result, "stdout", b"") or b"").decode("utf-8", "replace"))


def install_hint(model_dir=None):
    lines = ["Hlasové zadání potřebuje dvě věci, obojí jednorázově:", "",
             "1. whisper.cpp — `whisper-cli` v PATH",
             "   (Ubuntu: sudo apt install whisper.cpp, jinak build ze zdrojů)",
             "2. model pro češtinu, do %s:" % (model_dir or MODEL_DIR), ""]
    for name, label, size_mb, note in MODELS:
        lines.append("   %-32s %5d MB  %s" % (label, size_mb, note))
    lines += ["", "Nic z toho se nestahuje samo — model je velký a je to tvoje volba.",
              "Zvuk nikam neodchází, přepis běží celý u tebe."]
    return "\n".join(lines)

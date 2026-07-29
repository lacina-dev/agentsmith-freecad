"""Tests for dictating a prompt.

What matters here is not that speech recognition works -- that is whisper.cpp's
job -- but that everything around it behaves when it does not. A missing binary,
a missing model, an empty recording and a burst of silence are the normal cases,
not the exceptional ones, and each has to produce something a person can act on
rather than a traceback or, worse, a plausible-looking wrong prompt.

The other thing pinned here: whisper.cpp's placeholder markers for "I heard
nothing" must never survive into the prompt box. `[BLANK_AUDIO]` typed into a
modelling prompt is not a transcript, it is a bug with good manners.
"""

import os
import sys
import tempfile
import unittest

import helpers  # noqa: F401

sys.path.insert(0, helpers.ADDON_DIR)
import agentsmith_voice as voice  # noqa: E402


class Result(object):
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


class RecorderArgsTests(unittest.TestCase):
    def test_arecord_records_mono_16k(self):
        args = voice.record_args("/usr/bin/arecord", "/tmp/x.wav")
        self.assertIn("-r", args)
        self.assertEqual(args[args.index("-r") + 1], "16000")
        self.assertEqual(args[args.index("-c") + 1], "1")
        self.assertEqual(args[-1], "/tmp/x.wav")

    def test_pw_record_is_supported(self):
        args = voice.record_args("/usr/bin/pw-record", "/tmp/x.wav")
        self.assertEqual(args[0], "/usr/bin/pw-record")
        self.assertEqual(args[-1], "/tmp/x.wav")

    def test_ffmpeg_picks_the_platform_input(self):
        real_platform = voice.sys.platform
        try:
            for platform, expected in (("linux", "alsa"), ("darwin", "avfoundation"),
                                       ("win32", "dshow")):
                voice.sys.platform = platform
                args = voice.record_args("/usr/bin/ffmpeg", "/tmp/x.wav")
                self.assertIn(expected, args, platform)
        finally:
            voice.sys.platform = real_platform

    def test_ffmpeg_overwrites_rather_than_prompting(self):
        # Without -y ffmpeg stops and waits for a keypress nobody will see.
        self.assertIn("-y", voice.record_args("/usr/bin/ffmpeg", "/tmp/x.wav"))

    def test_device_is_passed_through_when_given(self):
        args = voice.record_args("/usr/bin/arecord", "/tmp/x.wav", device="hw:1,0")
        self.assertIn("hw:1,0", args)

    def test_unknown_recorder_is_refused_clearly(self):
        with self.assertRaises(voice.VoiceUnavailable):
            voice.record_args("/usr/bin/nonsense", "/tmp/x.wav")


class TranscribeArgsTests(unittest.TestCase):
    def test_output_is_words_only(self):
        args = voice.transcribe_args("/usr/bin/whisper-cli", "/m.bin", "/a.wav")
        self.assertIn("-nt", args)     # no timestamps
        self.assertIn("-np", args)     # no progress chatter
        # Default is whisper's own language detection: dictation must work in
        # Czech, English or anything else without a setting.
        self.assertEqual(args[args.index("-l") + 1], "auto")

    def test_language_is_selectable(self):
        args = voice.transcribe_args("/w", "/m.bin", "/a.wav", language="en")
        self.assertEqual(args[args.index("-l") + 1], "en")

    def test_language_env_override(self):
        os.environ["AGENTSMITH_WHISPER_LANG"] = "cs"
        try:
            args = voice.transcribe_args("/w", "/m.bin", "/a.wav")
            self.assertEqual(args[args.index("-l") + 1], "cs")
        finally:
            del os.environ["AGENTSMITH_WHISPER_LANG"]


class CleanTranscriptTests(unittest.TestCase):
    def test_blank_audio_marker_never_reaches_the_prompt(self):
        self.assertEqual(voice.clean_transcript("[BLANK_AUDIO]"), "")

    def test_other_noise_markers_are_stripped(self):
        for marker in ("[SOUND]", "[MUSIC]", "[INAUDIBLE]", "(hudba)", "(silence)"):
            self.assertEqual(voice.clean_transcript(marker), "", marker)

    def test_words_survive_alongside_a_marker(self):
        text = voice.clean_transcript("[SOUND] Udělej držák na kabel.")
        self.assertEqual(text, "Udělej držák na kabel.")

    def test_multiple_lines_become_one_prompt(self):
        text = voice.clean_transcript("Udělej krabičku\n  padesát na třicet.  \n")
        self.assertEqual(text, "Udělej krabičku padesát na třicet.")

    def test_stray_timestamps_are_removed(self):
        text = voice.clean_transcript("[00:00:00.000 --> 00:00:02.000]  Ahoj světe")
        self.assertEqual(text, "Ahoj světe")

    def test_diacritics_survive(self):
        self.assertEqual(voice.clean_transcript("Příruba, 22 mm, měkčí"),
                         "Příruba, 22 mm, měkčí")

    def test_empty_input_is_empty_output(self):
        self.assertEqual(voice.clean_transcript(""), "")
        self.assertEqual(voice.clean_transcript(None), "")


class TranscribeTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        handle.write(b"RIFF" + b"\x00" * 4096)
        handle.close()
        self.wav = handle.name

    def tearDown(self):
        os.unlink(self.wav)

    def test_transcript_is_returned_cleaned(self):
        result = voice.transcribe(
            self.wav, binary="/w", model="/m.bin",
            runner=lambda cmd: Result(stdout=" Udělej mi držák.\n".encode()))
        self.assertEqual(result, "Udělej mi držák.")

    def test_missing_tooling_says_so_instead_of_crashing(self):
        # The lookups are patched rather than left to the environment: this test
        # passed for the wrong reason until whisper.cpp was actually installed on
        # the development machine, at which point "nothing is installed" stopped
        # being true and the assertion quietly stopped meaning anything.
        saved = (voice.whisper_binary, voice.whisper_model)
        voice.whisper_binary = lambda: None
        voice.whisper_model = lambda *a, **k: None
        try:
            with self.assertRaises(voice.VoiceUnavailable):
                voice.transcribe(self.wav, runner=lambda cmd: Result())
        finally:
            voice.whisper_binary, voice.whisper_model = saved

    def test_empty_recording_is_reported_not_transcribed(self):
        # A tapped-and-released button produces a header-only WAV; running the
        # model on it wastes half a minute to return nothing.
        empty = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        empty.write(b"RIFF")
        empty.close()
        try:
            with self.assertRaises(voice.VoiceUnavailable) as caught:
                voice.transcribe(empty.name, binary="/w", model="/m.bin",
                                 runner=lambda cmd: Result())
            self.assertIn("empty", str(caught.exception))
        finally:
            os.unlink(empty.name)

    def test_a_failing_whisper_surfaces_its_own_message(self):
        with self.assertRaises(voice.VoiceUnavailable) as caught:
            voice.transcribe(self.wav, binary="/w", model="/m.bin",
                             runner=lambda cmd: Result(
                                 stderr=b"error: failed to load model", returncode=1))
        self.assertIn("failed to load model", str(caught.exception))

    def test_silence_yields_an_empty_string_not_a_marker(self):
        result = voice.transcribe(self.wav, binary="/w", model="/m.bin",
                                  runner=lambda cmd: Result(stdout=b"[BLANK_AUDIO]\n"))
        self.assertEqual(result, "")


class SilenceTests(unittest.TestCase):
    """Silence must be refused, not transcribed.

    Fed a silent recording, whisper does not return an empty string -- it invents
    a fluent sentence. Measured on this machine: half a second of digital silence
    produced "Titulky vytvořil JohnyX." A fabricated instruction landing in the
    prompt box is the worst failure this feature can have, so the audio is
    checked for signal before the model is ever asked.
    """

    def make_wav(self, amplitude, seconds=1.0, rate=16000):
        import math
        import struct
        import wave as wavemod
        path = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        with wavemod.open(path, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            frames = int(rate * seconds)
            handle.writeframes(b"".join(
                struct.pack("<h", int(amplitude * math.sin(i * 0.3)))
                for i in range(frames)))
        self.paths.append(path)
        return path

    def setUp(self):
        self.paths = []

    def tearDown(self):
        for path in self.paths:
            os.unlink(path)

    def test_digital_silence_is_refused_before_whisper_runs(self):
        asked = []
        with self.assertRaises(voice.VoiceUnavailable) as caught:
            voice.transcribe(self.make_wav(0), binary="/w", model="/m.bin",
                             runner=lambda cmd: asked.append(cmd) or Result(
                                 stdout=b"Titulky vytvoril JohnyX."))
        self.assertIn("silent", str(caught.exception))
        self.assertEqual(asked, [], "whisper must not be asked about silence")

    def test_flat_zero_suggests_a_muted_input(self):
        # A live microphone always records some noise floor; an exact zero means
        # nothing reached the recorder at all, which is a different problem from
        # "you did not speak" and deserves a different hint.
        with self.assertRaises(voice.VoiceUnavailable) as caught:
            voice.transcribe(self.make_wav(0), binary="/w", model="/m.bin",
                             runner=lambda cmd: Result())
        self.assertIn("muted", str(caught.exception))

    def test_quiet_room_does_not_blame_the_mute_switch(self):
        with self.assertRaises(voice.VoiceUnavailable) as caught:
            voice.transcribe(self.make_wav(20), binary="/w", model="/m.bin",
                             runner=lambda cmd: Result())
        self.assertNotIn("muted", str(caught.exception))

    def test_room_tone_is_still_refused(self):
        with self.assertRaises(voice.VoiceUnavailable):
            voice.transcribe(self.make_wav(20), binary="/w", model="/m.bin",
                             runner=lambda cmd: Result(stdout=b"halucinace"))

    def test_speech_level_audio_passes_through(self):
        text = voice.transcribe(self.make_wav(4000), binary="/w", model="/m.bin",
                                runner=lambda cmd: Result(stdout=b"Udelej drzak."))
        self.assertEqual(text, "Udelej drzak.")

    def test_level_of_a_real_recording_is_measurable(self):
        rms, seconds = voice.signal_level(self.make_wav(4000, seconds=2.0))
        self.assertGreater(rms, voice.SILENCE_RMS)
        self.assertAlmostEqual(seconds, 2.0, places=2)

    def test_unreadable_audio_does_not_block_transcription(self):
        # If the level cannot be measured we must not silently refuse valid
        # audio -- an unknown level is not evidence of silence.
        path = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
        with open(path, "wb") as handle:
            handle.write(b"RIFF" + b"\x01" * 4096)
        self.paths.append(path)
        self.assertEqual(voice.signal_level(path), (None, 0.0))
        self.assertEqual(
            voice.transcribe(path, binary="/w", model="/m.bin",
                             runner=lambda cmd: Result(stdout="prošlo".encode())), "prošlo")


class ThreadTests(unittest.TestCase):
    def test_threads_are_capped(self):
        # Measured on 16 cores: 8 threads took 26 s, 16 took 43 s. Past a point
        # they contend for memory bandwidth and more is slower.
        args = voice.transcribe_args("/w", "/m.bin", "/a.wav")
        self.assertLessEqual(int(args[args.index("-t") + 1]), 8)
        self.assertGreaterEqual(int(args[args.index("-t") + 1]), 1)


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.saved = (voice.recorder_binary, voice.whisper_binary)

    def tearDown(self):
        voice.recorder_binary, voice.whisper_binary = self.saved

    def fake(self, recorder, binary):
        voice.recorder_binary = lambda: recorder
        voice.whisper_binary = lambda: binary

    def test_each_missing_piece_gets_its_own_explanation(self):
        self.fake(None, None)
        report = voice.availability(self.dir)
        self.assertFalse(report["ready"])
        self.assertEqual(len(report["problems"]), 2)     # recorder + whisper
        self.assertTrue(any("recording" in p for p in report["problems"]))
        self.assertTrue(any("whisper" in p for p in report["problems"]))

    def test_missing_model_is_reported_only_once_whisper_exists(self):
        # Telling someone to download a model for a program they do not have is
        # noise; the fixes are ordered.
        self.fake("/usr/bin/arecord", None)
        self.assertFalse(any("model" in p for p in voice.availability(self.dir)["problems"]))
        self.fake("/usr/bin/arecord", "/usr/bin/whisper-cli")
        self.assertTrue(any("model" in p for p in voice.availability(self.dir)["problems"]))

    def test_everything_present_is_ready(self):
        self.fake("/usr/bin/arecord", "/usr/bin/whisper-cli")
        open(os.path.join(self.dir, voice.MODELS[0][0]), "wb").close()
        report = voice.availability(self.dir)
        self.assertTrue(report["ready"])
        self.assertEqual(report["problems"], [])


class ModelChoiceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_the_best_installed_model_wins(self):
        for name in (voice.MODELS[1][0], voice.MODELS[0][0]):
            open(os.path.join(self.dir, name), "wb").close()
        self.assertTrue(voice.whisper_model(self.dir).endswith(voice.MODELS[0][0]))

    def test_a_users_own_model_is_still_used(self):
        open(os.path.join(self.dir, "my-finetune.bin"), "wb").close()
        self.assertTrue(voice.whisper_model(self.dir).endswith("my-finetune.bin"))

    def test_no_model_directory_is_not_an_error(self):
        self.assertIsNone(voice.whisper_model(os.path.join(self.dir, "nope")))

    def test_offered_models_are_usable_for_czech(self):
        # tiny/base transcribe Czech badly enough that offering them would make
        # the feature look broken; they are deliberately absent.
        names = " ".join(name for name, _l, _s, _n in voice.MODELS)
        self.assertNotIn("tiny", names)
        self.assertNotIn("ggml-base", names)
        self.assertIn("large-v3-turbo", names)

    def test_download_urls_are_well_formed(self):
        for name, _label, size_mb, _note in voice.MODELS:
            url = voice.model_url(name)
            self.assertTrue(url.startswith("https://"))
            self.assertTrue(url.endswith(name))
            self.assertGreater(size_mb, 0)

    def test_install_hint_mentions_privacy_and_sizes(self):
        hint = voice.install_hint(self.dir)
        self.assertIn("never leaves", hint)
        self.assertIn("MB", hint)


if __name__ == "__main__":
    unittest.main()

import types

import pytest

import downloader


class _FakeYDL:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def download(self, urls):
        return None


class _FakeModel:
    def transcribe(self, path, fp16=False):
        return {"text": "hello world"}


def test_process_video_to_khmer_does_not_duplicate_error_prefix(monkeypatch):
    monkeypatch.setattr(downloader.yt_dlp, "YoutubeDL", _FakeYDL)
    monkeypatch.setattr(downloader, "_run_ffmpeg", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        downloader,
        "_translate_to_khmer",
        lambda text: (_ for _ in ()).throw(RuntimeError("Khmer translation is rate-limited by the free Google Translate endpoint. Use a paid API (DeepL/Azure/Google Cloud) for reliable Khmer dubbing.")),
    )

    fake_whisper = types.SimpleNamespace(load_model=lambda name: _FakeModel())
    monkeypatch.setitem(__import__("sys").modules, "whisper", fake_whisper)

    with pytest.raises(RuntimeError, match="Khmer translation is rate-limited by the free Google Translate endpoint") as exc:
        downloader.process_video_to_khmer("https://example.com/video.mp4", output_dir=".")

    assert str(exc.value) == "Khmer translation is rate-limited by the free Google Translate endpoint. Use a paid API (DeepL/Azure/Google Cloud) for reliable Khmer dubbing."

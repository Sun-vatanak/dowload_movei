from __future__ import annotations

import html as html_lib
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional


def _load_local_env() -> None:
    """Read a local .env file without depending on python-dotenv."""
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_local_env()
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from urllib.request import HTTPSHandler, Request, build_opener, install_opener, urlopen

import requests

import certifi
import yt_dlp


class _QuietLogger:
    """Silence yt-dlp output for GUI downloads."""

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def _resolve_format(option: str) -> str:
    """Map GUI selections to yt-dlp formats.

    Sites that expose only a generic unknown_video stream may reject a strict mp4/mkv
    selector, so the safe fallback is the more permissive 'best' option.
    """
    format_map = {
        "Best quality": "bestvideo+bestaudio/best",
        "MP4 (best)": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "MKV (best)": "bestvideo[ext=mkv]+bestaudio[ext=m4a]/best[ext=mkv]/best",
        "Audio only": "bestaudio/best",
    }
    return format_map.get(option, "bestvideo+bestaudio/best")


def _detect_extension_from_url(url: str) -> str:
    """Infer a file extension from a media URL when yt-dlp leaves it as unknown_video."""
    lower = url.lower()
    if ".mp4" in lower or "mime_type=video_mp4" in lower:
        return "mp4"
    if ".m3u8" in lower:
        return "mp4"
    if ".webm" in lower:
        return "webm"
    if ".mkv" in lower:
        return "mkv"
    if ".mp3" in lower:
        return "mp3"
    return "mp4"


def _resolve_redirect_url(url: str) -> str:
    """Decode common Facebook redirect URLs to the destination page."""
    parsed = urlparse(url)
    if "l.facebook.com" not in parsed.netloc:
        return url

    params = parse_qs(parsed.query)
    dest = params.get("u", [None])[0]
    if dest:
        return unquote(dest)
    return url


def _extract_direct_media_url(page_url: str) -> Optional[str]:
    """Look for a direct media URL embedded in a page. Returns None if none is found."""
    try:
        request = Request(page_url, headers={"User-Agent": "Mozilla/5.0"})
        context = ssl._create_unverified_context()
        # increase timeout to handle slow CDN responses
        with urlopen(request, timeout=60, context=context) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except Exception:
        return None

    def normalize(candidate: str) -> Optional[str]:
        if not candidate or candidate.startswith("data:"):
            return None
        candidate = html_lib.unescape(candidate).strip()
        if candidate.startswith("//"):
            candidate = "https:" + candidate
        if not candidate.startswith(("http://", "https://")):
            return None
        lower = candidate.lower()
        if any(lower.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")):
            return None
        if "mime_type=video_mp4" in lower or "video_mp4" in lower or "/video/" in lower or lower.endswith((".mp4", ".m3u8", ".webm")):
            return candidate
        return None

    patterns = [
        r'''(?is)<(?:video|source)[^>]+(?:src|srcset)=["']([^"']+)["']''',
        r'''(?is)<meta[^>]+(?:property|name)=["'](?:og:video|twitter:player)["'][^>]+content=["']([^"']+)["']''',
        r'''(?is)video\s+src=["']([^"']+)["']''',
        r'''(?is)https?://[^\s"'<>]+(?:\.mp4|\.m3u8|\.webm|/video/)[^\s"'<>]*''',
    ]

    candidates: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, html, flags=re.I):
            candidate = match.group(1) if match.groups() else match.group(0)
            if candidate:
                candidates.append(candidate)

    for candidate in candidates:
        normalized = normalize(candidate)
        if normalized:
            return normalized

    for url in re.findall(r'https?://[^\s"\'<>]+', html):
        normalized = normalize(url)
        if normalized:
            return normalized

    return None


def _safe_download_name(title: str, fallback: str = "video") -> str:
    """Normalize filenames for saved videos."""
    cleaned = re.sub(r"[\\/:*?\"<>|]", "-", title or fallback)
    cleaned = cleaned.strip() or fallback
    return cleaned


def get_media_output_dir(output_dir: str, option: str) -> Path:
    """Return a media-type-specific folder so videos and MP3s are stored separately."""
    base_dir = Path(output_dir or ".").expanduser()
    base_dir.mkdir(parents=True, exist_ok=True)
    media_folder = "mp3" if option == "Audio only" else "video"
    final_dir = base_dir / media_folder
    final_dir.mkdir(parents=True, exist_ok=True)
    return final_dir


def download_mp3(
    url: str,
    output_dir: Optional[str] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> str:
    """Download a video as an MP3 file."""
    return download_video(
        url,
        option="Audio only",
        output_dir=output_dir,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
    )


def download_video(
    url: str,
    option: str = "Best quality",
    output_dir: Optional[str] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> str:
    """Download a media URL and report progress via callback."""
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("Download stopped by user.")

    target_dir = get_media_output_dir(output_dir or ".", option)

    resolved_url = _resolve_redirect_url(url)
    direct_media_url = _extract_direct_media_url(resolved_url)

    if direct_media_url:
        source_url = direct_media_url
    else:
        source_url = resolved_url

    if not source_url.startswith(("http://", "https://")):
        raise ValueError(
            "This URL does not point to a downloadable media file. Please use the direct video link instead of a Facebook redirect or webpage."
        )

    if source_url == resolved_url and not re.match(r"https?://.*(?:youtube|youtu\.be|vimeo|twitter|x\.com|facebook|instagram|tiktok|dailymotion|bilibili|rumble|vk|soundcloud)", resolved_url, re.I):
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "noplaylist": True}) as ydl:
                ydl.extract_info(source_url, download=False)
        except Exception:
            raise ValueError(
                "This URL does not contain a supported direct video stream. Please paste the actual media URL, not a redirect page or social-share link."
            )

    def handle_progress(d):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Download stopped by user.")

        if d.get("status") != "downloading":
            if d.get("status") == "finished" and progress_callback:
                progress_callback(100.0, "Finalizing file...")
            return

        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        downloaded = d.get("downloaded_bytes") or 0

        if total > 0:
            percent = min(100.0, (downloaded / total) * 100)
        else:
            percent = 0.0

        speed = d.get("speed") or 0
        eta = d.get("eta") or 0
        speed_text = f"{speed / 1024:.1f} KB/s" if speed else "Starting..."
        eta_text = f" • ETA {eta}s" if eta else ""

        if progress_callback:
            progress_callback(percent, f"Downloading... {speed_text}{eta_text}")

    ydl_opts = {
        "format": _resolve_format(option),
        "outtmpl": str(target_dir / "%(title)s.%(ext)s"),
        "noplaylist": False,
        "quiet": True,
        "no_warnings": True,
        "logger": _QuietLogger(),
        "merge_output_format": "mp4",
        "socket_timeout": 60,
        "retries": 3,
        "fragment_retries": 5,
        "http_chunk_size": 1048576,
        "continuedl": True,
        "progress_hooks": [handle_progress],
        "ca_certificates": certifi.where(),
        "nocheckcertificate": True,
    }

    if option == "Audio only":
        ydl_opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",
            }
        ]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("Download stopped by user.")
        info = ydl.extract_info(source_url, download=True)

    if info and isinstance(info, dict):
        final_url = info.get("url") or info.get("webpage_url") or source_url
        ext = (info.get("ext") or "").lower()
        if ext in {"unknown_video"}:
            ext = _detect_extension_from_url(str(final_url))

        for file in target_dir.iterdir():
            if file.name.endswith(".unknown_video"):
                new_name = file.with_suffix(f".{ext}")
                if new_name.exists():
                    file.unlink()
                else:
                    file.rename(new_name)

    return str(target_dir)


def _extract_player_urls(page_url: str) -> list[str]:
    """Find all /player/<id> links on a series/player page."""
    try:
        response = requests.get(
            page_url,
            timeout=60,
            headers={"User-Agent": "Mozilla/5.0"},
            verify=False,
        )
        response.raise_for_status()
        html = response.text
    except Exception:
        return []

    seen: set[str] = set()
    urls: list[str] = []
    for match in re.findall(r'https?://[^\s\"\']+/player/\d+|/player/\d+', html, flags=re.I):
        candidate = match.strip()
        if candidate.startswith("/"):
            candidate = urljoin(page_url, candidate)
        normalized = candidate.split("#", 1)[0].split("?", 1)[0]
        if normalized not in seen and normalized:
            seen.add(normalized)
            urls.append(normalized)
    return urls


def _run_ffmpeg(args: list[str], progress_callback: Optional[Callable[[float, str], None]] = None, cancel_event: Optional[threading.Event] = None) -> None:
    """Run ffmpeg and stream progress text back through the UI callback."""
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("Download stopped by user.")

    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if process.stdout is None:
        process.wait()
        return

    while True:
        line = process.stdout.readline()
        if not line and process.poll() is not None:
            break
        if not line:
            continue
        if progress_callback:
            progress_callback(0.0, line.strip() or "Processing media...")
        if cancel_event is not None and cancel_event.is_set():
            process.terminate()
            raise RuntimeError("Download stopped by user.")

    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"ffmpeg failed with exit code {return_code}.")


def _configure_unverified_https_for_whisper() -> None:
    """Whisper downloads its model over HTTPS and some environments use a self-signed chain."""
    try:
        opener = build_opener(HTTPSHandler(context=ssl._create_unverified_context()))
        install_opener(opener)
    except Exception:
        pass


def _translate_with_deepl(text: str) -> Optional[str]:
    """Translate text to Khmer with DeepL when its API key is configured."""
    api_key = os.environ.get("DEEPL_API_KEY") or os.environ.get("DEEPL_AUTH_KEY")
    if not api_key:
        return None

    response = requests.post(
        "https://api-free.deepl.com/v2/translate",
        headers={
            "Authorization": f"DeepL-Auth-Key {api_key}",
            "Content-Type": "application/json",
        },
        data={
            "text": text,
            "target_lang": "KM",
        },
        timeout=30,
        verify=False,
    )

    if response.status_code == 429:
        raise RuntimeError(
            "Khmer translation is rate-limited by the free Google Translate endpoint. Use a paid API (DeepL/Azure/Google Cloud) for reliable Khmer dubbing."
        )
    response.raise_for_status()

    data = response.json()
    translations = data.get("translations", [])
    if not translations:
        raise ValueError("DeepL returned an empty response for Khmer translation.")

    translated = translations[0].get("text", "").strip()
    return translated or None


def _translate_with_azure(text: str) -> Optional[str]:
    """Translate text to Khmer with Azure Translator when credentials are configured."""
    api_key = os.environ.get("AZURE_TRANSLATOR_KEY")
    region = os.environ.get("AZURE_TRANSLATOR_REGION")
    endpoint = os.environ.get("AZURE_TRANSLATOR_ENDPOINT", "https://api.cognitive.microsofttranslator.com")

    if not api_key or not region:
        return None

    url = endpoint.rstrip("/") + "/translate?api-version=3.0&from=en&to=km"
    headers = {
        "Ocp-Apim-Subscription-Key": api_key,
        "Ocp-Apim-Subscription-Region": region,
        "Content-Type": "application/json",
    }

    response = requests.post(
        url,
        headers=headers,
        json=[{"Text": text}],
        timeout=30,
        verify=False,
    )

    if response.status_code == 429:
        raise RuntimeError(
            "Khmer translation is rate-limited by the free Google Translate endpoint. Use a paid API (DeepL/Azure/Google Cloud) for reliable Khmer dubbing."
        )
    response.raise_for_status()

    data = response.json()
    if not isinstance(data, list) or not data:
        raise ValueError("Azure Translator returned an empty response for Khmer translation.")

    translated = data[0].get("translations", [{}])[0].get("text")
    if translated and translated.strip():
        return translated.strip()
    return None


def _translate_to_khmer(text: str) -> str:
    """Translate text to Khmer using the best available supported provider."""
    if not text or not text.strip():
        return ""

    last_error: Optional[Exception] = None

    try:
        from googletrans import Translator

        translator = Translator()
        result = translator.translate(text, dest="km")
        if result and result.text:
            return result.text.strip()
    except Exception as exc:  # pragma: no cover - upstream provider dependent
        last_error = exc

    encoded = requests.utils.quote(text)
    url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=km&dt=t&q={encoded}"
    try:
        response = requests.get(
            url,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0"},
            verify=False,
        )
        if response.status_code == 429:
            raise RuntimeError(
                "Khmer translation is rate-limited by the free Google Translate endpoint. Use a paid API (DeepL/Azure/Google Cloud) for reliable Khmer dubbing."
            )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list) or not data:
            raise ValueError("Khmer translation failed: empty response from translation service.")
        translated = "".join(part[0] for part in data[0] if part and len(part) > 0)
        if translated.strip():
            return translated.strip()
    except Exception as exc:
        last_error = exc

    try:
        deepl_translation = _translate_with_deepl(text)
        if deepl_translation:
            return deepl_translation
    except RuntimeError:
        raise
    except Exception as exc:
        last_error = exc

    try:
        azure_translation = _translate_with_azure(text)
        if azure_translation:
            return azure_translation
    except RuntimeError:
        raise
    except Exception as exc:
        last_error = exc

    if isinstance(last_error, RuntimeError):
        raise last_error
    if last_error is not None:
        raise RuntimeError(
            "Khmer translation failed because the free translation service is unavailable or rate-limited. Set DEEPL_API_KEY or AZURE_TRANSLATOR_KEY/AZURE_TRANSLATOR_REGION for a paid Khmer translation backend."
        ) from last_error
    raise RuntimeError(
        "Khmer translation failed: no valid translation response was returned. Set DEEPL_API_KEY or AZURE_TRANSLATOR_KEY/AZURE_TRANSLATOR_REGION for a paid Khmer translation backend."
    )


def process_video_to_khmer(
    video_url: str,
    output_dir: Optional[str] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> str:
    """Download a video, transcribe it, translate to Khmer, synthesize Khmer audio, and merge it back into the video."""
    base_output = Path(output_dir or ".").expanduser()
    base_output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="khmer_dub_") as temp_dir:
        temp_path = Path(temp_dir)
        source_video = temp_path / "input_video.mp4"
        audio_wav = temp_path / "audio.wav"
        khmer_audio = temp_path / "khmer_audio.mp3"
        final_video = base_output / "khmer_dubbed_video.mp4"

        if progress_callback:
            progress_callback(5.0, "Downloading video...")
        last_error = None
        for download_format in ["best", "bestvideo+bestaudio/best", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"]:
            try:
                with yt_dlp.YoutubeDL({
                    "format": download_format,
                    "outtmpl": str(source_video),
                    "quiet": True,
                    "noplaylist": True,
                    "no_warnings": True,
                    "nocheckcertificate": True,
                    "ca_certificates": certifi.where(),
                }) as ydl:
                    if cancel_event is not None and cancel_event.is_set():
                        raise RuntimeError("Download stopped by user.")
                    ydl.download([video_url])
                break
            except Exception as exc:  # pragma: no cover - network/source specific fallback
                last_error = exc
                continue
        else:
            raise last_error or RuntimeError("Unable to download the video stream.")

        if progress_callback:
            progress_callback(20.0, "Extracting original audio...")
        _run_ffmpeg([
            "ffmpeg", "-y", "-i", str(source_video), "-vn", "-acodec", "pcm_s16le", "-ar", "16000", str(audio_wav)
        ], progress_callback=progress_callback, cancel_event=cancel_event)

        try:
            import whisper
        except ImportError as exc:
            raise RuntimeError("Whisper is required for Khmer dubbing. Install it with: pip install openai-whisper") from exc

        _configure_unverified_https_for_whisper()
        if progress_callback:
            progress_callback(40.0, "Transcribing audio to text...")
        model = whisper.load_model("base")
        result = model.transcribe(str(audio_wav), fp16=False)
        transcript_text = result.get("text", "").strip()
        if not transcript_text:
            raise ValueError("Speech-to-text did not return any text from the video audio.")

        if progress_callback:
            progress_callback(60.0, "Translating text to Khmer...")
        try:
            khmer_text = _translate_to_khmer(transcript_text)
        except RuntimeError:
            raise
        if not khmer_text:
            raise ValueError("Khmer translation returned empty text.")

        if progress_callback:
            progress_callback(75.0, "Generating Khmer voice...")
        try:
            from gtts import gTTS
            tts = gTTS(text=khmer_text, lang="km")
            tts.save(str(khmer_audio))
        except ImportError:
            try:
                import edge_tts
                asyncio = __import__("asyncio")
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(
                    edge_tts.Communicate(khmer_text, voice="km-KH-PisethNeural").save(str(khmer_audio))
                )
                loop.close()
            except Exception as exc:
                raise RuntimeError("TTS dependency missing. Install gTTS or edge-tts for Khmer voice synthesis.") from exc

        if progress_callback:
            progress_callback(90.0, "Mixing Khmer audio into the video...")
        _run_ffmpeg([
            "ffmpeg", "-y", "-i", str(source_video), "-i", str(khmer_audio), "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", str(final_video)
        ], progress_callback=progress_callback, cancel_event=cancel_event)

        if progress_callback:
            progress_callback(100.0, f"Khmer dub complete: {final_video.name}")

        return str(final_video)


def download_all_videos(
    url: str,
    option: str = "Best quality",
    output_dir: Optional[str] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> list[str]:
    """Download all videos discovered from a page or series listing by scanning page links."""
    target_dir = get_media_output_dir(output_dir or ".", option)

    resolved_url = _resolve_redirect_url(url)
    page_urls = _extract_player_urls(resolved_url)
    if len(page_urls) > 1:
        downloaded: list[str] = []
        for index, item_url in enumerate(page_urls, start=1):
            if cancel_event is not None and cancel_event.is_set():
                break
            try:
                file_dir = download_video(
                    item_url,
                    option=option,
                    output_dir=str(target_dir),
                    progress_callback=lambda percent, message, idx=index, total=len(page_urls): (
                        progress_callback(percent=(idx / total) * 100, message=f"Downloading {idx}/{total}: {message}")
                        if progress_callback else None
                    ) if progress_callback else None,
                    cancel_event=cancel_event,
                )
                if file_dir:
                    downloaded.append(file_dir)
            except Exception:
                continue

        if progress_callback:
            progress_callback(100.0, f"Downloaded {len(downloaded)} videos.")
        return downloaded

    ydl_opts = {
        "format": _resolve_format(option),
        "outtmpl": str(target_dir / "%(title)s.%(ext)s"),
        "noplaylist": False,
        "quiet": True,
        "no_warnings": True,
        "logger": _QuietLogger(),
        "merge_output_format": "mp4",
        "socket_timeout": 60,
        "retries": 3,
        "fragment_retries": 5,
        "http_chunk_size": 1048576,
        "continuedl": True,
        "extract_flat": False,
        "paths": {"home": str(target_dir)},
        "ca_certificates": certifi.where(),
        "nocheckcertificate": False,
    }

    if option == "Audio only":
        ydl_opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",
            }
        ]

    downloaded: list[str] = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(resolved_url, download=True)

    if isinstance(info, dict):
        entries = info.get("entries") or [info]
        for entry in entries:
            if not entry:
                continue
            title = entry.get("title") or "video"
            safe_name = _safe_download_name(title)
            for file in target_dir.iterdir():
                if file.name.startswith(safe_name) and file.is_file():
                    downloaded.append(str(file))
                    break
    elif isinstance(info, list):
        for entry in info:
            if not entry:
                continue
            title = entry.get("title") or "video"
            safe_name = _safe_download_name(title)
            for file in target_dir.iterdir():
                if file.name.startswith(safe_name) and file.is_file():
                    downloaded.append(str(file))
                    break

    if progress_callback:
        progress_callback(100.0, f"Downloaded {len(downloaded)} videos.")

    return downloaded
def process_video_subtitles_to_khmer_voxcp2(
    video_url: str,
    output_dir: Optional[str] = None,
    progress_callback: Optional[Callable[[float, str], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> str:
    """Download a video, extract Chinese subtitles (if present), translate to Khmer,
    synthesize Khmer audio using Voxcpm2 (configurable via `VOXCPM2_CMD`) or gTTS fallback,
    and merge the synthesized audio into the final video.

    The `VOXCPM2_CMD` environment variable can be a command template containing
    `{text}` and `{out}` placeholders. For example:
      VOXCPM2_CMD="/path/to/voxcpm2 --input '{text}' --output {out}"
    If not provided, the function falls back to `gTTS`.
    """
    if cancel_event is not None and cancel_event.is_set():
        raise RuntimeError("Processing stopped by user.")

    base_output = Path(output_dir or ".").expanduser()
    base_output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="sub_khmer_") as tmpdir:
        tmp = Path(tmpdir)
        source_video = tmp / "input_video.%(ext)s"
        video_file = tmp / "input_video.mp4"
        khmer_audio = tmp / "khmer_audio.mp3"
        final_video = base_output / f"khmer_subs_dub_{int(Path(tmpdir).stat().st_ctime)}.mp4"

        if progress_callback:
            progress_callback(5.0, "Downloading video and subtitles...")

        ydl_opts = {
            "format": "bestvideo+bestaudio/best",
            "outtmpl": str(tmp / "input_video.%(ext)s"),
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["zh", "zh-cn", "zh-tw"],
            "subtitlesformat": "srt",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "logger": _QuietLogger(),
            "ca_certificates": certifi.where(),
            "merge_output_format": "mp4",
            "socket_timeout": 60,
            "retries": 3,
            "fragment_retries": 5,
            "http_chunk_size": 1048576,
            "continuedl": True,
        }

        last_dl_error: Optional[Exception] = None
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("Processing stopped by user.")
                ydl.download([video_url])
        except Exception as exc:
            last_dl_error = exc
            # Try a fallback download without subtitle extraction so we can still transcribe audio
            if progress_callback:
                progress_callback(10.0, "Subtitle download failed — attempting fallback video download...")
            try:
                fallback_opts = {k: v for k, v in ydl_opts.items() if k not in {"writesubtitles", "writeautomaticsub", "subtitleslangs", "subtitlesformat"}}
                with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                    if cancel_event is not None and cancel_event.is_set():
                        raise RuntimeError("Processing stopped by user.")
                    ydl.download([video_url])
            except Exception as exc2:
                # Both attempts failed — raise with original error context
                raise RuntimeError(f"Failed to download video/subtitles: {exc}") from exc2

        # Find downloaded files: prefer subtitle SRTs and pick the largest non-subtitle file as video
        found_video = None
        found_srt = None
        candidates = []
        for f in tmp.iterdir():
            if not f.is_file():
                continue
            suf = f.suffix.lower()
            if suf == ".srt":
                name = f.name.lower()
                if "zh" in name or "chi" in name or "chinese" in name:
                    found_srt = f
                elif not found_srt:
                    found_srt = f
                continue
            # ignore small metadata files
            if suf in {".info.json", ".json", ".part"}:
                continue
            try:
                size = f.stat().st_size
            except Exception:
                size = 0
            candidates.append((size, f))

        if candidates:
            # choose largest file as the downloaded media
            candidates.sort(reverse=True, key=lambda x: x[0])
            found_video = candidates[0][1]

        if not found_video:
            raise RuntimeError("Downloaded file not found")

        if progress_callback:
            progress_callback(20.0, "Preparing subtitles for translation...")

        subtitles_text = ""
        if found_srt and found_srt.exists():
            raw = found_srt.read_text(encoding="utf-8", errors="ignore")
            # Simple SRT text extraction: remove indices and timestamps
            parts = re.split(r"\n\s*\n", raw)
            lines: list[str] = []
            for part in parts:
                # each block: optional index line, timestamp line, then text lines
                sublines = part.strip().splitlines()
                if len(sublines) >= 2 and re.search(r"-->", sublines[1]):
                    text_lines = sublines[2:]
                else:
                    # fallback when timestamp not in second line
                    text_lines = [l for l in sublines if not re.match(r"^\d+$", l) and "-->" not in l]
                block_text = " ".join(t.strip() for t in text_lines if t.strip())
                if block_text:
                    lines.append(block_text)
            subtitles_text = "\n".join(lines)
        else:
            # No subtitles — fall back to audio transcription using Whisper (Chinese)
            if progress_callback:
                progress_callback(30.0, "No subtitles found — transcribing audio (Chinese)...")
            try:
                import whisper
            except ImportError as exc:
                raise RuntimeError("Whisper is required to transcribe audio when subtitles are missing.") from exc

            # extract audio
            _run_ffmpeg(["ffmpeg", "-y", "-i", str(found_video), "-vn", "-acodec", "pcm_s16le", "-ar", "16000", str(tmp / "audio.wav")], progress_callback=progress_callback, cancel_event=cancel_event)
            model = whisper.load_model("base")
            result = model.transcribe(str(tmp / "audio.wav"), language="zh", fp16=False)
            subtitles_text = result.get("text", "").strip()

        if not subtitles_text:
            raise RuntimeError("No subtitle text or transcript available to translate.")

        if progress_callback:
            progress_callback(50.0, "Translating text to Khmer...")

        # Translate per-line to keep natural breaks
        translated_lines: list[str] = []
        for line in subtitles_text.splitlines():
            line = line.strip()
            if not line:
                translated_lines.append("")
                continue
            try:
                kh = _translate_to_khmer(line)
            except Exception:
                # if single-line translation fails, try chunking later
                kh = _translate_to_khmer(line)
            translated_lines.append(kh)

        translated_text = "\n".join(translated_lines)

        if progress_callback:
            progress_callback(70.0, "Synthesizing Khmer voice (Voxcpm2/gTTS)...")

        def synthesize_text_to_file(text: str, out_path: Path) -> None:
            cmd_template = os.environ.get("VOXCPM2_CMD")
            if cmd_template:
                # replace placeholders
                cmd = cmd_template.replace("{text}", text.replace("\"", "\\\"")).replace("{out}", str(out_path))
                subprocess.run(cmd, shell=True, check=True)
            else:
                try:
                    from gtts import gTTS

                    tts = gTTS(text=text, lang="km")
                    tts.save(str(out_path))
                except Exception as exc:  # pragma: no cover - fallback
                    raise RuntimeError("TTS failed: configure VOXCPM2_CMD or install gTTS") from exc

        # If the translated text is long, chunk it into smaller pieces to synthesize
        chunks: list[str] = []
        max_len = 3000
        current = []
        cur_len = 0
        for paragraph in translated_text.splitlines():
            if not paragraph:
                if current:
                    chunks.append(" ".join(current))
                    current = []
                    cur_len = 0
                continue
            if cur_len + len(paragraph) + 1 > max_len and current:
                chunks.append(" ".join(current))
                current = [paragraph]
                cur_len = len(paragraph)
            else:
                current.append(paragraph)
                cur_len += len(paragraph) + 1
        if current:
            chunks.append(" ".join(current))

        segment_files: list[Path] = []
        for i, chunk in enumerate(chunks, start=1):
            seg = tmp / f"seg_{i}.mp3"
            synthesize_text_to_file(chunk, seg)
            segment_files.append(seg)

        # If multiple segments, concat them
        if len(segment_files) == 1:
            combined = segment_files[0]
        else:
            concat_list = tmp / "concat.txt"
            with concat_list.open("w", encoding="utf-8") as fh:
                for p in segment_files:
                    fh.write(f"file '{p.as_posix()}'\n")
            combined = tmp / "combined.mp3"
            _run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(combined)])

        # Merge combined audio into video
        _run_ffmpeg([
            "ffmpeg", "-y", "-i", str(found_video), "-i", str(combined), "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", str(final_video)
        ], progress_callback=progress_callback, cancel_event=cancel_event)

        if progress_callback:
            progress_callback(100.0, f"Khmer subtitle dub complete: {final_video.name}")

        return str(final_video)

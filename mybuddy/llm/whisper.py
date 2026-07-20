"""本地 Whisper 语音转文字,完全离线运行。"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path

from mybuddy.config import Config

logger = logging.getLogger(__name__)


def _find_ffmpeg() -> str:
    """查找 ffmpeg.exe 完整路径,找不到则报错。"""
    found = shutil.which("ffmpeg")
    if found:
        return os.path.abspath(found)
    # winget 安装目录
    base = Path(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages"))
    if base.exists():
        for d in sorted(base.glob("Gyan.FFmpeg*"), reverse=True):
            for exe in d.rglob("ffmpeg.exe"):
                return str(exe.resolve())
    raise RuntimeError("找不到 ffmpeg,请安装: winget install ffmpeg")


class Transcriber:
    """openai-whisper 本地模型封装。"""

    def __init__(self, model_name: str, language: str, download_root: str) -> None:
        import whisper

        self._download_root = download_root
        self._ffmpeg = _find_ffmpeg()
        logger.info("ffmpeg: %s", self._ffmpeg)
        logger.info("加载 Whisper 模型: %s", model_name)
        self._model = whisper.load_model(model_name, download_root=download_root)
        self._language = language

    async def transcribe(self, audio_bytes: bytes) -> str:
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name
        try:
            return await asyncio.to_thread(self._transcribe_sync, tmp_path)
        finally:
            os.unlink(tmp_path)

    def _transcribe_sync(self, file_path: str) -> str:
        from whisper import audio as whisper_audio

        original_run = whisper_audio.run
        try:
            whisper_audio.run = lambda cmd, **kw: original_run(
                [self._ffmpeg if cmd[0] == "ffmpeg" else cmd[0]] + cmd[1:], **kw
            )
            result = self._model.transcribe(file_path, language=self._language)
            return result["text"].strip()
        finally:
            whisper_audio.run = original_run


def make_transcriber(cfg: Config) -> Transcriber | None:
    if not cfg.transcription.enabled:
        return None
    try:
        download_root = str(Path(cfg.paths.data_dir) / "whisper-models")
        return Transcriber(cfg.transcription.model, cfg.transcription.language, download_root)
    except Exception:
        logger.exception("Whisper 模型加载失败,语音转文字功能不可用")
        return None

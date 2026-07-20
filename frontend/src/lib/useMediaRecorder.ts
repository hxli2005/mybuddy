import { useCallback, useRef, useState } from "react";

export interface UseMediaRecorderResult {
  recording: boolean;
  supported: boolean;
  error: string | null;
  start: () => Promise<void>;
  stop: () => Promise<Blob>;
}

export function useMediaRecorder(): UseMediaRecorderResult {
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const resolveRef = useRef<((blob: Blob) => void) | null>(null);

  const supported =
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof MediaRecorder !== "undefined";

  const start = useCallback(async () => {
    if (!supported) return;
    setError(null);
    chunksRef.current = [];
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const recorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      recorder.onerror = () => {
        setError("录音出错");
        setRecording(false);
      };
      recorderRef.current = recorder;
      recorder.start();
      setRecording(true);
    } catch (e) {
      const err = e as DOMException;
      setError(err.name === "NotAllowedError" ? "麦克风权限被拒绝" : "无法访问麦克风");
    }
  }, [supported]);

  const stop = useCallback((): Promise<Blob> => {
    return new Promise((resolve) => {
      if (!recorderRef.current || recorderRef.current.state === "inactive") {
        resolve(new Blob(chunksRef.current, { type: "audio/webm" }));
        cleanup();
        return;
      }
      resolveRef.current = resolve;
      recorderRef.current.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        cleanup();
        resolve(blob);
      };
      recorderRef.current.stop();
    });
  }, []);

  const cleanup = () => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    recorderRef.current = null;
    setRecording(false);
  };

  return { recording, supported, error, start, stop };
}

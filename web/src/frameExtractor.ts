// Client-side frame extraction — THE core cost decision.
//
// A 15-minute capture is 1-4 GB. We never upload it. Instead we seek through the
// video in the browser with a <video> element + <canvas>, downscale + JPEG each
// sampled frame, and upload ~5-20 MB of frames instead of gigabytes of source.

export interface ExtractedFrame {
  index: number;
  timestampMs: number;
  blob: Blob;
}

export interface ExtractOptions {
  fps: number; // frames sampled per second of video
  maxWidth: number; // downscale target; keep high enough that HUD digits stay legible
  quality: number; // JPEG quality 0..1
  onProgress?: (done: number, total: number, previewUrl?: string) => void;
}

function seek(video: HTMLVideoElement, timeSec: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const onSeeked = () => {
      video.removeEventListener("seeked", onSeeked);
      video.removeEventListener("error", onError);
      resolve();
    };
    const onError = () => {
      video.removeEventListener("seeked", onSeeked);
      video.removeEventListener("error", onError);
      reject(new Error("video seek failed"));
    };
    video.addEventListener("seeked", onSeeked);
    video.addEventListener("error", onError);
    video.currentTime = timeSec;
  });
}

function loadMetadata(video: HTMLVideoElement): Promise<void> {
  return new Promise((resolve, reject) => {
    video.addEventListener("loadedmetadata", () => resolve(), { once: true });
    video.addEventListener("error", () => reject(new Error("cannot read video")), { once: true });
  });
}

export async function* extractFrames(
  file: File,
  opts: ExtractOptions
): AsyncGenerator<ExtractedFrame> {
  const url = URL.createObjectURL(file);
  const video = document.createElement("video");
  video.muted = true;
  video.playsInline = true;
  video.preload = "auto";
  video.src = url;

  try {
    await loadMetadata(video);
    const duration = video.duration;
    if (!isFinite(duration) || duration <= 0) {
      throw new Error("could not determine video duration");
    }

    const scale = Math.min(1, opts.maxWidth / video.videoWidth);
    const w = Math.round(video.videoWidth * scale);
    const h = Math.round(video.videoHeight * scale);
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d", { alpha: false })!;

    const step = 1 / opts.fps;
    const total = Math.floor(duration / step) + 1;
    let index = 0;

    for (let t = 0; t < duration; t += step, index++) {
      await seek(video, t);
      ctx.drawImage(video, 0, 0, w, h);
      const blob: Blob = await new Promise((res) =>
        canvas.toBlob((b) => res(b as Blob), "image/jpeg", opts.quality)
      );
      opts.onProgress?.(index + 1, total, index % 20 === 0 ? canvas.toDataURL("image/jpeg", 0.4) : undefined);
      yield { index, timestampMs: Math.round(t * 1000), blob };
    }
  } finally {
    URL.revokeObjectURL(url);
    video.remove();
  }
}

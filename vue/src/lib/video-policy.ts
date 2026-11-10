export type VideoPreference = { resolution: 'auto' | 240 | 360 | 480 | 720; fps: 10 | 15 | 20 };
export type VideoProfile = { short: number; fps: number };
export type NetworkSample = { rttMs: number | null; loss: number; bufferMs: number; decodeMs: number };
export const DEFAULT_VIDEO: VideoPreference = { resolution: 'auto', fps: 10 };
export function videoPreference(value: unknown): VideoPreference {
  const x = value as Partial<VideoPreference> | null;
  return { resolution: x && ['auto',240,360,480,720].includes(x.resolution!) ? x.resolution! : 'auto',
    fps: x && [10,15,20].includes(x.fps!) ? x.fps! : 10 };
}
export function captureScale(short: number, width: number, height: number) {
  const edge = Math.min(width, height);
  return Math.max(.1, Math.min(1, short / (Number.isFinite(edge) && edge > 0 ? edge : 1080)));
}
/** A negotiated connection without an encoded first frame can indicate an
 * unsupported hardware encoder profile. Quarantine it for this session only. */
export function compatibleProfile(requested: VideoProfile, rejected: ReadonlySet<number>): VideoProfile | null {
  const short=[720,480,360,240].find(edge=>edge<=requested.short&&!rejected.has(edge));
  return short===undefined?null:{short,fps:requested.fps};
}
/** Fast degradation, slow recovery. Idle clarity uses a separate still frame,
 * so it never renegotiates the peer or delays an in-flight gesture. */
export class VideoPolicy {
  level = 1;
  bad = 0; good = 0; lastChange = -Infinity;
  profile(preference: VideoPreference): VideoProfile {
    if (preference.resolution !== 'auto') return { short: preference.resolution, fps: preference.fps };
    // Auto prioritizes interaction latency. Escalating a working 360 stream
    // to an unproven hardware profile can leave a connected peer without video.
    // Readable idle detail is provided by the separate 720 still; higher live
    // resolutions remain an explicit manual choice.
    return [{ short:240,fps:10 },{ short:360,fps:10 }][this.level]!;
  }
  sample(s: NetworkSample, now: number): boolean {
    const bad = s.loss > .03 || s.bufferMs > 180 || s.decodeMs > 60 || (s.rttMs ?? 0) > 300;
    this.bad = bad ? this.bad + 1 : 0;
    this.good = !bad && s.loss < .005 && s.bufferMs < 80 && (s.rttMs ?? 999) < 160 ? this.good + 1 : 0;
    if (this.bad >= 2 && this.level > 0 && now-this.lastChange >= 10000) {
      this.level--; this.lastChange=now; this.bad=this.good=0; return true;
    }
    if (this.good >= 15 && this.level < 1 && now-this.lastChange >= 30000) {
      this.level++; this.lastChange=now; this.bad=this.good=0; return true;
    }
    return false;
  }
}

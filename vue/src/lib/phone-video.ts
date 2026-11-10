/** FIRERPA video transport. No device configuration is changed by this client. */
import { captureScale, DEFAULT_VIDEO, VideoPolicy, type VideoPreference, type VideoProfile, type NetworkSample } from './video-policy.ts';
import { pollFrames } from './phone-frame.ts';
export type VideoMode = 'webrtc' | 'h264' | 'mjpeg';
type Cancel = () => void;
export type VideoOptions = {
  url: (path: string) => string;
  canvas: HTMLCanvasElement;
  /** Mounted compositor surface. Canvas remains the input/fallback surface. */
  video?: HTMLVideoElement;
  decoderUrl: string;
  iceServers?: RTCIceServer[];
  loadIceServers?: () => Promise<RTCIceServer[]>;
  webrtc?: boolean;
  frame: (width: number, height: number) => void;
  state: (state: string) => void;
  mode: (mode: VideoMode) => void;
  log: (message: string) => void;
  stats?: (value: { rttMs: number | null; path: string; decoded: number; dropped: number }) => void;
  inputChannel?: (channel: RTCDataChannel) => void;
  preference?: VideoPreference;
  displaySize?: () => { width: number; height: number };
  snapshot?: (short: number, signal: AbortSignal) => Promise<Blob>;
  detail?: (active: boolean) => void;
  profile?: (value: VideoProfile) => void;
  interactionBusy?: () => boolean;
};

type RtcOptions = Pick<VideoOptions, 'url' | 'iceServers' | 'video'> & {
  frame: (video: HTMLVideoElement) => void;
  ready: () => void;
  failed: (reason: string) => void;
  stats?: VideoOptions['stats'];
  inputChannel?: VideoOptions['inputChannel'];
  profile?: VideoProfile;
  displaySize?: VideoOptions['displaySize'];
  network?: (sample: NetworkSample) => void;
};

/** One RTC attempt; its cleanup also invalidates every outstanding async callback. */
export function startRtcVideo(o: RtcOptions): Cancel {
  let closed = false, ready = false, remoteReady = false, offered = false;
  let pc: RTCPeerConnection | undefined, ws: WebSocket | undefined;
  let video: HTMLVideoElement | undefined, frameId = 0, raf = 0, previousTime = -1, previousDecoded = -1;
  let lastCallback = 0, paintWatchdog: ReturnType<typeof setInterval> | undefined;
  let first: ReturnType<typeof setTimeout> | undefined, disconnected: ReturnType<typeof setTimeout> | undefined;
  let ping: ReturnType<typeof setInterval> | undefined, statsTimer: ReturnType<typeof setInterval> | undefined;
  let statsBusy = false, incoming = Promise.resolve();
  let previousStats: any;
  const remoteCandidates: (RTCIceCandidateInit | null)[] = [];
  const localCandidates: (RTCIceCandidateInit | null)[] = [];
  const close = () => {
    if (closed) return; closed = true;
    clearTimeout(first); clearTimeout(disconnected); clearInterval(ping); clearInterval(statsTimer); clearInterval(paintWatchdog);
    if (video) { if (frameId) video.cancelVideoFrameCallback?.(frameId); cancelAnimationFrame(raf); video.pause(); video.srcObject = null; }
    if (ws) { ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null; ws.close(); }
    if (pc) {
      pc.ontrack = pc.onicecandidate = pc.onconnectionstatechange = pc.oniceconnectionstatechange = null;
      pc.getReceivers().forEach(r => r.track?.stop()); pc.close();
    }
    remoteCandidates.length = localCandidates.length = 0;
  };
  const fail = (reason: string) => { if (closed) return; close(); o.failed(reason); };
  const send = (type: string, data?: unknown) => { if (!closed && ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type, data })); };
  const local = (candidate: RTCIceCandidateInit | null) => candidate ? send('candidate', candidate) : send('ice-candidates-complete');
  function drawLatest() {
    if (closed || !video || (o.video && !video.srcObject)) return;
    const decoded = video.getVideoPlaybackQuality?.().totalVideoFrames;
    const changed = decoded === undefined ? video.currentTime !== previousTime : decoded !== previousDecoded;
    if (video.readyState >= 2 && video.videoWidth && video.videoHeight && changed) {
      previousTime = video.currentTime; previousDecoded = decoded ?? -1;
      if (!ready) { ready = true; clearTimeout(first); o.ready(); }
      if (closed) return;
      try { o.frame(video); } catch { fail('视频绘制失败'); return; }
    }
  }
  function render() {
    if (closed || !video) return;
    lastCallback = performance.now(); drawLatest();
    if (closed) return;
    if (video.requestVideoFrameCallback) frameId = video.requestVideoFrameCallback(render);
    else raf = requestAnimationFrame(render);
  }
  try {
    const iceServers = o.iceServers ?? [{ urls: 'stun:stun.device-farm.com' }];
    pc = new RTCPeerConnection({ iceServers });
    // Same authenticated peer and DTLS session as video. Phones without the
    // BK receiver never send capability/ready, so native WSS remains active.
    if (o.inputChannel) o.inputChannel(pc.createDataChannel('bk-touch-v1', { ordered: true, protocol: 'bk.touch.v1' }));
    video = o.video ?? document.createElement('video'); video.autoplay = true; video.muted = true; video.playsInline = true;
    pc.ontrack = e => {
      if (closed || !video || e.track.kind !== 'video') return;
      // A preference, not a promise: the browser still raises its minimum for
      // network jitter. Unsupported/readonly implementations must keep working.
      try {
        if ('jitterBufferTarget' in e.receiver) {
          (e.receiver as RTCRtpReceiver & { jitterBufferTarget: number }).jitterBufferTarget = 20;
        }
      } catch { /* Leave the browser's adaptive buffering in place. */ }
      video.srcObject = e.streams[0] || new MediaStream([e.track]);
      e.track.onended = () => fail('视频轨道结束');
      void video.play().catch(() => fail('浏览器未允许视频播放'));
    };
    const connection = () => {
      if (closed || !pc) return;
      const bad = [pc.connectionState, pc.iceConnectionState];
      if (bad.includes('failed') || bad.includes('closed')) { fail('媒体连接中断'); return; }
      if (bad.includes('disconnected')) disconnected ??= setTimeout(() => fail('媒体连接超时'), 3000);
      else { clearTimeout(disconnected); disconnected = undefined; }
    };
    pc.onconnectionstatechange = pc.oniceconnectionstatechange = connection;
    pc.onicecandidate = e => {
      if (closed) return;
      const candidate = e.candidate?.toJSON() ?? null;
      if (offered) local(candidate); else if (localCandidates.length < 128) localCandidates.push(candidate);
    };
    ws = new WebSocket(o.url('/ws/webrtc/signaling'));
    ws.onopen = () => { void (async () => {
      const peer = pc!;
      const offer = await peer.createOffer({ offerToReceiveAudio: false, offerToReceiveVideo: true });
      if (closed) return; await peer.setLocalDescription(offer); if (closed) return;
      const profile=o.profile??{short:360,fps:10},size=o.displaySize?.()??{width:1080,height:2388};
      send('request-offer', { type: offer.type, sdp: offer.sdp, servers: iceServers, display: 0, backend: 1,
        scale:captureScale(profile.short,size.width,size.height), quality:25, fps:profile.fps });
      offered = true; localCandidates.splice(0).forEach(local);
    })().catch(() => fail('视频协商失败')); };
    ws.onmessage = e => {
      // Preserve signaling order across async setRemoteDescription/addIceCandidate.
      incoming = incoming.then(async () => {
        if (closed) return; const m = JSON.parse(e.data); const peer = pc!;
        if (m.type === 'answer') {
          await peer.setRemoteDescription(m.data); if (closed) return; remoteReady = true;
          for (const c of remoteCandidates.splice(0)) { if (closed) return; await peer.addIceCandidate(c ?? undefined); }
        } else if (m.type === 'candidate' || m.type === 'ice-candidates-complete') {
          const c = m.type === 'candidate' ? m.data : null;
          if (remoteReady) await peer.addIceCandidate(c ?? undefined);
          else if (remoteCandidates.length < 128) remoteCandidates.push(c);
          else fail('视频协商消息过多');
        }
      }).catch(() => fail('视频协商响应异常'));
    };
    // Once media is flowing, a signaling socket closure alone need not interrupt it.
    ws.onclose = ws.onerror = () => { if (!ready) fail('视频信令未连接'); };
    first = setTimeout(() => fail('视频首帧等待超时'), 12000);
    ping = setInterval(() => send('ping'), 15000);
    statsTimer = setInterval(() => {
      if (closed || !ready || statsBusy || !pc) return;
      statsBusy = true;
      void pc.getStats().then(report => {
        if (closed) return;
        const all: any[] = []; report.forEach(s => all.push(s));
        const transport = all.find(s => s.type === 'transport');
        const pair = all.find(s => s.id === transport?.selectedCandidatePairId);
        const receiver = all.find(s => s.type === 'inbound-rtp' && s.kind === 'video');
        const local = all.find(s => s.id === pair?.localCandidateId), remote = all.find(s => s.id === pair?.remoteCandidateId);
        o.stats?.({ rttMs: Number.isFinite(pair?.currentRoundTripTime) ? Math.round(pair.currentRoundTripTime * 1000) : null,
          path: local?.candidateType === 'relay' || remote?.candidateType === 'relay' ? 'relay' : pair ? 'direct' : 'unknown',
          decoded: receiver?.framesDecoded ?? 0, dropped: receiver?.framesDropped ?? 0 });
        if(receiver && previousStats && receiver.id===previousStats.id) {
          const delta=(key:string)=>Math.max(0,(receiver[key]??0)-(previousStats[key]??0));
          const received=delta('packetsReceived'),lost=delta('packetsLost'),emitted=delta('jitterBufferEmittedCount'),decoded=delta('framesDecoded');
          // No changed frames is a valid idle screen, not congestion.
          if(received+lost>0) o.network?.({rttMs:Number.isFinite(pair?.currentRoundTripTime)?pair.currentRoundTripTime*1000:null,
            loss:lost/Math.max(1,received+lost),bufferMs:emitted?delta('jitterBufferDelay')*1000/emitted:0,
            decodeMs:decoded?delta('totalDecodeTime')*1000/decoded:0});
        }
        previousStats=receiver;
      }).catch(() => {}).finally(() => { statsBusy = false; });
    }, 2000);
    render();
    // Mounted video presents independently of JS. This watchdog updates first
    // frame/dimensions/stats, and only paints for legacy canvas consumers.
    paintWatchdog = setInterval(() => {
      if (performance.now() - lastCallback >= 150) drawLatest();
    }, 100);
  } catch { fail('浏览器无法建立 WebRTC'); }
  return close;
}

/** Driver screenshots avoid the native screen fallback's pre-exec deadlock. */
function startSocketVideo(o: VideoOptions): Cancel {
  o.mode('mjpeg');o.state('connecting');
  if(!o.snapshot)return ()=>{};
  return pollFrames(signal=>o.snapshot!(360,signal),image=>{
    o.canvas.width=image.width;o.canvas.height=image.height;
    o.canvas.getContext('2d',{alpha:false})?.drawImage(image,0,0);
    o.frame(image.width,image.height);
  },()=>o.state('reconnecting'));
}

export function startPhoneVideo(o: VideoOptions): Cancel & { recover: () => void; interact: () => void } {
  let disposed = false, socket: Cancel | undefined, rtc: Cancel | undefined;
  let retry: ReturnType<typeof setTimeout> | undefined, attempts = 0, connectedAt = 0, upgrading = false;
  const policy=new VideoPolicy(),preference=o.preference??DEFAULT_VIDEO;
  let lastInteraction=performance.now(),lastMotion=performance.now(),sampled=0,signature:Uint8ClampedArray|undefined;
  let detail=false,detailAt=0,stillBusy=false,stillAbort:AbortController|undefined,stillVersion=0,lastStillAttempt=0;
  const probeWidth=64,probeHeight=144;
  const probe=document.createElement('canvas');probe.width=probeWidth;probe.height=probeHeight;
  const probeContext=probe.getContext?.('2d',{willReadFrequently:true});
  const clearDetail=()=>{stillVersion++;stillAbort?.abort();if(detail){detail=false;o.detail?.(false);o.canvas.getContext('2d')?.clearRect(0,0,o.canvas.width,o.canvas.height);}};
  const interact=()=>{lastInteraction=performance.now();clearDetail();};
  const inspectFrame=(video:HTMLVideoElement)=>{
    const now=performance.now();
    if(!probeContext||now-sampled<400)return;
    sampled=now;probeContext.drawImage(video,0,0,probeWidth,probeHeight);
    const pixels=probeContext.getImageData(0,0,probeWidth,probeHeight).data;
    let changed=0;for(let i=0;signature&&i<pixels.length;i+=4)if(Math.abs(pixels[i]!-signature[i]!)+Math.abs(pixels[i+1]!-signature[i+1]!)+Math.abs(pixels[i+2]!-signature[i+2]!)>45)changed++;
    if(!signature||changed>2){lastMotion=now;clearDetail();}
    signature=pixels;
  };
  const stillTimer=setInterval(()=>{
    const now=performance.now();
    // Even a tiny notification below the motion detector's threshold must not
    // remain hidden behind a stale screenshot. Live video remains underneath.
    if(detail&&now-detailAt>=2000)clearDetail();
    if(disposed||!connectedAt||preference.resolution!=='auto'||!o.snapshot||!o.video||detail||stillBusy||now-Math.max(lastInteraction,lastMotion)<5000||now-lastStillAttempt<10000)return;
    stillBusy=true;lastStillAttempt=now;const version=stillVersion;stillAbort=new AbortController();
    const deadline=setTimeout(()=>stillAbort?.abort(),6000);
    void o.snapshot(720,stillAbort.signal).then(createImageBitmap).then(image=>{
      try {
        if(disposed||version!==stillVersion||!connectedAt)return;
        // Never cover a changed or interacted-with screen with a late still.
        o.canvas.width=image.width;o.canvas.height=image.height;o.canvas.getContext('2d')?.drawImage(image,0,0);
        detail=true;detailAt=performance.now();o.detail?.(true);o.frame(image.width,image.height);
      } finally {image.close();}
    }).catch(()=>{}).finally(()=>{clearTimeout(deadline);stillBusy=false;});
  },500);
  const fallback = () => { if (!disposed && !socket) socket = startSocketVideo(o); };
  const applyProfile = () => {
    if(disposed)return;
    if(o.interactionBusy?.() || performance.now()-lastInteraction<500) {
      retry=setTimeout(applyProfile,500);return;
    }
    clearDetail();rtc?.();rtc=undefined;connectedAt=0;upgrading=false;void upgrade();
  };
  const upgrade = async () => {
    if (disposed || upgrading || connectedAt || o.webrtc === false || typeof RTCPeerConnection === 'undefined') return;
    upgrading = true;
    let iceServers = o.iceServers;
    if (o.loadIceServers) {
      try { iceServers = await o.loadIceServers(); }
      catch { if (!disposed) o.log('中转配置暂不可用，继续尝试直连'); }
    }
    if (disposed) return;
    const profile=policy.profile(preference);o.profile?.(profile);
    rtc = startRtcVideo({ url: o.url, iceServers, video: o.video, stats: o.stats, inputChannel: o.inputChannel, profile,displaySize:o.displaySize,
      network: sample=>{
        if(preference.resolution!=='auto'||!policy.sample(sample,performance.now())||disposed)return;
        // Apply only between gestures; new peer gets fresh timestamps/IDR.
        clearTimeout(retry);retry=setTimeout(applyProfile,500);
      },
      ready: () => { if (disposed) return; upgrading = false; connectedAt = Date.now(); socket?.(); socket = undefined; o.mode('webrtc'); o.log('已切换 WebRTC 视频'); },
      frame: video => {
        if (disposed) return;
        inspectFrame(video);
        const { videoWidth: w, videoHeight: h } = video;
        if (!detail&&(o.canvas.width !== w || o.canvas.height !== h)) { o.canvas.width = w; o.canvas.height = h; }
        // Let the browser composite RTC directly. No JS readback/copy is needed
        // to make each frame visible; the transparent canvas still maps input.
        if (!o.video) o.canvas.getContext('2d', { alpha: false })!.drawImage(video, 0, 0);
        if(!detail)o.frame(w, h);
      },
      failed: reason => {
        if (disposed) return;
        clearDetail();
        if (connectedAt && Date.now() - connectedAt > 15000) attempts = 0;
        upgrading = false; connectedAt = 0; o.stats?.({ rttMs: null, path: 'unknown', decoded: 0, dropped: 0 });
        o.log(`WebRTC：${reason}，保留兼容传屏并稍后重试`); fallback();
        retry = setTimeout(upgrade, [1000, 2000, 3000, 5000][Math.min(attempts++, 3)]);
      }
    });
  };
  // Negotiate RTC first, rather than starting two encoders on every reconnect.
  // JPEG only starts after RTC fails (or when RTC is explicitly unavailable).
  if (o.webrtc === false || typeof RTCPeerConnection === 'undefined') fallback();
  else { o.mode('webrtc'); o.state('connecting'); upgrade(); }
  return Object.assign(() => { disposed = true; clearTimeout(retry);clearInterval(stillTimer);clearDetail(); rtc?.(); socket?.(); }, {
    interact,
    // A fresh phone command response is evidence that the tunnel is back. Don't
    // wait out an unrelated video backoff; keep any working fallback meanwhile.
    recover: () => { if (!disposed && !connectedAt && !upgrading) { clearTimeout(retry); attempts = 0; void upgrade(); } }
  });
}

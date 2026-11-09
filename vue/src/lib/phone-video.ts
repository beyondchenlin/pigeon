/** FIRERPA video transport. No device configuration is changed by this client. */
export type VideoMode = 'webrtc' | 'h264' | 'mjpeg';
type Cancel = () => void;
export type VideoOptions = {
  url: (path: string) => string;
  canvas: HTMLCanvasElement;
  decoderUrl: string;
  iceServers?: RTCIceServer[];
  webrtc?: boolean;
  frame: (width: number, height: number) => void;
  state: (state: string) => void;
  mode: (mode: VideoMode) => void;
  log: (message: string) => void;
  stats?: (value: { rttMs: number | null; path: string; decoded: number; dropped: number }) => void;
};

type RtcOptions = Pick<VideoOptions, 'url' | 'iceServers'> & {
  frame: (video: HTMLVideoElement) => void;
  ready: () => void;
  failed: (reason: string) => void;
  stats?: VideoOptions['stats'];
};

/** One RTC attempt; its cleanup also invalidates every outstanding async callback. */
export function startRtcVideo(o: RtcOptions): Cancel {
  let closed = false, ready = false, remoteReady = false, offered = false;
  let pc: RTCPeerConnection | undefined, ws: WebSocket | undefined;
  let video: HTMLVideoElement | undefined, frameId = 0, raf = 0, previousTime = -1;
  let first: ReturnType<typeof setTimeout> | undefined, disconnected: ReturnType<typeof setTimeout> | undefined;
  let ping: ReturnType<typeof setInterval> | undefined, statsTimer: ReturnType<typeof setInterval> | undefined;
  let statsBusy = false, incoming = Promise.resolve();
  const remoteCandidates: (RTCIceCandidateInit | null)[] = [];
  const localCandidates: (RTCIceCandidateInit | null)[] = [];
  const close = () => {
    if (closed) return; closed = true;
    clearTimeout(first); clearTimeout(disconnected); clearInterval(ping); clearInterval(statsTimer);
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
  function render() {
    if (closed || !video) return;
    if (video.readyState >= 2 && video.videoWidth && video.videoHeight && video.currentTime !== previousTime) {
      previousTime = video.currentTime;
      if (!ready) { ready = true; clearTimeout(first); o.ready(); }
      if (closed) return;
      try { o.frame(video); } catch { fail('视频绘制失败'); return; }
    }
    if (video.requestVideoFrameCallback) frameId = video.requestVideoFrameCallback(render);
    else raf = requestAnimationFrame(render);
  }
  try {
    const iceServers = o.iceServers ?? [{ urls: 'stun:stun.device-farm.com' }];
    pc = new RTCPeerConnection({ iceServers });
    video = document.createElement('video'); video.autoplay = true; video.muted = true; video.playsInline = true;
    pc.ontrack = e => {
      if (closed || !video || e.track.kind !== 'video') return;
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
      send('request-offer', { type: offer.type, sdp: offer.sdp, servers: iceServers, display: 0, backend: 1, scale: 0.5, quality: 25, fps: 15 });
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
      }).catch(() => {}).finally(() => { statsBusy = false; });
    }, 2000);
    render();
  } catch { fail('浏览器无法建立 WebRTC'); }
  return close;
}

/** Bounded fallback decoder. A video restart never restarts the touch channel. */
function startSocketVideo(o: VideoOptions): Cancel {
  let disposed = false, cancel: Cancel = () => {}, retry: ReturnType<typeof setTimeout> | undefined;
  let codecFailures = 0, networkFailures = 0;
  function connect() {
    if (disposed) return; cancel();
    let closed = false, ws: WebSocket | undefined, worker: Worker | undefined, workerUrl: string | undefined;
    let check: ReturnType<typeof setInterval> | undefined, pending = 0, oldest = 0, frames = 0;
    let decoding = false, latest: Blob | null = null, lastProgress = Date.now();
    const mode = codecFailures >= 2 ? 'mjpeg' : 'h264';
    o.mode(mode); o.state('connecting');
    cancel = () => {
      if (closed) return; closed = true; latest = null; clearInterval(check); worker?.terminate(); if (workerUrl) URL.revokeObjectURL(workerUrl);
      if (ws) { ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null; ws.close(); }
    };
    const recover = (reason: string, codec = false) => {
      if (closed || disposed) return; cancel(); if (codec) codecFailures++;
      const wait = Math.min(8000, 300 * 2 ** Math.min(networkFailures++, 5));
      o.state('reconnecting'); o.log(`${reason}，正在恢复画面`); retry = setTimeout(connect, wait);
    };
    const drawn = (w: number, h: number) => { frames++; lastProgress = Date.now(); if (frames >= 15) networkFailures = 0; o.frame(w, h); };
    const context = o.canvas.getContext('2d', { alpha: false });
    if (!context) { o.log('浏览器无法绘制画面'); return; }
    const size = (w: number, h: number) => { if (o.canvas.width !== w || o.canvas.height !== h) { o.canvas.width = w; o.canvas.height = h; } };
    try {
      if (mode === 'h264') {
        const source = `var window=self;self.window=self;var document=undefined;importScripts(${JSON.stringify(o.decoderUrl)});var decoder=new H264Decoder();onmessage=function(e){try{if(decoder.decode(new Uint8Array(e.data))===H264Decoder.PIC_RDY){var w=decoder.width,h=decoder.height,yuv=decoder.pic,out=new Uint8ClampedArray(w*h*4),uw=w>>1,uo=w*h,vo=uo+uw*(h>>1);for(var j=0,p=0;j<h;j++)for(var i=0;i<w;i++){var y=yuv[j*w+i],uv=(j>>1)*uw+(i>>1),u=yuv[uo+uv]-128,v=yuv[vo+uv]-128;out[p++]=y+1.402*v;out[p++]=y-.344136*u-.714136*v;out[p++]=y+1.772*u;out[p++]=255;}postMessage({w:w,h:h,rgba:out.buffer},[out.buffer]);}postMessage({done:true});}catch(e){postMessage({error:true});}};`;
        workerUrl = URL.createObjectURL(new Blob([source], { type: 'application/javascript' })); worker = new Worker(workerUrl);
        worker.onerror = () => recover('H.264 解码器不可用', true);
        worker.onmessage = e => {
          if (closed) return; const d = e.data;
          if (d.error) { recover('H.264 解码失败', true); return; }
          if (d.done) { pending = Math.max(0, pending - 1); if (!pending) oldest = 0; return; }
          size(d.w, d.h); context.putImageData(new ImageData(new Uint8ClampedArray(d.rgba), d.w, d.h), 0, 0); drawn(d.w, d.h);
        };
      }
      ws = new WebSocket(o.url(`/ws/screen/25@${mode === 'h264' ? 15 : 8}/live?type=${mode}&backend=${mode === 'h264' ? 1 : 0}&scale=0.5&display=0`));
      ws.binaryType = mode === 'h264' ? 'arraybuffer' : 'blob';
      ws.onmessage = async e => {
        if (closed) return;
        if (worker && e.data instanceof ArrayBuffer) {
          if (!pending) oldest = Date.now();
          // Bound both burst memory and queue age, without discarding dependent P frames.
          if (++pending > 24 || Date.now() - oldest > 500) { recover('视频队列积压'); return; }
          worker.postMessage(e.data, [e.data]); return;
        }
        if (!(e.data instanceof Blob)) return; latest = e.data; if (decoding) return; decoding = true;
        try {
          while (latest && !closed) {
            const b = latest; latest = null; const bitmap = await createImageBitmap(b);
            try { if (!closed) { size(bitmap.width, bitmap.height); context.drawImage(bitmap, 0, 0); drawn(bitmap.width, bitmap.height); } } finally { bitmap.close(); }
          }
        } catch { recover('兼容画面解码失败'); } finally { decoding = false; }
      };
      ws.onclose = ws.onerror = () => recover('视频连接中断');
      check = setInterval(() => {
        if (!frames && Date.now() - lastProgress > 12000) recover('视频首帧等待超时');
        else if (pending && Date.now() - oldest > 1000) recover('解码队列超时');
        // A connected, unchanged screen is not a broken video stream.
      }, 1000);
    } catch { recover('视频初始化失败', true); }
  }
  connect();
  return () => { disposed = true; clearTimeout(retry); cancel(); };
}

export function startPhoneVideo(o: VideoOptions): Cancel {
  let disposed = false, socket: Cancel | undefined, rtc: Cancel | undefined;
  let retry: ReturnType<typeof setTimeout> | undefined, attempts = 0, connectedAt = 0;
  const fallback = () => { if (!disposed && !socket) socket = startSocketVideo(o); };
  const upgrade = () => {
    if (disposed || o.webrtc === false || typeof RTCPeerConnection === 'undefined') return;
    rtc = startRtcVideo({ url: o.url, iceServers: o.iceServers, stats: o.stats,
      ready: () => { if (disposed) return; connectedAt = Date.now(); socket?.(); socket = undefined; o.mode('webrtc'); o.log('已切换 WebRTC 视频'); },
      frame: video => {
        if (disposed) return;
        const { videoWidth: w, videoHeight: h } = video;
        if (o.canvas.width !== w || o.canvas.height !== h) { o.canvas.width = w; o.canvas.height = h; }
        o.canvas.getContext('2d', { alpha: false })!.drawImage(video, 0, 0); o.frame(w, h);
      },
      failed: reason => {
        if (disposed) return;
        if (connectedAt && Date.now() - connectedAt > 15000) attempts = 0;
        connectedAt = 0; o.stats?.({ rttMs: null, path: 'unknown', decoded: 0, dropped: 0 });
        o.log(`WebRTC：${reason}，保留兼容传屏并稍后重试`); fallback();
        retry = setTimeout(upgrade, [2000, 5000, 15000, 30000][Math.min(attempts++, 3)]);
      }
    });
  };
  fallback(); upgrade();
  return () => { disposed = true; clearTimeout(retry); rtc?.(); socket?.(); };
}

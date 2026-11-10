/** Detect a media stall without treating an unchanged screen as a failure. */
export class VideoHealth {
  lastFrameAt = 0;
  private decoded: number | undefined;
  private packets: number | undefined;
  private packetsSinceFrame = 0;
  private lastPacketAt = 0;
  private checkedInteraction = -Infinity;

  frame(now: number) { this.lastFrameAt = now; this.packetsSinceFrame = 0; }

  sample(now: number, decoded: number, packets: number, interaction: number): string | null {
    const advanced = this.decoded !== undefined && decoded > this.decoded;
    if (advanced) this.frame(now);
    if (this.packets !== undefined && packets > this.packets) {
      this.packetsSinceFrame += packets - this.packets;
      this.lastPacketAt = now;
    }
    this.decoded = decoded; this.packets = packets;
    // A static screen may legitimately send no frames. Probe once after input,
    // or when RTP keeps arriving but decoding has stopped; never on idle alone.
    if (interaction > this.checkedInteraction && interaction > this.lastFrameAt && now - interaction >= 1500) {
      this.checkedInteraction = interaction;
      return '操作后视频未更新';
    }
    if (this.packetsSinceFrame >= 2 && now - this.lastFrameAt >= 3000 && now - this.lastPacketAt < 3000) {
      return '视频仍在收包但未解码出帧';
    }
    return null;
  }
}

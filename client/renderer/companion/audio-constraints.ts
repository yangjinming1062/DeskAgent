// 麦克风采样约束：单声道 16 kHz、AGC/EC/NS 全开。
// 由 IM 语音条（use-voice-recorder.ts）使用。
export const VOICE_CALL_AUDIO_CONSTRAINTS: MediaTrackConstraints = {
  autoGainControl: true,
  channelCount: 1,
  echoCancellation: true,
  noiseSuppression: true,
  sampleRate: 16000
}

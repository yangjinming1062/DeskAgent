export function portraitIntroHint(mode: 'single' | 'multi' = 'single'): string {
  return mode === 'multi'
    ? '形象生成有四张图：先确认半身头像，再依次生成正面、右侧面、背面全身立绘。每一张都可以单独反馈意见。'
    : '形象生成有两张图：先确认半身头像，再生成正面全身立绘。每张都可以单独反馈意见。'
}

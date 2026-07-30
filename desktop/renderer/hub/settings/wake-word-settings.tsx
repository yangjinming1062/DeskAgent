import { IconVolume } from '@tabler/icons-react'
import { useState } from 'react'

import { ListRow, SettingsContent, SettingsSubsection } from './primitives'

export function WakeWordSettings() {
  const [enabled, setEnabled] = useState(false)
  const [wakeWord, setWakeWord] = useState('Hey DeskAgent')
  const [subtitles, setSubtitles] = useState(true)
  const [gainThreshold, setGainThreshold] = useState(35)

  return (
    <SettingsContent>
      <div className="space-y-6">
        <SettingsSubsection icon={IconVolume} title="语音唤醒与双向字幕">
          <div className="rounded-lg border bg-card p-4 space-y-4">
            <ListRow
              title="开启语音唤醒"
              description="无需点击即可通过唤醒词在本地唤起伙伴"
              action={
                <input
                  type="checkbox"
                  className="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary cursor-pointer"
                  checked={enabled}
                  onChange={e => setEnabled(e.target.checked)}
                />
              }
            />

            {enabled && (
              <div className="space-y-3 border-t pt-3">
                <ListRow
                  title="唤醒词"
                  description="选择用于唤醒伙伴的关键词"
                  action={
                    <select
                      className="rounded-md border bg-background px-2.5 py-1 text-xs outline-none"
                      value={wakeWord}
                      onChange={e => setWakeWord(e.target.value)}
                    >
                      <option value="Hey DeskAgent">Hey DeskAgent</option>
                      <option value="小伙伴">小伙伴</option>
                      <option value="小精灵">小精灵</option>
                    </select>
                  }
                />

                <div className="rounded-lg bg-amber-500/10 border border-amber-500/20 p-3 text-xs text-amber-300">
                  🔒 <strong>隐私声明</strong>：语音唤醒功能仅在本地进行短关键词匹配，未匹配成功前绝不会将声音数据上传云端。
                </div>
              </div>
            )}

            <div className="border-t pt-3">
              <ListRow
                title="实时双向字幕"
                description="在通话模式下浮现显示双向对话字幕"
                action={
                  <input
                    type="checkbox"
                    className="h-4 w-4 rounded border-gray-300 text-primary focus:ring-primary cursor-pointer"
                    checked={subtitles}
                    onChange={e => setSubtitles(e.target.checked)}
                  />
                }
              />
            </div>

            <div className="border-t pt-3 space-y-2">
              <div className="flex justify-between text-xs font-medium">
                <span>打断灵敏度 (Barge-in Threshold)</span>
                <span>{gainThreshold}</span>
              </div>
              <input
                type="range"
                min="10"
                max="80"
                value={gainThreshold}
                onChange={e => setGainThreshold(Number(e.target.value))}
                className="w-full h-1.5 bg-secondary rounded-lg appearance-none cursor-pointer"
              />
            </div>
          </div>
        </SettingsSubsection>
      </div>
    </SettingsContent>
  )
}


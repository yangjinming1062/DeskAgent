import { useStore } from '@nanostores/react'
import type React from 'react'
import { useEffect, useState } from 'react'

import { $systemPresetsFetched, fetchSystemPresets } from '@/chat/session-list-store'
import { Cpu, Globe, type IconComponent, List, MessageCircle, Pencil, Sparkles } from '@/shared/lib/icons'
import { BTN_PRIMARY, BTN_SUBTLE, WizardModal } from '@/shared/panel'
import { strings } from '@/shared/strings'
import type { SystemPresetSummary } from '@/shared/types/spiritagent'

// icon_key → Tabler 图标映射。新增预设须同步：(1) BUILTIN_PRESETS icon_key，(2) 此映射表。
const PRESET_ICONS: Record<string, IconComponent> = {
  preset_companion: Sparkles,
  preset_developer: Cpu,
  preset_product_manager: List,
  preset_copywriter: Pencil,
  preset_language_teacher: Globe
}

function PresetIcon({ iconKey }: { iconKey: string }): React.JSX.Element {
  const Icon: IconComponent = PRESET_ICONS[iconKey] ?? MessageCircle

  return <Icon className="size-4" />
}

interface PresetPickerModalProps {
  presets: SystemPresetSummary[]
  loading: boolean
  onConfirm: (presetId: string) => void
  onClose: () => void
}

// 用户决策要求「无默认、确认按钮必选中才启用」，避免误选 companion。
export function PresetPickerModal({ presets, loading, onConfirm, onClose }: PresetPickerModalProps): React.JSX.Element {
  const [selectedId, setSelectedId] = useState<string>('')
  const fetched = useStore($systemPresetsFetched)

  useEffect(() => {
    if (!fetched) {
      void fetchSystemPresets()
    }
  }, [fetched])

  const canSubmit = selectedId !== '' && !loading && presets.length > 0

  return (
    <WizardModal
      footer={
        <>
          <button className={BTN_SUBTLE} onClick={onClose} type="button">
            {strings.chat.presetPicker.cancel}
          </button>
          <button
            className={BTN_PRIMARY}
            disabled={!canSubmit}
            onClick={() => {
              if (canSubmit) {
                onConfirm(selectedId)
              }
            }}
            title={!canSubmit ? strings.chat.presetPicker.pickOne : undefined}
            type="button"
          >
            {strings.chat.presetPicker.confirm}
          </button>
        </>
      }
      onClose={onClose}
      regionId="preset-picker"
      title={strings.chat.presetPicker.title}
      widthClass="max-w-lg"
    >
      <p className="mb-3 text-[11px] leading-relaxed text-muted">{strings.chat.presetPicker.intro}</p>
      <div className="space-y-2">
        {loading || (!fetched && presets.length === 0) ? (
          <div className="py-6 text-center text-xs text-faint">{strings.common.loading}</div>
        ) : presets.length === 0 ? (
          <div className="py-6 text-center text-xs text-faint">{strings.chat.presetPicker.fetchFailed}</div>
        ) : (
          presets.map(p => {
            const selected = selectedId === p.id

            return (
              <button
                aria-pressed={selected}
                className={`flex w-full items-start gap-3 rounded-xl border px-3 py-2.5 text-left transition ${
                  selected
                    ? 'border-accent-line bg-accent-soft'
                    : 'border-line-standard hover:border-line-strong hover:bg-fill-faint'
                }`}
                key={p.id}
                onClick={() => setSelectedId(p.id)}
                type="button"
              >
                <span
                  className={`mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg ${
                    selected ? 'bg-fill-hover text-strong' : 'bg-fill-faint text-muted'
                  }`}
                >
                  <PresetIcon iconKey={p.icon_key} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-xs font-medium text-strong">{p.name}</span>
                  <span className="mt-0.5 block text-[11px] leading-relaxed text-muted line-clamp-2">
                    {p.description}
                  </span>
                </span>
              </button>
            )
          })
        )}
      </div>
    </WizardModal>
  )
}

export function PresetIconBadge({ iconKey }: { iconKey: string | null | undefined }): React.JSX.Element {
  const Icon: IconComponent = (iconKey && PRESET_ICONS[iconKey]) || MessageCircle

  return <Icon className="size-3.5 shrink-0 text-accent/70" />
}

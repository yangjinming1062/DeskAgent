import type React from 'react'
import { useCallback, useRef, useState } from 'react'

import { AudioLines, Brain, Info, Keyboard, Palette } from '@/shared/lib/icons'
import { cn } from '@/shared/lib/utils'
import { strings } from '@/shared/strings'

import { AboutSettings } from '../settings/about-settings'
import { AppearanceSettings } from '../settings/appearance-settings'
import { InferenceSettings } from '../settings/inference-settings'
import { ShortcutsSettings } from '../settings/shortcuts-settings'
import { SpeechSettings } from '../settings/speech-settings'

interface NavSection {
  id: string
  label: string
  icon: React.ComponentType<{ className?: string }>
}

// 应用设置单页平铺面板：汇集基础配置（外观、语音、推理与对话、快捷键、关于），
// 支持顶部锚点快速定位与平滑滚动，消除多重侧边栏割裂感。
export function AppSettingsPanel(): React.JSX.Element {
  const t = strings
  const containerRef = useRef<HTMLDivElement>(null)
  const [activeId, setActiveId] = useState<string>('appearance')

  const navSections: NavSection[] = [
    { id: 'appearance', label: t.settings.nav.appearance, icon: Palette },
    { id: 'speech', label: t.speech.title, icon: AudioLines },
    { id: 'inference', label: t.settings.nav.inference, icon: Brain },
    { id: 'shortcuts', label: t.settings.nav.shortcuts, icon: Keyboard },
    { id: 'about', label: t.settings.nav.about, icon: Info }
  ]

  const scrollToSection = useCallback((id: string) => {
    setActiveId(id)
    const el = containerRef.current?.querySelector(`#setting-section-${id}`)

    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [])

  const handleScroll = useCallback(() => {
    const el = containerRef.current

    if (!el) {
      return
    }

    // 触底时自动高亮末尾的"关于"
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 24) {
      setActiveId('about')

      return
    }

    // 根据顶部偏移高亮当前可见分区
    const containerTop = el.getBoundingClientRect().top

    for (const section of navSections) {
      const sectionEl = el.querySelector(`#setting-section-${section.id}`)

      if (sectionEl) {
        const rect = sectionEl.getBoundingClientRect()

        if (rect.top - containerTop <= 80 && rect.bottom - containerTop > 80) {
          setActiveId(section.id)

          break
        }
      }
    }
  }, [navSections])

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden bg-surface-panel/20">
      {/* 顶部固定导航栏：标题与各分类锚点跳转 */}
      <header className="sticky top-0 z-10 flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-line-hairline bg-surface-chrome/80 px-6 py-3 backdrop-blur-md">
        <h1 className="text-sm font-semibold tracking-tight text-strong">{t.settings.title}</h1>
        <nav aria-label="设置分区导航" className="flex items-center gap-1 overflow-x-auto">
          {navSections.map(section => {
            const Icon = section.icon
            const isActive = activeId === section.id

            return (
              <button
                className={cn(
                  'flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium transition select-none',
                  isActive ? 'bg-accent-soft text-accent shadow-xs' : 'text-faint hover:bg-fill-hover hover:text-strong'
                )}
                key={section.id}
                onClick={() => scrollToSection(section.id)}
                type="button"
              >
                <Icon className="size-3.5 shrink-0" />
                <span>{section.label}</span>
              </button>
            )
          })}
        </nav>
      </header>

      {/* 单页平铺滚动内容区 */}
      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6" onScroll={handleScroll} ref={containerRef}>
        <div className="mx-auto flex max-w-3xl flex-col space-y-10 pb-16">
          <section className="scroll-mt-4" id="setting-section-appearance">
            <AppearanceSettings standalone={false} />
          </section>

          <div className="h-px bg-line-hairline" />

          <section className="scroll-mt-4" id="setting-section-speech">
            <SpeechSettings standalone={false} />
          </section>

          <div className="h-px bg-line-hairline" />

          <section className="scroll-mt-4" id="setting-section-inference">
            <InferenceSettings standalone={false} />
          </section>

          <div className="h-px bg-line-hairline" />

          <section className="scroll-mt-4" id="setting-section-shortcuts">
            <ShortcutsSettings standalone={false} />
          </section>

          <div className="h-px bg-line-hairline" />

          <section className="scroll-mt-4" id="setting-section-about">
            <AboutSettings standalone={false} />
          </section>
        </div>
      </div>
    </div>
  )
}

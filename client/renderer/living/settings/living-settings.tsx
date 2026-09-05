// 生活空间「设置」长页：单页平铺角色/音色/交互/主题/语音/快捷键/关于，
// 与 AppSettingsPanel 同形态——顶部锚点导航 + 滚动正文 + 触底高亮「关于」。
//
// 推理与对话已搬至工作台工位抽屉，本页不再收纳。

import { useStore } from '@nanostores/react'
import type React from 'react'
import { Fragment, useLayoutEffect, useRef } from 'react'

import { PAGE_INSET_X } from '@/shared/layout/page-inset'
import { cn } from '@/shared/lib/utils'
import { strings } from '@/shared/strings'

import {
  $livingSettingsSection,
  LIVING_SETTINGS_SECTIONS,
  type LivingSettingsSection,
  setLivingSettingsSection
} from '../living-store'

import { AboutPage } from './about-page'
import { InteractionPage } from './interaction-page'
import { PersonaPage } from './persona-page'
import { ShortcutsPage } from './shortcuts-page'
import { SpeechPage } from './speech-page'
import { ThemePage } from './theme-page'
import { VoicePage } from './voice-page'

// 单一来源：加新段需要同步 LIVING_SETTINGS_SECTIONS、这里、Page 注册三处，
// SectionPage 类型用强类型把缺注册挡在编译期。
type SectionPage = () => React.JSX.Element

const SECTION_PAGES: Record<LivingSettingsSection, SectionPage> = {
  about: AboutPage,
  interaction: InteractionPage,
  persona: PersonaPage,
  shortcuts: ShortcutsPage,
  speech: SpeechPage,
  theme: ThemePage,
  voice: VoicePage
}

const SECTION_LABELS: Record<LivingSettingsSection, string> = {
  about: strings.settings.nav.about,
  interaction: '交互',
  persona: '角色与记忆',
  shortcuts: strings.settings.nav.shortcuts,
  speech: strings.speech.title,
  theme: '主题',
  voice: '音色'
}

const NAV_SECTIONS: ReadonlyArray<{ id: LivingSettingsSection; label: string }> = LIVING_SETTINGS_SECTIONS.map(id => ({
  id,
  label: SECTION_LABELS[id]
}))

const SECTION_ID_PREFIX = 'living-setting-'

export function LivingSettings(): React.JSX.Element {
  const section = useStore($livingSettingsSection)
  const containerRef = useRef<HTMLDivElement>(null)
  const sectionElsRef = useRef<Map<LivingSettingsSection, HTMLElement> | null>(null)
  const isUserScrollingRef = useRef(false)
  const scrollTargetRef = useRef<LivingSettingsSection | null>(null)
  const scrollTimeoutRef = useRef<number | null>(null)

  // 缓存 section 元素引用，避免每次 scroll 触发 querySelector + getBoundingClientRect。
  // 一次挂载后挂载顺序固定，不会动态增减。
  const getSectionEls = (): Map<LivingSettingsSection, HTMLElement> => {
    let map = sectionElsRef.current

    if (!map) {
      map = new Map()
      const root = containerRef.current

      if (root) {
        for (const id of LIVING_SETTINGS_SECTIONS) {
          const el = root.querySelector<HTMLElement>(`#${SECTION_ID_PREFIX}${id}`)

          if (el) {
            map.set(id, el)
          }
        }
      }

      sectionElsRef.current = map
    }

    return map
  }

  // 挂载定位与外部深链时滚入；用户滚动时不重复拉扯。
  useLayoutEffect(() => {
    if (isUserScrollingRef.current) {
      isUserScrollingRef.current = false

      return
    }

    containerRef.current?.querySelector(`#${SECTION_ID_PREFIX}${section}`)?.scrollIntoView({ block: 'start' })
  }, [section])

  const scrollToSection = (target: LivingSettingsSection): void => {
    if (scrollTimeoutRef.current) {
      window.clearTimeout(scrollTimeoutRef.current)
    }

    scrollTargetRef.current = target
    setLivingSettingsSection(target)

    containerRef.current?.querySelector(`#${SECTION_ID_PREFIX}${target}`)?.scrollIntoView({
      behavior: 'smooth',
      block: 'start'
    })

    scrollTimeoutRef.current = window.setTimeout(() => {
      scrollTargetRef.current = null
    }, 800)
  }

  const handleScroll = (): void => {
    const el = containerRef.current

    if (!el) {
      return
    }

    // 平滑滚动前往特定目标期间，不被中途滚过的分区覆盖目标
    if (scrollTargetRef.current) {
      return
    }

    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 24) {
      if ($livingSettingsSection.get() !== 'about') {
        isUserScrollingRef.current = true
        setLivingSettingsSection('about')
      }

      return
    }

    const containerTop = el.getBoundingClientRect().top
    const map = getSectionEls()

    for (const id of LIVING_SETTINGS_SECTIONS) {
      const sectionEl = map.get(id)

      if (!sectionEl) {
        continue
      }

      const rect = sectionEl.getBoundingClientRect()

      if (rect.top - containerTop <= 80 && rect.bottom - containerTop > 80) {
        if ($livingSettingsSection.get() !== id) {
          isUserScrollingRef.current = true
          setLivingSettingsSection(id)
        }

        break
      }
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden bg-surface-panel/20">
      <header className="sticky top-0 z-10 flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-line-hairline bg-surface-chrome/80 px-6 py-3 backdrop-blur-md">
        <h1 className="text-sm font-semibold tracking-tight text-strong">{strings.settings.title}</h1>
        <nav aria-label="设置分区导航" className="flex items-center gap-1 overflow-x-auto">
          {NAV_SECTIONS.map(nav => (
            <button
              className={cn(
                'flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium transition select-none',
                section === nav.id
                  ? 'bg-accent-soft text-accent shadow-xs'
                  : 'text-faint hover:bg-fill-hover hover:text-strong'
              )}
              key={nav.id}
              onClick={() => scrollToSection(nav.id)}
              type="button"
            >
              <span>{nav.label}</span>
            </button>
          ))}
        </nav>
      </header>

      <div
        className={cn('min-h-0 flex-1 overflow-y-auto py-6', PAGE_INSET_X)}
        onScroll={handleScroll}
        ref={containerRef}
      >
        <div className="mx-auto flex max-w-3xl flex-col space-y-10 pb-16">
          {LIVING_SETTINGS_SECTIONS.map((id, i) => {
            const Page = SECTION_PAGES[id]

            return (
              <Fragment key={id}>
                {i > 0 && <div className="h-px bg-line-hairline" />}
                <section className="scroll-mt-4" id={`${SECTION_ID_PREFIX}${id}`}>
                  <Page />
                </section>
              </Fragment>
            )
          })}
        </div>
      </div>
    </div>
  )
}

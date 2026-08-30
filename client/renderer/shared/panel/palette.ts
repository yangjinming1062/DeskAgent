// 面板设计语言的类常量词汇表——两个窗口（精灵窗浮层 / 工具窗）共用的唯一视觉来源。
// 全部消费 styles.css 的语义 token（--ui-*），主题在 html[data-theme] 上换肤。
// 分层：大面板实体表面（阶梯 chrome→panel→card），瞬时浮层轻玻璃。

// 表面阶梯（实体档）
export const SURFACE_CHROME = 'bg-surface-chrome'

// 按钮
export const BTN_PRIMARY =
  'inline-flex h-8 items-center justify-center gap-1.5 rounded-lg bg-inverse-surface px-3.5 text-xs font-medium text-inverse-fg transition hover:bg-inverse-surface-hover disabled:pointer-events-none disabled:opacity-40'
export const BTN_SUBTLE =
  'inline-flex h-8 items-center justify-center gap-1.5 rounded-lg border border-line-standard bg-fill-faint px-3.5 text-xs font-medium text-body transition hover:bg-fill-hover hover:text-strong disabled:pointer-events-none disabled:opacity-40'
export const BTN_GHOST =
  'inline-flex h-7 items-center justify-center gap-1.5 rounded-lg px-2.5 text-xs font-medium text-muted transition hover:bg-fill-hover hover:text-strong disabled:pointer-events-none disabled:opacity-40'
export const BTN_DANGER =
  'inline-flex h-8 items-center justify-center gap-1.5 rounded-lg border border-danger-line bg-danger-bg px-3.5 text-xs font-medium text-danger-fg transition hover:bg-danger-bg-hover disabled:pointer-events-none disabled:opacity-40'
export const BTN_ICON =
  'inline-flex size-7 items-center justify-center rounded-lg text-muted transition hover:bg-fill-hover hover:text-strong disabled:pointer-events-none disabled:opacity-40 [&_svg]:size-4'

// 输入与选择
export const INPUT_CLASS =
  'w-full rounded-lg border border-line-standard bg-fill-faint px-3 py-2 text-xs text-strong outline-none placeholder:text-faint focus:border-focus-line'
export const CHIP =
  'inline-flex shrink-0 items-center whitespace-nowrap rounded-full border border-line-standard bg-fill-faint px-2.5 py-0.5 text-[11px] text-muted'
export const CHIP_ACTIVE =
  'inline-flex shrink-0 items-center whitespace-nowrap rounded-full border border-accent-line bg-accent-soft px-2.5 py-0.5 text-[11px] font-medium text-accent'
export const CHIP_FILTER =
  'inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2.5 py-0.5 text-[11px] transition bg-fill-faint text-muted hover:bg-fill-hover'
export const CHIP_FILTER_ACTIVE =
  'inline-flex shrink-0 items-center whitespace-nowrap rounded-full px-2.5 py-0.5 text-[11px] transition bg-fill-hover font-medium text-strong'

// 设置侧栏导航
export const NAV_ITEM =
  'group relative flex h-8 w-full items-center gap-2 rounded-lg px-2.5 text-left text-xs text-muted transition-all duration-150 hover:bg-fill-hover hover:text-strong'
export const NAV_ITEM_ACTIVE =
  'group relative flex h-8 w-full items-center gap-2 rounded-lg bg-accent-soft px-2.5 text-left text-xs font-medium text-accent shadow-[inset_0_0_0_1px_var(--ui-line-hairline)] before:absolute before:left-0 before:top-1.5 before:bottom-1.5 before:w-0.5 before:rounded-full before:bg-accent'

// 文本层级
export const SECTION_TITLE = 'text-xs font-medium text-body tracking-wide'
export const FIELD_LABEL = 'mb-1 block text-[11px] text-muted font-medium'
export const HINT_TEXT = 'text-[10px] leading-relaxed text-faint'

// 科技面板与卡片类
export const TECH_CARD =
  'relative overflow-hidden rounded-xl border border-line-hairline bg-surface-card transition-all duration-200 hover:border-line-strong'

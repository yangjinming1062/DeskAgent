import { type CSSProperties } from 'react'
import { Button } from '../components/button'
import { startInstall } from '../store'
import { ArrowRight } from 'lucide-react'

/*
 * Welcome screen.
 *
 * Mirrors the desktop's chat intro (desktop/src/components/chat/intro.tsx):
 *   - DESKAGENT AGENT wordmark rendered in Collapse Bold, uppercase, tracked
 *   - mix-blend-plus-lighter so the type "glows" on the canvas
 *   - fit-text utility so the wordmark sizes itself to the column
 *
 * No install-path footer. The default install location is correct for
 * 99% of users; the rest will use the CLI installer with a -DeskAgentHome
 * flag. Showing %LOCALAPPDATA% to grandma is developer-brain.
 */
export default function Welcome() {
  return (
    <div className="deskagent-fade-in flex h-full flex-col items-center justify-center gap-10 px-12 py-10">
      {/* Hero — same recipe the desktop's chat/intro.tsx uses */}
      <div className="w-full max-w-2xl min-w-0 text-center">
        <p
          className="fit-text mx-auto mb-4 w-full font-['Collapse'] font-bold uppercase leading-[0.9] tracking-[0.08em] text-midground mix-blend-plus-lighter dark:text-foreground/90"
          style={
            {
              '--fit-text-line-height': '0.9',
              '--fit-text-max': '6rem',
              '--fit-text-min': '2.5rem'
            } as CSSProperties
          }
        >
          <span>
            <span>DESKAGENT AGENT</span>
          </span>
          <span aria-hidden="true">DESKAGENT AGENT</span>
        </p>

        <p className="m-0 text-center text-base leading-normal tracking-tight text-muted-foreground">
          您的智能助手。我们将在后台完成设置 &mdash; 需要几分钟时间。
        </p>
      </div>

      <Button
        onClick={() => void startInstall()}
        size="lg"
        className="group inline-flex items-center gap-2 px-6"
      >
        安装 DeskAgent
        <ArrowRight
          size={18}
          className="transition-transform group-hover:translate-x-0.5"
        />
      </Button>
    </div>
  )
}

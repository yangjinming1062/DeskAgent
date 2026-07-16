// Renderer-facing catalog of toolsets displayed in Settings → Skills → Toolsets.
//
// Filter logic (`prefixes` / `extraTools` / MCP exclusion) lives in the two
// filter-side mirrors: `desktop/electron/lib/toolset-index.cjs` and
// `runner/tools/toolsets/catalog.py`. Both must be kept in lockstep with the
// `id`s declared here. Label / description / icon live here only; toolset
// filter data lives in the mirror files (see CLAUDE.md §"双侧目录同步").

import {
  Brain,
  Clock,
  Command,
  Cpu,
  Eye,
  FileText,
  Globe,
  type IconComponent,
  ImageIcon,
  Monitor,
  Search,
  Send,
  Sparkles,
  Terminal,
  Users,
  Volume2
} from '@/lib/icons'

export interface ToolsetCatalogEntry {
  id: string
  icon: IconComponent
}

export type ToolsetId = (typeof TOOLSET_CATALOG)[number]['id']

export const TOOLSET_CATALOG: readonly ToolsetCatalogEntry[] = [
  { id: 'browser_automation', icon: Globe },
  { id: 'file_operations', icon: FileText },
  { id: 'terminal', icon: Terminal },
  { id: 'code_execution', icon: Command },
  { id: 'process_management', icon: Cpu },
  { id: 'skills_system', icon: Sparkles },
  { id: 'memory', icon: Brain },
  { id: 'web_tools', icon: Search },
  { id: 'image_generation', icon: ImageIcon },
  { id: 'text_to_speech', icon: Volume2 },
  { id: 'messaging', icon: Send },
  { id: 'scheduled_tasks', icon: Clock },
  { id: 'agent_delegation', icon: Users },
  { id: 'computer_use', icon: Monitor },
  { id: 'media_analysis', icon: Eye }
] as const

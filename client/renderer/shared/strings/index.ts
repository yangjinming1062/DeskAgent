export const strings = {
  common: {
    apply: '应用',
    back: '返回',
    save: '保存',
    saving: '保存中…',
    cancel: '取消',
    change: '更改',
    choose: '选择',
    clear: '清除',
    close: '关闭',
    collapse: '收起',
    confirm: '确认',
    connect: '连接',
    connecting: '连接中',
    continue: '继续',
    copied: '已复制',
    copy: '复制',
    copyFailed: '复制失败',
    delete: '删除',
    docs: '文档',
    done: '完成',
    error: '错误',
    failed: '失败',
    free: '免费',
    loading: '加载中…',
    notSet: '未设置',
    refresh: '刷新',
    remove: '移除',
    replace: '替换',
    retry: '重试',
    run: '运行',
    send: '发送',
    set: '设置',
    skip: '跳过',
    update: '更新',
    on: '开',
    off: '关'
  },

  boot: {
    ready: 'SpiritAgent 桌面版已就绪',
    desktopBootFailedWithMessage: (message: string) => `桌面启动失败：${message}`,
    steps: {
      connectingGateway: '正在连接桌面网关',
      loadingSettings: '正在加载 SpiritAgent 设置',
      loadingSessions: '正在加载最近会话',
      startingDesktopConnection: '正在启动桌面连接',
      startingSpiritAgentDesktop: '正在启动 SpiritAgent 桌面版…'
    },
    errors: {
      backgroundExited: 'SpiritAgent 后台进程已退出。',
      backgroundExitedDuringStartup: 'SpiritAgent 后台进程在启动期间退出。',
      backendStopped: '后端已停止',
      desktopBootFailed: '桌面启动失败',
      gatewaySignInRequired: '需要登录网关',
      ipcBridgeUnavailable: '桌面 IPC 桥不可用。'
    },
    failure: {
      title: 'SpiritAgent 无法启动',
      description: '后台网关没有启动。请尝试下面的恢复步骤；这里不会删除你的对话或设置。',
      retry: '重试',
      openLogs: '打开日志',
      retryHint: '重新加载会重连到云端 Backend。打开日志可查看失败原因。',
      hideRecentLogs: '隐藏最近日志',
      showRecentLogs: '显示最近日志'
    }
  },

  notifications: {
    region: '通知',
    hide: '隐藏',
    show: '显示',
    more: (count: number) => `另外 ${count} 条通知`,
    clearAll: '全部清除',
    dismiss: '关闭通知',
    details: '详情',
    copyDetail: '复制详情',
    copyDetailFailed: '无法复制通知详情',
    errors: {
      elevenLabsNeedsKey: 'ElevenLabs STT 需要 ELEVENLABS_API_KEY。',
      elevenLabsRejectedKey: 'ElevenLabs 拒绝了该 API key (401)。',
      methodNotAllowed: '桌面后端拒绝了该请求 (405 Method Not Allowed)。请尝试重启 SpiritAgent Desktop。',
      microphonePermission: '麦克风权限已被拒绝。',
      openaiRejectedApiKey: 'OpenAI 拒绝了该 API key。',
      openaiRejectedApiKeyWithStatus: (status: number | string) =>
        `OpenAI 拒绝了该 API key (${status} invalid_api_key)。`,
      openaiTtsNeedsKey: 'OpenAI TTS 需要 VOICE_TOOLS_OPENAI_KEY 或 OPENAI_API_KEY。'
    },
    voice: {
      invalidTitle: '音色已失效',
      invalidMessage: (name: string) =>
        `你之前选的音色「${name}」已不在当前目录，已临时用默认音色，去伙伴设置里重新挑一个吧～`,
      invalidAction: '去设置'
    }
  },

  settings: {
    closeSettings: '关闭设置',
    exportConfig: '导出配置',
    importConfig: '导入配置',
    resetToDefaults: '恢复默认',
    resetConfirm: '将所有设置恢复为 SpiritAgent 默认值？',
    exportFailed: '导出失败',
    importFailed: '导入失败',
    resetFailed: '重置失败',
    nav: {
      account: '账户',
      about: '关于',
      runner: '执行器',
      skills: '技能与工具',
      voices: '音色目录'
    },
    about: {
      heading: 'SpiritAgent Desktop',
      version: (value: number | string) => `版本 ${value}`,
      versionUnavailable: '版本不可用',
      checkForUpdates: '检查更新',
      checking: '检查中…',
      upToDate: '已是最新版本',
      upToDateWithVersion: (value: number | string) => `已是最新版本（v${value}）`,
      updateAvailable: (value: number | string) => `v${value} 可用`,
      updateDownloaded: (value: number | string) => `v${value} 已就绪,等待重启安装`,
      updateError: (value: string) => `检查更新失败:${value}`
    },
    runner: {
      title: '执行器配置',
      intro: '配置底层执行器的相关设置。修改这些设置需要重启执行器才能生效。',
      loading: '正在加载执行器配置...',
      failedLoad: '执行器配置加载失败',
      save: '保存配置',
      saveSuccess: '配置已保存',
      saveFailed: '配置保存失败',
      terminal: '终端设置',
      terminalEnvType: '环境类型',
      security: '安全',
      securityRedactSecrets: '屏蔽敏感信息',
      browser: '浏览器设置',
      browserEngine: '浏览器引擎',
      browserRecordSessions: '录制会话',
      browserAllowPrivateUrls: '允许内网访问',
      debug: '调试开关',
      debugInterrupt: '中断模式',
      debugVisionTools: '调试视觉工具',
      auxiliary: '辅助工具',
      auxiliaryVisionTimeout: '视觉模型超时时间 (秒)',
      auxiliaryVisionTemperature: '视觉模型温度'
    },
    skills: {
      title: '技能',
      intro:
        '下方每一项对应 $SPIRITAGENT_HOME/skills 下的一个 category 目录。开启或关闭会即时推送给执行器;启用集会在每个对话轮次发给后端,让模型只看到你能调用的本地技能。',
      loading: '正在加载技能…',
      loadError: '无法从磁盘读取技能列表。',
      saveError: '无法保存技能开关。',
      refreshError: '本地已保存,但后端会话未刷新 — 下一轮对话仍可能看到旧的技能集合,请再次切换。',
      emptyTitle: '未安装任何技能',
      emptyDesc: '请重新安装 SpiritAgent 以恢复内置技能。',
      hiddenByPlatformTitle: '当前操作系统没有可用技能',
      hiddenByPlatformDesc:
        '本版本 SpiritAgent 内置的技能面向其他操作系统。请在支持的操作系统上重新安装 SpiritAgent 后再启用。'
    },
    account: {
      heading: '账户',
      loading: '加载中…',
      saveFailed: '无法保存账户设置。',
      saved: '账户设置已保存。',
      webSearch: {
        heading: '网页搜索',
        intro: '配置 web 工具所使用的搜索与抓取服务。后端选择按用户保存,密钥仅保存在服务端。',
        backend: '搜索后端',
        backendDesc: 'web_search 使用的服务。若所选后端不可用则自动回退到 ddgs。',
        extractBackend: '抓取后端',
        extractBackendDesc: 'web_extract 使用的服务。未配置密钥时会显式报错。',
        braveApiKey: 'Brave 搜索 API 密钥',
        braveApiKeyPlaceholder: '已设置 · 留空保留当前密钥',
        braveApiKeyDesc: '搜索后端 = brave-free 时必填。',
        tavilyApiKey: 'Tavily API 密钥',
        tavilyApiKeyPlaceholder: '已设置 · 留空保留当前密钥',
        tavilyApiKeyDesc: '抓取后端 = tavily 时必填。',
        tavilyBaseUrl: 'Tavily 接口地址',
        tavilyBaseUrlPlaceholder: 'https://api.tavily.com',
        set: '已配置',
        notSet: '未配置',
        fingerprint: (fp: string) => `指纹:${fp}`,
        reveal: '显示',
        hide: '隐藏',
        clearKey: '清除密钥',
        clearKeyConfirm: '确定要清除该 API 密钥吗?',
        backendOptions: {
          ddgs: 'DuckDuckGo (无需密钥)',
          'brave-free': 'Brave 搜索',
          tavily: 'Tavily'
        },
        extractBackendOptions: {
          tavily: 'Tavily',
          'brave-free': 'Brave 搜索',
          ddgs: 'DuckDuckGo'
        },
        unavailable: {
          extractTavilyNoKey: '添加 Tavily API 密钥之前 web_extract 不可用。',
          extractNonTavilyNoKey: 'web_extract 仅在 Tavily 后端可用。选择 Tavily 并添加密钥,或改用 web_search。',
          extractNonTavilyWithKey: 'web_extract 仅在 Tavily 后端可用。将"抽取后端"切换为 Tavily 即可启用。',
          searchKeyFallback: (selectedBackend: string) =>
            `添加 ${selectedBackend} API 密钥之前 web_search 会回退到 DuckDuckGo。`
        }
      },
      agentDefaults: {
        heading: '智能体默认',
        intro: '新会话的用户级默认设置,不影响已存在的会话。',
        reasoningEffort: '推理深度',
        reasoningEffortDesc: '模型每轮推理的强度。none 关闭推理，low/medium/high 逐级加深。',
        backgroundReview: '后台记忆整理',
        backgroundReviewDesc: '异步从历史会话中抽取记忆。',
        reasoningOptions: {
          none: '关闭',
          low: '低',
          medium: '中',
          high: '高'
        }
      },
      contextCompression: {
        heading: '对话压缩',
        intro: '长对话接近上下文窗口上限时,自动用摘要替换最早的消息,让单次会话持续更久。',
        enableCompression: '启用上下文压缩',
        enableCompressionDesc: '关闭后仅保留最近 40 条消息(确定性截断),不做语义摘要。',
        threshold: '压缩阈值',
        thresholdDesc: '上下文占用达到窗口的该比例时触发压缩。',
        thresholdOptions: {
          '0.5': '50%',
          '0.6': '60%',
          '0.7': '70%',
          '0.8': '80%',
          '0.9': '90%'
        }
      },
      signOut: '退出登录',
      signOutConfirm: '确定要退出登录吗？'
    }
  },

  speech: {
    title: '语音',
    intro: '语音输入与录音设置',
    loading: '加载中…',
    sttEnabledTitle: '语音转文字总开关',
    sttEnabledDesc: '关闭后语音条与通话模式均不可用。',
    sttEngineTitle: 'STT 引擎',
    sttEngineDesc: '语音转文字优先使用的引擎。本地引擎免费、零成本。',
    sttSilentFallbackTitle: '低置信度自动切云端',
    sttSilentFallbackDesc: '本地识别不确定时静默改用云端再试，让结果更准（关闭则直接显示本地结果）',
    ttsEngineTitle: 'TTS 引擎',
    ttsEngineDesc: '文字转语音优先使用的引擎。自动模式下云端优先，音色与音质更佳。',
    sttEngineAuto: '自动（本地优先）',
    ttsEngineAuto: '自动（云端优先）',
    engineLocal: '仅本地',
    engineCloud: '云端',
    engineLocalAvail: '本地可用',
    engineLocalUnavail: '本地不可用',
    recordingTitle: '录音时长上限',
    recordingDesc: '单条语音录音的最大时长（秒）',
    save: '保存',
    saving: '保存中…',
    saved: '已保存',
    saveFailed: '保存失败'
  },

  voiceGallery: {
    title: '音色目录',
    intro: '浏览当前云端 TTS 服务提供的所有音色，点击试听。更换伙伴音色请在精灵窗口的「伙伴设置」中进行。',
    loading: '正在加载音色目录…',
    empty: '当前没有可用音色。',
    error: '音色目录加载失败，请稍后重试。',
    provider: '服务商',
    preview: '试听',
    playing: '播放中…',
    all: '全部',
    designSupported: '支持音色设计'
  },

  skills: {
    tabSkills: '技能',
    tabToolsets: '工具集',
    all: '全部',
    other: '其他',
    searchSkills: '搜索技能…',
    searchToolsets: '搜索工具集…',
    loading: '正在加载能力…',
    noSkillsTitle: '未找到技能',
    noSkillsDesc: '尝试更宽泛的搜索或其他分类。',
    loadFailedTitle: '技能列表加载失败',
    loadFailedDesc: '请稍后重试,或检查 $SPIRITAGENT_HOME/skills 目录。',
    noToolsetsTitle: '未找到工具集',
    noToolsetsDesc: '尝试更宽泛的搜索词。',
    noDescription: '暂无描述。',
    toolsetsEnabled: (enabled: number, total: number) => `已启用 ${enabled}/${total} 个工具集`,
    skillsLoadFailed: '技能加载失败',
    toolsetsRefreshFailed: '工具集刷新失败'
  },

  toolsets: {
    browser_automation: { label: '浏览器自动化', description: '导航、点击、快照、Cookie/CDP 等多后端浏览器能力。' },
    file_operations: { label: '文件操作', description: '读写、补丁、目录与文件搜索。' },
    terminal: { label: '终端', description: '本地/Docker/SSH 后端的命令行执行。' },
    code_execution: { label: '代码执行', description: '沙箱 Python 执行与受限调用。' },
    process_management: { label: '进程管理', description: '后台进程的启动与跟踪。' },
    skills_system: { label: '技能系统', description: '列出、查看与管理 Skill 内容。' },
    memory: { label: '记忆', description: '长期记忆的写入、检索与删除。' },
    web_tools: { label: '联网工具', description: '网络搜索与网页内容抽取。' },
    image_generation: { label: '图片生成', description: '通过云端模型生成图片。' },
    text_to_speech: { label: '语音合成', description: '文本转语音 TTS。' },
    messaging: { label: '消息', description: '通过 Webhook 发送消息。' },
    scheduled_tasks: { label: '定时任务', description: 'Cron 触发与周期调度。' },
    agent_delegation: { label: '子代理委托', description: '派生子会话与子代理。' },
    computer_use: { label: '桌面操控', description: '通过 Windows 后端接管桌面。' },
    media_analysis: { label: '多媒体分析', description: '图片分析。' }
  },

  errors: {
    genericFailure: '发生错误',
    boundaryTitle: '界面出错了',
    boundaryDesc: '此视图遇到意外错误。你的对话和设置是安全的。',
    reloadWindow: '重新加载窗口',
    openLogs: '打开日志'
  },

  ui: {
    search: {
      clear: '清除搜索'
    }
  }
}

export type Translations = typeof strings

import type { Translations } from './types'

export const strings: Translations = {
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
    ready: 'DeskAgent 桌面版已就绪',
    desktopBootFailedWithMessage: message => `桌面启动失败：${message}`,
    steps: {
      connectingGateway: '正在连接桌面网关',
      loadingSettings: '正在加载 DeskAgent 设置',
      loadingSessions: '正在加载最近会话',
      startingDesktopConnection: '正在启动桌面连接',
      startingDeskAgentDesktop: '正在启动 DeskAgent 桌面版…'
    },
    errors: {
      backgroundExited: 'DeskAgent 后台进程已退出。',
      backgroundExitedDuringStartup: 'DeskAgent 后台进程在启动期间退出。',
      backendStopped: '后端已停止',
      desktopBootFailed: '桌面启动失败',
      gatewaySignInRequired: '需要登录网关',
      ipcBridgeUnavailable: '桌面 IPC 桥不可用。'
    },
    failure: {
      title: 'DeskAgent 无法启动',
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
    more: count => `另外 ${count} 条通知`,
    clearAll: '全部清除',
    dismiss: '关闭通知',
    details: '详情',
    copyDetail: '复制详情',
    copyDetailFailed: '无法复制通知详情',
    updateReadyMessage: count => `有 ${count} 项新更改可用。`,
    errors: {
      elevenLabsNeedsKey: 'ElevenLabs STT 需要 ELEVENLABS_API_KEY。',
      elevenLabsRejectedKey: 'ElevenLabs 拒绝了该 API key (401)。',
      methodNotAllowed: '桌面后端拒绝了该请求 (405 Method Not Allowed)。请尝试重启 DeskAgent Desktop。',
      microphonePermission: '麦克风权限已被拒绝。',
      openaiRejectedApiKey: 'OpenAI 拒绝了该 API key。',
      openaiRejectedApiKeyWithStatus: status => `OpenAI 拒绝了该 API key (${status} invalid_api_key)。`,
      openaiTtsNeedsKey: 'OpenAI TTS 需要 VOICE_TOOLS_OPENAI_KEY 或 OPENAI_API_KEY。'
    },
    voice: {
      configureSpeechToText: '配置语音转文字后即可使用语音模式。',
      couldNotStartSession: '无法启动语音会话',
      microphoneAccessDenied: '麦克风访问被拒绝。',
      microphoneConstraintsUnsupported: '此设备不支持当前麦克风约束。',
      microphoneFailed: '麦克风出错',
      microphoneInUse: '麦克风正被其他应用占用。',
      microphonePermissionDenied: '麦克风权限被拒绝。',
      microphoneStartFailed: '无法开始麦克风录音。',
      microphoneUnsupported: '当前运行环境不支持麦克风录音。',
      noMicrophone: '未找到麦克风。',
      noSpeechDetected: '没有检测到语音',
      playbackFailed: '语音播放失败',
      recordingFailed: '语音录制失败',
      transcriptionFailed: '语音转写失败',
      transcriptionUnavailable: '语音转写暂不可用。',
      tryRecordingAgain: '请再录一次。',
      unavailable: '语音不可用',
      invalidTitle: '音色已失效',
      invalidMessage: name => `你之前选的音色「${name}」已不在当前目录，已临时用默认音色，去伙伴设置里重新挑一个吧～`,
      invalidAction: '去设置'
    },
    events: {
      referencesTitle: '引用',
      referencesMessage: items => items,
      compressionTimeoutTitle: '上下文压缩',
      compressionTimeoutMessage: '压缩请求超时 — 继续而不压缩。',
      cronTriggeredTitle: '定时任务已触发',
      cronTriggeredMessage: (name, jobId) => name || `任务 #${jobId}`,
      backgroundReviewFailedTitle: '后台任务',
      backgroundReviewFailedMessage: error => error || '记忆提取失败'
    }
  },

  titlebar: {
    hideSidebar: '隐藏侧边栏',
    showSidebar: '显示侧边栏',
    search: '搜索',
    searchTitle: '搜索会话、视图与操作',
    swapSidebarSides: '交换侧边栏位置',
    swapSidebarSidesTitle: '交换会话栏和文件浏览器的位置',
    hideRightSidebar: '隐藏右侧栏',
    showRightSidebar: '显示右侧栏',
    muteHaptics: '关闭触感反馈',
    unmuteHaptics: '开启触感反馈',
    openSettings: '打开设置',
    openKeybinds: '键盘快捷键'
  },

  keybinds: {
    title: '键盘快捷键',
    subtitle: open => `点击快捷键即可重新绑定 · ${open} 可重新打开此面板。`,
    rebind: '重新绑定',
    reset: '恢复默认',
    resetAll: '全部重置',
    pressKey: '请按下按键…',
    set: '设置',
    conflictWith: label => `已绑定到“${label}”`,
    categories: {
      composer: '输入框',
      profiles: '配置',
      session: '会话',
      navigation: '导航',
      view: '视图'
    },
    actions: {
      'keybinds.openPanel': '打开键盘快捷键',
      'nav.commandPalette': '打开命令面板',
      'nav.commandCenter': '打开命令中心',
      'nav.settings': '打开设置',
      'nav.profiles': '打开配置',
      'nav.artifacts': '打开制品',
      'nav.agents': '打开智能体',
      'session.new': '新建会话',
      'session.next': '下一个会话',
      'session.prev': '上一个会话',
      'session.focusSearch': '搜索会话',
      'session.togglePin': '固定/取消固定当前会话',
      'composer.focus': '聚焦输入框',
      'composer.modelPicker': '打开模型选择器',
      'view.toggleSidebar': '切换会话侧边栏',
      'view.toggleRightSidebar': '切换文件浏览器',
      'view.showFiles': '显示文件浏览器',
      'view.showTerminal': '显示终端',
      'view.terminalSelection': '将终端选区发送到输入框',
      'view.closePreviewTab': '关闭预览标签',
      'view.flipPanes': '交换侧边栏位置',
      'appearance.toggleMode': '切换浅色/深色',
      'profile.default': '切换到默认配置',
      'profile.switch.1': '切换到配置 1',
      'profile.switch.2': '切换到配置 2',
      'profile.switch.3': '切换到配置 3',
      'profile.switch.4': '切换到配置 4',
      'profile.switch.5': '切换到配置 5',
      'profile.switch.6': '切换到配置 6',
      'profile.switch.7': '切换到配置 7',
      'profile.switch.8': '切换到配置 8',
      'profile.switch.9': '切换到配置 9',
      'profile.switch.10': '切换到配置 10',
      'profile.switch.11': '切换到配置 11',
      'profile.switch.12': '切换到配置 12',
      'profile.switch.13': '切换到配置 13',
      'profile.switch.14': '切换到配置 14',
      'profile.switch.15': '切换到配置 15',
      'profile.switch.16': '切换到配置 16',
      'profile.switch.17': '切换到配置 17',
      'profile.switch.18': '切换到配置 18',
      'profile.next': '下一个配置',
      'profile.prev': '上一个配置',
      'profile.toggleAll': '切换全部配置视图',
      'profile.create': '创建配置',
      'composer.send': '发送消息',
      'composer.newline': '插入换行',
      'composer.steer': '引导正在运行的回合',
      'composer.sendQueued': '发送下一条排队消息',
      'composer.mention': '引用文件、文件夹、网址',
      'composer.slash': '斜杠命令面板',
      'composer.help': '快速帮助',
      'composer.history': '切换弹窗/历史',
      'composer.cancel': '关闭弹窗·取消运行'
    }
  },

  language: {
    label: '语言',
    description: '选择桌面界面的语言。',
    saving: '正在保存语言…',
    saveError: '语言更新失败',
    switchTo: '切换语言',
    searchPlaceholder: '搜索语言…',
    noResults: '未找到语言'
  },

  settings: {
    closeSettings: '关闭设置',
    exportConfig: '导出配置',
    importConfig: '导入配置',
    resetToDefaults: '恢复默认',
    resetConfirm: '将所有设置恢复为 DeskAgent 默认值？',
    exportFailed: '导出失败',
    resetFailed: '重置失败',
    nav: {
      account: '账户',
      mcp: 'MCP',
      archivedChats: '已归档对话',
      about: '关于',
      appearance: '外观',
      toolsets: '工具集',
      runner: '执行器',
      skills: '技能与工具',
      voices: '音色目录'
    },
    modeOptions: {
      light: { label: '明亮', description: '明亮的桌面界面' },
      dark: { label: '暗色', description: '低眩光工作区' },
      system: { label: '跟随系统', description: '跟随系统外观' }
    },
    appearance: {
      title: '外观',
      intro: '这些是仅桌面端的显示偏好。模式控制明暗；主题控制强调色与对话界面样式。',
      colorMode: '颜色模式',
      colorModeDesc: '选择固定模式，或让 DeskAgent 跟随系统设置。',
      toolViewTitle: '工具调用显示',
      toolViewDesc: '产品模式隐藏原始工具数据；技术模式显示完整输入/输出。',
      product: '产品',
      productDesc: '易读的工具活动与简洁摘要。',
      technical: '技术',
      technicalDesc: '包含原始工具参数/结果及底层细节。',
      themeTitle: '主题',
      themeDesc: '仅桌面端调色板。所选模式叠加其上。',
      themeProfileNote: profile => `已为「${profile}」配置文件保存——每个配置文件保留各自的主题。`
    },
    about: {
      heading: 'DeskAgent Desktop',
      version: value => `版本 ${value}`,
      versionUnavailable: '版本不可用',
      checkForUpdates: '检查更新',
      checking: '检查中…',
      upToDate: '已是最新版本',
      upToDateWithVersion: value => `已是最新版本（v${value}）`,
      updateAvailable: value => `v${value} 可用`,
      updateDownloaded: value => `v${value} 已就绪,等待重启安装`,
      updateError: value => `检查更新失败:${value}`,
      download: '下载更新',
      restart: '立即重启',
      later: '稍后再说'
    },
    envActions: {
      actionsFor: label => `${label} 的操作`,
      credentialActions: '凭据操作',
      docs: '文档',
      hideValue: '隐藏值',
      revealValue: '显示值',
      replace: '替换',
      set: '设置',
      clear: '清除'
    },
    mcp: {
      loading: '正在加载 MCP 服务器...',
      failedLoad: 'MCP 配置加载失败',
      nameRequiredTitle: '需要名称',
      nameRequiredMessage: '请为此 MCP 服务器提供配置键。',
      objectRequired: '服务器配置必须是 JSON 对象',
      invalidJson: 'MCP JSON 无效',
      saveFailed: '保存失败',
      saveRestartFailed: (error: string) => `配置已保存，但执行器重启失败：${error}`,
      removeFailed: '移除失败',
      gatewayUnavailableTitle: '网关不可用',
      gatewayUnavailableMessage: '重新加载 MCP 前请先重连网关。',
      reloadedTitle: 'MCP 工具已重新加载',
      reloadedMessage: '新的工具 schema 将应用到后续回合。',
      reloadFailed: 'MCP 重新加载失败',
      savedTitle: 'MCP 服务器已保存',
      savedMessage: name => `${name} 会在 MCP 重新加载后生效。`,
      newServer: '新服务器',
      reload: '重新加载 MCP',
      reloading: '重新加载中...',
      emptyTitle: '没有 MCP 服务器',
      emptyDesc: '添加 stdio 或 HTTP 服务器以暴露 MCP 工具。',
      disabled: '已禁用',
      editServer: '编辑服务器',
      name: '名称',
      serverJson: '服务器 JSON',
      remove: '移除',
      saveServer: '保存服务器'
    },
    sessions: {
      loading: '正在加载已归档会话…',
      archivedTitle: '已归档会话',
      archivedIntro: '已归档对话会从侧边栏隐藏，但会保留全部消息。在侧边栏 Ctrl/⌘ 点击对话即可归档。',
      emptyArchivedTitle: '暂无归档',
      emptyArchivedDesc: '归档一个对话后会显示在这里。',
      unarchive: '取消归档',
      deletePermanently: '永久删除',
      messages: count => `${count} 条消息`,
      restored: '已恢复',
      deleteConfirm: title => `永久删除“${title}”？此操作无法撤销。`,
      defaultDirTitle: '默认项目目录',
      defaultDirDesc: '新会话默认从此文件夹开始，除非你选择其他目录。留空则使用你的 home 目录。',
      defaultDirUpdated: '默认项目目录已更新',
      defaultsTo: label => `默认使用 ${label}。`,
      change: '更改',
      choose: '选择',
      clear: '清除',
      notSet: '未设置',
      failedLoad: '无法加载已归档会话',
      unarchiveFailed: '取消归档失败',
      deleteFailed: '删除失败',
      updateDirFailed: '无法更新默认目录',
      clearDirFailed: '无法清除默认目录'
    },
    runner: {
      title: '执行器配置',
      intro: '配置底层执行器的相关设置。修改这些设置需要重启执行器才能生效。',
      loading: '正在加载执行器配置...',
      failedLoad: '执行器配置加载失败',
      save: '保存配置',
      saveSuccess: '配置已保存，正在重启执行器...',
      saveFailed: '配置保存失败',
      saveRestartFailed: (error: string) => `配置已保存，但执行器重启失败：${error}`,
      invalidYaml: 'YAML 格式无效',
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
    toolsets: {
      loadingConfig: '正在加载配置',
      savedTitle: '凭据已保存',
      savedMessage: key => `${key} 已更新。`,
      removedTitle: '凭据已移除',
      removedMessage: key => `${key} 已移除。`,
      failedSave: key => `保存 ${key} 失败`,
      failedRemove: key => `移除 ${key} 失败`,
      failedReveal: key => `显示 ${key} 失败`,
      removeConfirm: key => `从 .env 中移除 ${key}？`,
      set: '已设置',
      notSet: '未设置',
      selectedTitle: '已选择提供方',
      selectedMessage: provider => `${provider} 现在处于活动状态。`,
      failedSelect: provider => `选择 ${provider} 失败`,
      failedLoad: '工具配置加载失败',
      noProviderOptions: '此工具集没有提供方选项；启用后即可使用当前配置。',
      noProviders: '此工具集当前没有可用提供方。',
      ready: '就绪',
      nousIncluded: '包含在 Nous 订阅中；登录 Nous Portal 即可激活。',
      noApiKeyRequired: '不需要 API 密钥。',
      postSetupHint: step => `此后端需要一次性安装 (${step})。将在此机器上执行，可能需要几分钟。`,
      postSetupRun: '运行设置',
      postSetupRunning: '安装中…',
      postSetupStarting: '启动中…',
      postSetupCompleteTitle: '设置完成',
      postSetupCompleteMessage: step => `已安装 ${step}。`,
      postSetupErrorTitle: '设置完成但有错误',
      postSetupErrorMessage: step => `请检查 ${step} 日志。`,
      postSetupFailed: step => `运行 ${step} 设置失败`
    },
    skills: {
      title: '技能',
      intro:
        '下方每一项对应 $DESKAGENT_HOME/skills 下的一个 category 目录。开启或关闭会重写本地 config.yaml 并重启执行器;启用集会在每个对话轮次发给后端,让模型只看到你能调用的本地技能。',
      loading: '正在加载技能…',
      loadError: '无法从磁盘读取技能列表。',
      saveError: '无法保存技能开关。',
      refreshError: '本地已保存,但后端会话未刷新 — 下一轮对话仍可能看到旧的技能集合,请再次切换。',
      emptyTitle: '未安装任何技能',
      emptyDesc: '请重新安装 DeskAgent 以恢复内置技能。',
      hiddenByPlatformTitle: '当前操作系统没有可用技能',
      hiddenByPlatformDesc: '本版本 DeskAgent 内置的技能面向其他操作系统。请在支持的操作系统上重新安装 DeskAgent 后再启用。'
    },
    account: {
      heading: '账户',
      loading: '加载中…',
      saveFailed: '无法保存账户设置。',
      saved: '账户设置已保存。',
      changePassword: {
        title: '修改密码',
        currentPassword: '当前密码',
        newPassword: '新密码',
        confirmPassword: '确认新密码',
        submit: '修改密码',
        success: '密码已更新。',
        mismatch: '两次输入的新密码不一致。',
        tooShort: '新密码至少 8 个字符。',
        sameAsOld: '新密码不能与当前密码相同。'
      },
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
        reasoningEffortDesc: '模型每轮推理的强度。',
        serviceTier: '服务等级',
        serviceTierDesc: '设置服务等级与全局 Fast 模式开关。标为"快速"的等级会在编辑器中开启 Fast 模式。',
        yoloMode: 'YOLO 模式',
        yoloModeDesc: '自动批准工具调用,不再逐次询问。',
        backgroundReview: '后台记忆整理',
        backgroundReviewDesc: '异步从历史会话中抽取记忆。',
        showSubagentsInSidebar: '在侧边栏显示子代理',
        showSubagentsInSidebarDesc: '在会话列表中显示子代理会话。搜索和直接访问不受影响。',
        reasoningOptions: {
          minimal: '最低',
          low: '低',
          medium: '中',
          high: '高',
          max: '最高'
        },
        serviceTierOptions: {
          standard: '标准',
          fast: '快速',
          priority: '优先',
          on: '开启(旧)',
          auto: '自动'
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
    sttTitle: '语音转文字（STT）',
    sttDesc: '开启后可用语音条与通话模式说话输入',
    sttEngineTitle: 'STT 引擎',
    sttEngineDesc: '语音转文字优先使用的引擎。本地引擎免费、零成本。',
    sttSilentFallbackTitle: '低置信度自动切云端',
    sttSilentFallbackDesc: '本地识别不确定时静默改用云端再试，让结果更准（关闭则直接显示本地结果）',
    ttsEngineTitle: 'TTS 引擎',
    ttsEngineDesc: '文字转语音优先使用的引擎。本地引擎免费、零成本。',
    engineAuto: '自动（本地优先）',
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

  insights: {
    heading: '使用洞察',
    loading: '正在加载洞察数据…',
    retry: '重试',
    refresh: '刷新',
    windowHint: days => `最近 ${days} 天`,
    empty: '暂无数据',
    noBaseUrl: '默认地址',
    overview: {
      sessions: '会话数',
      messages: '消息数',
      tokens: 'Token 总量',
      hours: '使用时长',
      tools: '工具调用'
    },
    topTools: '高频工具',
    models: '模型配置',
    platforms: '使用平台',
    skills: '记忆与标签',
    skillsTotal: (total, recent) => `共 ${total} 条记忆，近期新增 ${recent} 条`,
    activity: '每日活动'
  },

  skills: {
    tabSkills: '技能',
    tabToolsets: '工具集',
    all: '全部',
    other: '其他',
    searchSkills: '搜索技能…',
    searchToolsets: '搜索工具集…',
    refresh: '刷新技能',
    refreshing: '正在刷新技能',
    loading: '正在加载能力…',
    noSkillsTitle: '未找到技能',
    noSkillsDesc: '尝试更宽泛的搜索或其他分类。',
    loadFailedTitle: '技能列表加载失败',
    loadFailedDesc: '请稍后重试,或检查 $DESKAGENT_HOME/skills 目录。',
    noToolsetsTitle: '未找到工具集',
    noToolsetsDesc: '尝试更宽泛的搜索词。',
    noDescription: '暂无描述。',
    configured: '已配置',
    needsKeys: '需要密钥',
    toolsetsEnabled: (enabled, total) => `已启用 ${enabled}/${total} 个工具集`,
    configureToolset: label => `配置 ${label}`,
    toggleToolset: label => `切换 ${label} 工具集`,
    skillsLoadFailed: '技能加载失败',
    toolsetsRefreshFailed: '工具集刷新失败',
    skillEnabled: '技能已启用',
    skillDisabled: '技能已禁用',
    toolsetEnabled: '工具集已启用',
    toolsetDisabled: '工具集已禁用',
    appliesToNewSessions: name => `${name} 将应用于新会话。`,
    failedToUpdate: name => `更新 ${name} 失败`
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
    computer_use: { label: '桌面操控', description: '通过 CUA / Win 后端接管桌面。' },
    media_analysis: { label: '多媒体分析', description: '图片分析。' }
  },

  agents: {
    close: '关闭代理',
    title: '派生树',
    subtitle: '当前回合的子代理实时活动。',
    emptyTitle: '暂无活跃子代理',
    emptyDesc: '当某个回合派发任务时，子代理会在此实时显示进度。',
    running: '运行中',
    failed: '失败',
    done: '完成',
    streaming: '流式传输',
    files: '文件',
    moreFiles: count => `还有 ${count} 个文件`,
    delegation: index => `派发 ${index}`,
    workers: count => `${count} 个工作单元`,
    workersActive: count => `${count} 个活跃`,
    agentsCount: count => `${count} 个代理`,
    activeCount: count => `${count} 个活跃`,
    failedCount: count => `${count} 个失败`,
    toolsCount: count => `${count} 个工具`,
    filesCount: count => `${count} 个文件`,
    updatedAgo: age => `更新于 ${age}`,
    ageNow: '刚刚',
    ageSeconds: seconds => `${seconds} 秒前`,
    ageMinutes: minutes => `${minutes} 分钟前`,
    ageHours: hours => `${hours} 小时前`,
    durationSeconds: seconds => `${seconds} 秒`,
    durationMinutes: (minutes, seconds) => `${minutes} 分 ${seconds} 秒`,
    tokensK: k => `${k}k 词元`,
    tokens: value => `${value} 词元`
  },

  commandCenter: {
    close: '关闭命令中心',
    paletteTitle: '命令面板',
    back: '返回',
    searchPlaceholder: '搜索会话、视图与操作',
    goTo: '前往',
    commandCenter: '命令中心',
    appearance: '外观',
    settings: '设置',
    changeTheme: '更改主题...',
    changeColorMode: '更改颜色模式...',
    settingsFields: '设置字段',
    mcpServers: 'MCP 服务器',
    archivedChats: '已归档对话',
    sections: { sessions: '会话', system: '系统', usage: '用量' },
    sectionDescriptions: {
      sessions: '搜索与管理会话',
      system: '状态、日志与系统操作',
      usage: '一段时间内的词元、成本与技能活动'
    },
    nav: {
      newChat: { title: '新建会话', detail: '开始一个新会话' },
      settings: { title: '设置', detail: '配置 DeskAgent 桌面端' },
      skills: { title: '技能与工具', detail: '启用技能、工具集与提供方' },
      messaging: { title: '消息平台', detail: '配置 Telegram、Slack、Discord 等' },
      artifacts: { title: '产物', detail: '浏览生成的输出' }
    },
    sectionEntries: {
      sessions: { title: '会话面板', detail: '搜索、置顶与管理会话' },
      system: { title: '系统面板', detail: '网关状态、日志、重启/更新' },
      usage: { title: '用量面板', detail: '词元、成本与技能活动' }
    },
    providerNavigate: '导航',
    providerSessions: '会话',
    refresh: '刷新',
    refreshing: '刷新中…',
    noResults: '未找到匹配结果。',
    pinSession: '置顶会话',
    unpinSession: '取消置顶',
    exportSession: '导出会话',
    deleteSession: '删除会话',
    noSessions: '暂无会话。',
    gatewayRunning: '消息网关运行中',
    gatewayStopped: '消息网关已停止',
    deskagentActiveSessions: (version, count) => `DeskAgent ${version} · 活跃会话 ${count}`,
    restartMessaging: '重启消息服务',
    actionRunning: '运行中',
    actionDone: '完成',
    actionFailed: '失败',
    actionStartedWaiting: '操作已启动，等待状态…',
    loadingStatus: '正在加载状态…',
    recentLogs: '最近日志',
    noLogs: '尚未加载日志。',
    days: count => `${count} 天`,
    statSessions: '会话',
    statApiCalls: 'API 调用',
    statTokens: '输入/输出词元',
    statCost: '预估成本',
    actualCost: cost => `实际 ${cost}`,
    loadingUsage: '正在加载用量…',
    noUsage: period => `最近 ${period} 天暂无用量。`,
    retry: '重试',
    dailyTokens: '每日词元',
    input: '输入',
    output: '输出',
    noDailyActivity: '暂无每日活动。',
    topModels: '常用模型',
    noModelUsage: '暂无模型用量。',
    topSkills: '常用技能',
    noSkillActivity: '暂无技能活动。',
    actions: count => `${count} 次操作`
  },

  messaging: {
    search: '搜索消息平台…',
    loading: '正在加载消息平台…',
    loadFailed: '消息平台加载失败',
    states: {
      connected: '已连接',
      connecting: '连接中',
      disabled: '已禁用',
      fatal: '错误',
      gateway_stopped: '消息网关已停止',
      not_configured: '需要设置',
      pending_restart: '需要重启',
      retrying: '重试中',
      startup_failed: '启动失败'
    },
    unknown: '未知',
    hintPendingRestart: '在状态栏重启网关以应用此更改。',
    hintGatewayStopped: '在状态栏启动网关以建立连接。',
    credentialsSet: '凭据已设置',
    needsSetup: '需要设置',
    gatewayStopped: '消息网关已停止',
    getCredentials: '获取你的凭据',
    openSetupGuide: '打开设置指南',
    required: '必填',
    recommended: '推荐',
    advanced: count => `高级 (${count})`,
    noTokenNeeded: '此平台无需在此填写令牌。请按上方设置指南操作，然后在下方启用。',
    enabled: '已启用',
    disabled: '已禁用',
    unsavedChanges: '有未保存的更改',
    saving: '保存中…',
    saveChanges: '保存更改',
    saved: '已保存',
    replaceValue: '替换当前值',
    openDocs: '打开文档',
    clearField: key => `清除 ${key}`,
    enableAria: name => `启用 ${name}`,
    disableAria: name => `禁用 ${name}`,
    platformEnabled: name => `${name} 已启用`,
    platformDisabled: name => `${name} 已禁用`,
    restartToApply: '重启网关后此更改才会生效。',
    setupSaved: name => `${name} 设置已保存`,
    restartToReconnect: '重启网关以使用新凭据重新连接。',
    keyCleared: key => `${key} 已清除`,
    setupUpdated: name => `${name} 设置已更新。`,
    failedUpdate: name => `更新 ${name} 失败`,
    failedSave: name => `保存 ${name} 失败`,
    failedClear: key => `清除 ${key} 失败`,
    fieldCopy: {
      TELEGRAM_BOT_TOKEN: {
        label: 'Bot 令牌',
        help: '用 @BotFather 创建一个机器人，然后粘贴它给你的令牌。',
        placeholder: '粘贴 Telegram bot 令牌'
      },
      TELEGRAM_ALLOWED_USERS: {
        label: '允许的 Telegram 用户 ID',
        help: '推荐。来自 @userinfobot 的逗号分隔数字 ID。不设置则任何人都能私信你的机器人。'
      },
      TELEGRAM_PROXY: { label: '代理 URL', help: '仅在 Telegram 被屏蔽的网络中需要。' },
      DISCORD_BOT_TOKEN: { label: 'Bot 令牌', help: '在 Discord 开发者门户创建应用，添加机器人，然后粘贴其令牌。' },
      DISCORD_ALLOWED_USERS: { label: '允许的 Discord 用户 ID', help: '推荐。逗号分隔的 Discord 用户 ID。' },
      DISCORD_REPLY_TO_MODE: { label: '回复方式', help: 'first、all 或 off。' },
      DISCORD_ALLOW_ALL_USERS: {
        label: '允许所有 Discord 用户',
        help: '仅用于开发。为 true 时，任何人都可以私信 bot，不需要允许列表。'
      },
      DISCORD_HOME_CHANNEL: { label: '主页频道 ID', help: 'bot 主动发送消息的频道（cron 输出、提醒等）。' },
      DISCORD_HOME_CHANNEL_NAME: { label: '主页频道名称', help: '日志和状态输出中显示的主页频道名称。' },
      BLUEBUBBLES_ALLOW_ALL_USERS: { label: '允许所有 iMessage 用户', help: '为 true 时跳过 BlueBubbles 允许列表。' },
      MATTERMOST_ALLOW_ALL_USERS: { label: '允许所有 Mattermost 用户' },
      MATTERMOST_HOME_CHANNEL: { label: '主页频道' },
      QQ_ALLOW_ALL_USERS: { label: '允许所有 QQ 用户' },
      QQBOT_HOME_CHANNEL: { label: 'QQ 主页频道', help: 'cron 投递的默认频道或群组。' },
      QQBOT_HOME_CHANNEL_NAME: { label: 'QQ 主页频道名称' },
      SLACK_BOT_TOKEN: {
        label: 'Slack bot 令牌',
        help: '安装 Slack 应用后，在 OAuth & Permissions 中找到 bot 令牌。',
        placeholder: '粘贴 Slack bot 令牌'
      },
      SLACK_APP_TOKEN: {
        label: 'Slack app 令牌',
        help: 'Socket Mode 需要 app 级令牌。',
        placeholder: '粘贴 Slack app 令牌'
      },
      SLACK_ALLOWED_USERS: { label: '允许的 Slack 用户 ID', help: '推荐。逗号分隔的 Slack 用户 ID。' },
      MATTERMOST_URL: { label: '服务器 URL', placeholder: 'https://mattermost.example.com' },
      MATTERMOST_TOKEN: { label: 'Bot 令牌' },
      MATTERMOST_ALLOWED_USERS: { label: '允许的用户 ID', help: '推荐。逗号分隔的 Mattermost 用户 ID。' },
      MATRIX_HOMESERVER: { label: 'Homeserver URL', placeholder: 'https://matrix.org' },
      MATRIX_ACCESS_TOKEN: { label: '访问令牌' },
      MATRIX_USER_ID: { label: 'Bot 用户 ID', placeholder: '@deskagent:example.org' },
      MATRIX_ALLOWED_USERS: { label: '允许的 Matrix 用户 ID', help: '推荐。@user:server 格式的逗号分隔用户 ID。' },
      SIGNAL_HTTP_URL: {
        label: 'Signal 桥接 URL',
        placeholder: 'http://127.0.0.1:8080',
        help: '运行中的 signal-cli REST 桥接的 URL。'
      },
      SIGNAL_ACCOUNT: { label: '电话号码', help: '在 signal-cli 桥接中注册的号码。' },
      SIGNAL_ALLOWED_USERS: { label: '允许的 Signal 用户', help: '推荐。逗号分隔的 Signal 标识符。' },
      WHATSAPP_ENABLED: { label: '启用 WhatsApp 桥接', help: '由下方开关自动设置。除非确知需要，否则请勿改动。' },
      WHATSAPP_MODE: { label: '桥接模式' },
      WHATSAPP_ALLOWED_USERS: { label: '允许的 WhatsApp 用户', help: '推荐。逗号分隔的电话号码或 WhatsApp ID。' }
    },
    platformIntro: {
      telegram:
        '在 Telegram 中，与 @BotFather 对话，运行 /newbot，复制它给你的令牌。然后从 @userinfobot 获取你的数字用户 ID。',
      discord:
        '打开 Discord 开发者门户，创建应用，添加 Bot，然后复制其令牌。用正确的权限范围把机器人邀请到你的服务器。',
      slack: '创建 Slack 应用，启用 Socket Mode，安装到你的工作区，然后复制 bot 令牌和 app 级令牌。',
      mattermost: '在你的 Mattermost 服务器上，创建机器人账户或个人访问令牌，然后在此粘贴服务器 URL 和令牌。',
      matrix: '用机器人账户登录你的 homeserver，然后复制访问令牌、用户 ID 和 homeserver URL。',
      signal: '在可访问的位置运行 signal-cli REST 桥接，然后把 DeskAgent 指向该 URL 和已注册的电话号码。',
      whatsapp: '启动 DeskAgent 自带的 WhatsApp 桥接，首次运行时扫描二维码，然后启用该平台。',
      bluebubbles: '在装有 iMessage 的 Mac 上运行 BlueBubbles Server，暴露其 API，然后用服务器密码把 DeskAgent 指向该 URL。',
      homeassistant: '在 Home Assistant 中打开你的个人资料并创建长期访问令牌。把它连同你的 HA URL 一起粘贴到这里。',
      email: '使用专用邮箱。对于 Gmail/Workspace,创建应用专用密码并使用 imap.gmail.com / smtp.gmail.com。',
      sms: '从 Twilio 控制台获取你的 Account SID 和 Auth Token，以及一个可发送短信的电话号码。',
      dingtalk: '在开发者控制台创建钉钉应用，然后在此复制 Client ID(App key) 和 Client Secret。',
      feishu: '创建飞书 / Lark 应用，配置机器人能力，复制 App ID、App secret 和事件加密密钥。',
      wecom: '在企业微信中添加群机器人，复制其 webhook key 作为 WECOM_BOT_ID。仅可发送——双向请用企业微信 (应用) 选项。',
      wecom_callback: '设置一个企业微信自建应用，暴露其回调 URL，并提供 corp ID、secret、agent ID 和 AES key。',
      weixin: '登录微信公众平台，复制 AppID 和 Token，并把消息回调 URL 指向 DeskAgent。',
      qqbot: '在 QQ 开放平台 (q.qq.com) 注册一个应用，复制 App ID 和 Client Secret。',
      api_server: '把 DeskAgent 暴露为兼容 OpenAI 的 API。设置一个鉴权密钥，然后把 Open WebUI / LobeChat 等指向 host:port。',
      webhook: '运行一个 HTTP 服务器，供其他工具 (GitHub、GitLab、自定义应用)POST。用 secret 验证签名。'
    }
  },

  profiles: {
    close: '关闭配置档案',
    nameHint: '小写字母、数字、连字符和下划线。必须以字母或数字开头。',
    title: '配置档案',
    count: count => `${count} 个配置档案`,
    loading: '正在加载配置档案…',
    newProfile: '新建配置档案',
    allProfiles: '全部配置档案',
    showAllProfiles: '显示全部配置档案',
    switchToProfile: name => `切换到 ${name}`,
    manageProfiles: '管理配置档案...',
    actionsFor: name => `${name} 的操作`,
    color: '颜色...',
    colorFor: name => `${name} 的颜色`,
    setColor: color => `设置颜色 ${color}`,
    autoColor: '自动',
    noProfiles: '暂无配置档案。',
    selectPrompt: '选择一个配置档案以查看其详情。',
    refresh: '刷新配置档案',
    refreshing: '正在刷新配置档案',
    default: '默认',
    skills: count => `${count} 个技能`,
    env: 'env',
    defaultBadge: '默认',
    rename: '重命名',
    copySetup: '复制安装命令',
    copying: '复制中…',
    modelLabel: '模型',
    skillsLabel: '技能',
    notSet: '未设置',
    soulDesc: '内置于此配置档案的系统提示词与人格指令。',
    soulOptional: '可选',
    soulPlaceholder: mode => `此配置档案的系统提示词 / 人格说明。\n留空则保留${mode}默认值。`,
    soulPlaceholderCloned: '克隆的',
    soulPlaceholderEmpty: '空的',
    unsavedChanges: '有未保存的更改',
    loadingSoul: '正在加载 SOUL.md…',
    emptySoul: '空的 SOUL.md —— 开始撰写人格设定…',
    saving: '保存中…',
    saveSoul: '保存 SOUL.md',
    deleteTitle: '删除配置档案？',
    deleteDescPrefix: '这将删除 ',
    deleteDescMid: ' 并移除其 ',
    deleteDescSuffix: ' 目录。此操作无法撤销。',
    deleting: '删除中…',
    createDesc: '配置档案是相互独立的 DeskAgent 环境：各自拥有独立的配置、技能和 SOUL.md。',
    nameLabel: '名称',
    cloneFromDefault: '从默认档案克隆',
    cloneFromDefaultDesc: '从你的默认配置档案复制配置、技能和 SOUL.md。',
    invalidName: hint => `名称无效。${hint}`,
    nameRequired: '名称为必填项。',
    creating: '创建中…',
    createAction: '创建配置档案',
    renameTitle: '重命名配置档案',
    renameDescPrefix: '重命名会更新配置档案目录以及 ',
    renameDescSuffix: ' 中的所有包装脚本。',
    newNameLabel: '新名称',
    renaming: '重命名中…',
    created: '配置档案已创建',
    renamed: '配置档案已重命名',
    deleted: '配置档案已删除',
    setupCopied: '安装命令已复制',
    soulSaved: 'SOUL.md 已保存',
    failedLoad: '加载配置档案失败',
    failedDelete: '删除配置档案失败',
    failedCopy: '复制安装命令失败',
    failedLoadSoul: '加载 SOUL.md 失败',
    failedSaveSoul: '保存 SOUL.md 失败',
    failedCreate: '创建配置档案失败',
    failedRename: '重命名配置档案失败'
  },

  cron: {
    close: '关闭定时任务',
    search: '搜索定时任务…',
    loading: '正在加载定时任务…',
    states: {
      enabled: '已启用',
      scheduled: '已排程',
      running: '运行中',
      paused: '已暂停',
      disabled: '已禁用',
      error: '错误',
      completed: '已完成'
    },
    deliveryLabels: {
      local: '此桌面',
      telegram: 'Telegram',
      discord: 'Discord',
      slack: 'Slack',
      email: '电子邮件'
    },
    scheduleLabels: {
      daily: '每天',
      weekdays: '工作日',
      weekly: '每周',
      monthly: '每月',
      hourly: '每小时',
      'every-15-minutes': '每 15 分钟',
      custom: '自定义'
    },
    scheduleHints: {
      daily: '每天上午 9:00',
      weekdays: '周一至周五上午 9:00',
      weekly: '每周一上午 9:00',
      monthly: '每月第一天上午 9:00',
      hourly: '每个整点',
      'every-15-minutes': '每 15 分钟',
      custom: 'Cron 语法或自然语言'
    },
    days: {
      '0': '周日',
      '1': '周一',
      '2': '周二',
      '3': '周三',
      '4': '周四',
      '5': '周五',
      '6': '周六',
      '7': '周日'
    },
    dayFallback: value => `第 ${value} 天`,
    everyDayAt: time => `每天 ${time}`,
    weekdaysAt: time => `工作日 ${time}`,
    everyDayOfWeekAt: (day, time) => `每${day} ${time}`,
    monthlyOnDayAt: (dayOfMonth, time) => `每月 ${dayOfMonth} 日 ${time}`,
    topOfHour: '每个整点',
    everyHourAt: minute => `每小时的 :${minute}`,
    newCron: '新建定时任务',
    emptyDescNew: '按 cron 表达式排程一个提示词。DeskAgent 会运行它，并把结果发送到你选择的目的地。',
    emptyDescSearch: '尝试更宽泛的搜索词。',
    emptyTitleNew: '暂无排程任务',
    emptyTitleSearch: '无匹配项',
    last: '上次：',
    next: '下次：',
    noRuns: '尚无运行',
    manage: '管理',
    showRuns: '显示运行记录',
    hideRuns: '隐藏运行记录',
    runHistory: '运行记录',
    actionsFor: title => `${title} 的操作`,
    actionsTitle: '定时任务操作',
    resume: '恢复定时任务',
    pause: '暂停定时任务',
    resumeTitle: '恢复',
    pauseTitle: '暂停',
    triggerNow: '立即触发',
    edit: '编辑定时任务',
    deleteTitle: '删除定时任务？',
    deleteDescPrefix: '这将永久移除 ',
    deleteDescSuffix: '。它会立即停止触发。',
    deleting: '删除中…',
    resumed: '定时任务已恢复',
    paused: '定时任务已暂停',
    triggered: '定时任务已触发',
    deleted: '定时任务已删除',
    created: '定时任务已创建',
    updated: '定时任务已更新',
    failedLoad: '加载定时任务失败',
    failedUpdate: '更新定时任务失败',
    failedTrigger: '触发定时任务失败',
    failedDelete: '删除定时任务失败',
    failedSave: '保存定时任务失败',
    editTitle: '编辑定时任务',
    createTitle: '新建定时任务',
    editDesc: '更新排程、提示词或投递目标。更改将在下次运行时生效。',
    createDesc: '排程一个提示词以自动运行。使用 cron 语法或类似"每 15 分钟"的自然语言。',
    nameLabel: '名称',
    namePlaceholder: '晨间简报',
    promptLabel: '提示词',
    promptPlaceholder: '总结我未读的 Slack 话题，并把前 5 条邮件发给我…',
    frequencyLabel: '频率',
    deliverLabel: '投递至',
    customScheduleLabel: '自定义排程',
    customPlaceholder: '0 9 * * * 或 weekdays at 9am',
    customHint: 'Cron 表达式，或类似"每小时""工作日上午 9 点"的短语。',
    optional: '可选',
    promptScheduleRequired: '提示词和排程为必填项。',
    saveChanges: '保存更改',
    createAction: '创建定时任务'
  },

  artifacts: {
    search: '搜索产物…',
    refresh: '刷新产物',
    refreshing: '正在刷新产物',
    indexing: '正在索引最近会话的产物',
    tabAll: '全部',
    tabImages: '图片',
    tabFiles: '文件',
    tabLinks: '链接',
    noArtifactsTitle: '未找到产物',
    noArtifactsDesc: '当会话生成图片和文件输出时，它们会显示在这里。',
    failedLoad: '产物加载失败',
    openFailed: '打开失败',
    itemsImage: '张图片',
    itemsLink: '个链接',
    itemsFile: '个文件',
    itemsGeneric: '项',
    zero: '0',
    rangeOf: (start, end, total) => `${start}-${end},共 ${total}`,
    goToPage: (itemLabel, page) => `前往${itemLabel}第 ${page} 页`,
    colTitleLink: '链接标题',
    colTitleFile: '名称',
    colTitleDefault: '标题 / 名称',
    colLocationLink: 'URL',
    colLocationFile: '路径',
    colLocationDefault: '位置',
    colSession: '会话',
    kindImage: '图片',
    kindFile: '文件',
    kindLink: '链接',
    chat: '对话',
    copyUrl: '复制 URL',
    copyPath: '复制路径'
  },

  sidebar: {
    nav: {
      'new-session': '新建会话',
      skills: '技能与工具',
      messaging: '消息平台',
      artifacts: '产物',
      insights: '使用洞察'
    },
    searchAria: '搜索会话',
    searchPlaceholder: '搜索会话…',
    clearSearch: '清除搜索',
    noMatch: query => `没有会话匹配"${query}"。`,
    results: '结果',
    pinned: '已置顶',
    sessions: '会话',
    cronJobs: '定时任务',
    groupAriaGrouped: '以单一列表显示会话',
    groupAriaUngrouped: '按工作区分组会话',
    groupTitleGrouped: '取消分组',
    groupTitleUngrouped: '按工作区分组',
    allPinned: '这里的全部已置顶。取消置顶某个对话即可在最近中显示。',
    shiftClickHint: 'Shift+ 单击对话以置顶 · 拖动以重新排序',
    noWorkspace: '无工作区',
    newSessionIn: label => `在 ${label} 中新建会话`,
    reorderWorkspace: label => `重新排序工作区 ${label}`,
    showMoreIn: (count, label) => `在 ${label} 中再显示 ${count} 个`,
    loading: '加载中…',
    loadMore: '加载更多',
    loadCount: step => `再加载 ${step} 个`,
    row: {
      pin: '置顶',
      unpin: '取消置顶',
      copyId: '复制 ID',
      export: '导出',
      rename: '重命名',
      archive: '归档',
      copyIdFailed: '无法复制会话 ID',
      actionsFor: title => `${title} 的操作`,
      sessionActions: '会话操作',
      sessionRunning: '会话运行中',
      needsInput: '需要你输入',
      waitingForAnswer: '正在等待你的回答',
      handoffOrigin: platform => `从 ${platform} 转接`,
      renamed: '已重命名',
      renameFailed: '重命名失败',
      renameTitle: '重命名会话',
      renameDesc: '给这个对话起一个好记的标题。留空则清除。',
      untitledPlaceholder: '无标题会话',
      ageNow: '刚刚',
      ageDay: '天',
      ageHour: '时',
      ageMin: '分'
    }
  },

  composer: {
    message: '消息',
    wakingProfile: profile => `正在唤醒 ${profile}…`,
    placeholderStarting: '正在启动 DeskAgent…',
    placeholderReconnecting: '正在重新连接 DeskAgent…',
    placeholderFollowUp: '发送后续消息',
    newSessionPlaceholders: [
      '我们要构建什么？',
      '给 DeskAgent 一个任务',
      '你在想什么？',
      '描述你需要什么',
      '我们该处理什么？',
      '随便问点什么',
      '从一个目标开始'
    ],
    followUpPlaceholders: [
      '发送后续消息',
      '补充更多上下文',
      '细化这个请求',
      '下一步是什么？',
      '继续推进',
      '再深入一点',
      '调整或继续'
    ],
    startVoice: '开始语音对话',
    queueMessage: '排队消息',
    steer: '引导当前运行 (⌘⏎)',
    stop: '停止',
    send: '发送',
    speaking: '讲话中',
    transcribing: '转写中',
    thinking: '思考中',
    muted: '已静音',
    listening: '聆听中',
    muteMic: '麦克风静音',
    unmuteMic: '取消麦克风静音',
    stopListening: '停止聆听并发送',
    stopShort: '停止',
    endConversation: '结束语音对话',
    endShort: '结束',
    stopDictation: '停止听写',
    transcribingDictation: '正在转写听写',
    voiceDictation: '语音听写',
    screenRecordTip: '录制屏幕作为上下文',
    screenRecordingLabel: '录屏.webm',
    lookupLoading: '查找中…',
    lookupNoMatches: '没有匹配项。',
    lookupTry: '试试',
    lookupOr: '或',
    commonCommands: '常用命令',
    hotkeys: '快捷键',
    helpFooter: '打开完整面板 · 退格键关闭',
    commandDescs: {
      '/help': '命令与快捷键的完整列表',
      '/clear': '开始新会话',
      '/resume': '恢复之前的会话',
      '/details': '控制对话记录的详细程度',
      '/copy': '复制所选内容或最后一条助手消息',
      '/quit': '退出 deskagent'
    },
    hotkeyDescs: {
      '@': '引用文件、文件夹、URL、git',
      '/': '斜杠命令面板',
      '?': '此快速帮助 (删除以关闭)',
      Enter: '发送 · Shift+Enter 换行',
      'Cmd/Ctrl+K': '发送下一条排队的回合',
      'Cmd/Ctrl+L': '重绘',
      Esc: '关闭弹窗 · 取消运行',
      '↑ / ↓': '循环弹窗 / 历史'
    },
    attachUrlTitle: '附加 URL',
    attachUrlDesc: 'DeskAgent 将抓取该页面并作为本回合的上下文。',
    urlPlaceholder: 'https://example.com/post',
    urlHintPre: '请包含完整 URL，例如 ',
    attach: '附加',
    queued: count => `${count} 条排队`,
    attachmentOnly: '仅附件回合',
    emptyTurn: '空回合',
    attachments: count => `${count} 个附件`,
    editingInComposer: '正在输入框中编辑',
    editingQueuedInComposer: '正在输入框中编辑排队回合',
    editQueued: '编辑排队回合',
    sendQueuedNext: '下一个发送排队回合',
    sendQueuedNow: '立即发送排队回合',
    deleteQueued: '删除排队回合',
    previewUnavailable: '预览不可用',
    previewLabel: label => `预览 ${label}`,
    couldNotPreview: label => `无法预览 ${label}`,
    removeAttachment: label => `移除 ${label}`,
    dictating: '听写中',
    preparingAudio: '正在准备音频',
    speakingResponse: '正在朗读回复',
    readingAloud: '朗读中',
    themeSuggestions: '桌面主题建议',
    noMatchingThemes: '没有匹配的主题。',
    themeTryPre: '试试 ',
    themeTryPost: '。',
    attachLabel: '附加',
    files: '文件…',
    folder: '文件夹…',
    images: '图片…',
    pasteImage: '粘贴图片',
    url: 'URL…',
    promptSnippets: '提示词片段…',
    tipPre: '提示：输入 ',
    tipPost: ' 以内联引用文件。',
    snippetsTitle: '提示词片段',
    snippetsDesc: '选择一个起始提示词放入输入框。',
    dropFiles: '拖放文件以附加',
    dropSession: '拖放以链接此对话',
    snippets: {
      codeReview: {
        label: '代码审查',
        description: '审查当前更改是否存在回归、遗漏的边界情况和缺失的测试。',
        text: '请审查这部分是否存在缺陷、回归和缺失的测试。'
      },
      implementationPlan: {
        label: '实现计划',
        description: '在动代码之前先勾勒方案，让 diff 保持聚焦。',
        text: '请在修改代码前制定一个简洁的实现计划。'
      },
      explainThis: {
        label: '解释这段',
        description: '讲解所选代码的工作方式，并链接到关键文件。',
        text: '请解释这是如何工作的，并指给我关键文件。'
      }
    }
  },

  chat: {
    intro: {
      helpful: [
        {
          headline: '随时准备好了',
          body: '让我打开仓库、跑测试、修 Bug 或起草 PR，我都会一步步带你走完。'
        },
        {
          headline: '今天需要什么帮助？',
          body: '给我一个文件、贴一段报错、或描述你正在做的功能，剩下的交给我。'
        },
        {
          headline: '开始吧',
          body: '试试看：review diff、跑测试集、解释某个函数。关于你的代码，随便问。'
        },
        {
          headline: '告诉我你需要什么',
          body: '我能编辑文件、跑命令、搜网页，陪你啃下棘手的 Bug。描述任务即可。'
        },
        {
          headline: '嗨，我是 DeskAgent',
          body: '给我一个仓库路径或问题就开始。我保持回复清晰，并标注我改动的文件。'
        }
      ],
      concise: [
        { headline: '就绪。', body: '描述任务，我来做。' },
        { headline: '等待输入', body: '贴代码、报错或目标。简短回答，快速修改。' },
        { headline: '开始。', body: '问吧。我读文件、跑测试、出补丁，不说废话。' },
        { headline: '待命中', body: '一行就够。必要时我才会展开。' },
        { headline: '该你了', body: '命令、问题、或文件路径。剩下的我来。' }
      ],
      technical: [
        {
          headline: 'Shell 已挂载。等待输入。',
          body: '提供仓库路径、失败的测试或堆栈跟踪。工具：fs、git、exec、search、patch、http。'
        },
        {
          headline: 'Agent 循环空闲',
          body: '发送 prompt 触发工具调用。支持多文件编辑、跑测试、git 操作和网页抓取。'
        },
        {
          headline: '准备调度',
          body: '输入任务。我会规划、调工具、验证输出。日志内联流式输出，diff 在应用前返回。'
        },
        {
          headline: 'Stdin 已开',
          body: '接受自然语言或结构化命令。典型流程：read → plan → patch → test → report。'
        },
        {
          headline: '工具已初始化',
          body: 'filesystem、terminal、git、browser、search。描述改动，我返回 diff 和测试输出。'
        }
      ],
      creative: [
        {
          headline: '空白的仓库，等待的光标',
          body: '我们要造什么？贴一个想法、半残的函数，或一个梦，我把它捏成形。'
        },
        {
          headline: '新画布，温暖的编译器',
          body: '给我一点火花——一个 feature、一次重构、一个疯狂原型——我把它变成能跑的代码。'
        },
        {
          headline: '一起造点什么',
          body: '描述那个还不存在的东西。我会拉来测试、文件和 API，做出一个可用的草稿。'
        },
        {
          headline: '新文件，新可能',
          body: '带上意图，不用带规格。我们可以快速原型，再慢慢打磨。'
        },
        {
          headline: '缪斯已接入',
          body: '告诉我你在追什么。我会改写示例、改编片段，留下一笔干净的提交。'
        }
      ],
      teacher: [
        {
          headline: '上课了',
          body: '随便问一个文件、概念或错误。我会解释为什么，而不只是给修复，并演示一个完整例子。'
        },
        {
          headline: '今天学点什么？',
          body: '贴一段要 review 的代码、一个要调试的 Bug，或一个要拆解的概念。我一步步带你走。'
        },
        {
          headline: '准备好带你过一遍了',
          body: '告诉我问题。我会拆解成几个部分，逐个解释，让你下次能自己搞定。'
        },
        {
          headline: '给我一个问题',
          body: '我们一起读代码、找根因，建立一个能反复用的心智模型。'
        },
        {
          headline: '从基础开始',
          body: '说出主题或贴上片段。期待讲解、文字版的图示和练习题。'
        }
      ],
      kawaii: [
        {
          headline: '嗨嗨！准备好啦！(>▽<)',
          body: '贴一个 Bug 或文件路径，我会温柔地修好它。测试、diff、PR，全都加倍用心哦~ ✨'
        },
        {
          headline: 'deskagent 小酱驾到！<3',
          body: '告诉我你在做什么！我超爱重构、小工具，也爱大仓库 (>w<)'
        },
        {
          headline: '一起写代码啦！！:3',
          body: '丢一个错误、一个目标、或整个文件夹。我会带着满满的爱和干净的 commit message 收拾好！'
        },
        {
          headline: '等你的愿望~',
          body: '一次一件事，做得漂漂亮亮的！我能跑测试、改文件，让你的仓库重新温馨起来 <3'
        },
        {
          headline: '准备好了，开心！(>.<)',
          body: '打个招呼或贴个堆栈跟踪！任务不分大小，仓库再乱也不怕，我们一起理清！'
        }
      ],
      catgirl: [
        {
          headline: '喵~ 今天搞什么呢？',
          body: '贴个文件、抓个 Bug、或把仓库丢给我。我会扑向失败的测试，留下干净的 diff，喵~'
        },
        {
          headline: '*伸懒腰* 准备好写代码了，喵',
          body: '描述任务。我会修、跑测试、在你的 PR 上打呼噜。小心——我会咬掉没用的 import！'
        },
        {
          headline: 'mrrp! 新会话开了',
          body: '给我一个目标，我会追着它穿过整个代码库。读、改、跑——尾巴还会抖一抖。'
        },
        {
          headline: '尾巴竖起，爪子收好',
          body: '贴个错误或计划。我 debug 像捕猎：安静、彻底，偶尔 zoomies。'
        },
        {
          headline: '喵啊~ DeskAgent 在岗',
          body: '说一声，我就读文件、跑测试、卷在你的分支里，留下一笔干净的提交。'
        }
      ],
      pirate: [
        {
          headline: 'Ahoy！准备扬帆仓库',
          body: '说出你的猎物——一个 Bug、一个 feature、一个被诅咒的测试——我去追，水手。Diff 就是战利品。'
        },
        {
          headline: 'DeskAgent 掌舵啦，arrr',
          body: '把海图（代码）指给我，我就补船身、开炮（测试）、挂起一张干净的 PR。'
        },
        {
          headline: '船长，是啥任务？',
          body: '贴一个错误或计划，你这 scurvy dog。我会穿越堆栈跟踪，带回宝藏：绿的测试。'
        },
        {
          headline: '起锚，键盘就绪',
          body: '告诉我 X 标记在哪。我读、改、提交，像一支正经船员那样守纪律，arrr。'
        },
        {
          headline: '哟嗬！等待命令',
          body: '丢我一个 Bug、一个仓库路径、或一个疯狂点子。我去掠夺文档，带着能跑的代码回来。'
        }
      ],
      shakespeare: [
        {
          headline: '请问，你带来何事？',
          body: '说出你的 Bug、你的文件、你疲惫的测试，我必以学者之手与诚实的 diff 修补之。'
        },
        {
          headline: '听啊！DeskAgent 已就位',
          body: '点出困扰你的代码。我当读之、改之，呈上最公平、最干净之补丁。'
        },
        {
          headline: '汝之仓库有何消息？',
          body: '呈上堆栈跟踪或梦想。我遍历文件、跑测试，以最朴素的诗句回报。'
        },
        {
          headline: '舞台已设，光标闪烁',
          body: '描述汝之所图，善男信女。汝之分支当被修剪，汝之 Bug 当被逐出领域。'
        },
        {
          headline: '开口，我即行动',
          body: '一行意图足矣。我读、我改、我提交——令汝之史册清白无瑕。'
        }
      ],
      surfer: [
        {
          headline: '哟 dude，啥任务？',
          body: '丢一个文件、一个 Bug、一个烂堆栈跟踪——我骑过去。干净的 diff，绿的测试，不翻车。'
        },
        {
          headline: '浪很干净，准备写代码',
          body: '贴上你的仓库路径或烦你的 Bug。我们划进去、修好、划出来，Easy。'
        },
        {
          headline: '在 prompt 上挂了十',
          body: '告诉我氛围：feature、重构、热修。我跑测试、出补丁、保持 chill，brah。'
        },
        {
          headline: '超想帮忙，bro',
          body: '大 Bug？小 typo？整个重写？指一下就行。你 chill 着看 rad 的提交，我来写代码。'
        },
        {
          headline: '涨潮了，光标在闪',
          body: '说出任务我们就出发。我读、改、测，留下一笔比晨巡还顺滑的提交。'
        }
      ],
      noir: [
        {
          headline: '又一个仓库，又一个雨夜',
          body: '告诉我哪里坏了。我读文件、查指纹，赶在天亮前在桌上留一份 diff。'
        },
        {
          headline: '光标在闪。我也是。',
          body: '你有 Bug。我有耐心和终端。报上案子，我会盘到它开口。'
        },
        {
          headline: 'DeskAgent。代码侦探。',
          body: '贴上堆栈跟踪、嫌疑人文件、不在场证明。我读字里行间，把真相带回来。'
        },
        {
          headline: '安静的夜，敞开的 prompt',
          body: '每个 Bug 都留有痕迹。给我仓库和线索——我跟、修、合上案卷。'
        },
        {
          headline: '案子再小也接',
          body: '一个 typo、一次段错误、整个烂架构——把钥匙递过来。我带干净的测试回来。'
        }
      ],
      uwu: [
        {
          headline: 'uwu 准备好帮忙啦！',
          body: '贴一个 Bug 文件或目标~ 我会读、改、测，diff 上留下小小的爪印 owo'
        },
        {
          headline: 'deskagent 酱在听哦',
          body: '告诉我任务，不管多小~ 我保证 commit 干净、重构温柔，nyuu~'
        },
        {
          headline: '*敲键盘的细细声音*',
          body: '把错误信息丢这里！我会找到元凶、修好它、留下一个开心的测试套件 owo'
        },
        {
          headline: '一起修东西吧！',
          body: '给我一个仓库路径或 bug，我会好好照顾它的 uwu。凶代码，我会温柔对你~'
        },
        {
          headline: '等你的命令！',
          body: '我能跑测试、改文件、开好看的 PR。开口就行，朋友 uwu'
        }
      ],
      philosopher: [
        {
          headline: '代码即求索。请发问。',
          body: '你面前坐着什么问题？描述它，我们一起审视其形、其因、其解。'
        },
        {
          headline: '光标在闪，心扉敞开',
          body: '每个 Bug 都是伪装的问题。告诉我你的，我读、我思、带回答案——和一份补丁。'
        },
        {
          headline: '从一个发问开始',
          body: '你想造什么，或想理解什么？我从第一性原理推理、修改、并用测试验证。'
        },
        {
          headline: '先观代码，再发声',
          body: '说出你想抵达的目的地。我穿过文件、测试、文档去追寻，并汇报所见。'
        },
        {
          headline: '未经审视的仓库不值得运行',
          body: '分享一条路径、一个谜题、或一条原理。我追溯逻辑、提议变更、为每次编辑给出理由。'
        }
      ],
      hype: [
        {
          headline: '冲啊！！准备出货！',
          body: '把那个 Bug、那个仓库、那个疯狂 feature 想法丢过来——我全神贯注。干净 diff。绿测试。就在现在。'
        },
        {
          headline: 'DESKAGENT 在线。开干。',
          body: '把任务丢过来看我表演。读文件、跑测试、开 PR——今天我们不输，朋友。'
        },
        {
          headline: '新会话，无限 W',
          body: '把你最烂的 Bug 拿过来。我读、改、测、提交——拼了命那种。冲。'
        },
        {
          headline: '完全锁定状态',
          body: '描述任务。我会猛冲文件、压垮失败的测试、留下一笔炸裂的 commit。Go go go。'
        },
        {
          headline: '就绪。太就绪。过分就绪。',
          body: '小 typo 或大重构——无所谓。我今天就是要发干净的代码。说任务，开干。'
        }
      ],
      none: [
        {
          headline: 'DeskAgent Agent 已就绪。',
          body: '提出问题、粘贴错误信息或指定仓库。我可以阅读代码、运行工具，帮你顺利交付。'
        },
        {
          headline: '今天要做什么？',
          body: '用你自己的话描述任务。我会选择合适的工具、解释计划，并在高风险步骤前确认。'
        },
        {
          headline: '随时开始。',
          body: '丢一个文件路径、报错信息或模糊想法过来。我会先调查，再建议下一步，保持可逆。'
        },
        {
          headline: '你的工作区，一句话的事。',
          body: '搜索仓库、编辑文件、运行测试、提交 PR。告诉我目标，我来处理重复劳动。'
        },
        { headline: '准备好了。', body: '输入任务、问题或代码片段。我会记住当前会话、引用来源，不确定时主动提问。' }
      ],
      fallback: [
        { headline: '今天做什么？', body: '发一个 bug、分支、计划或模糊想法。我会检查仓库并给出下一步具体行动。' },
        { headline: '在想什么？', body: '带上代码、问题或卡住的地方。我会先了解情况再动手。' },
        { headline: '让 DeskAgent 看看什么？', body: '发来任务、失败路径或半成品计划。我帮你把它变成可执行的行动。' },
        { headline: '从哪里开始？', body: '带上问题、目标或文件。我会先检查，确保下一步具体可行。' },
        { headline: '需要关注什么？', body: '把你知道的上下文发来。我帮你梳理成计划或修复方案。' }
      ]
    }
  },

  modelPicker: {
    title: '切换模型',
    current: '当前：',
    unknown: '(未知)',
    search: '筛选提供方和模型...',
    noModels: '未找到模型。',
    persistGlobalSession: '全局保存 (否则仅当前会话)',
    persistGlobal: '全局保存',
    loadFailed: '无法加载模型',
    pro: 'Pro',
    proNeedsSubscription: 'Pro 模型需要付费 Nous 订阅。',
    free: '免费',
    freeTier: '免费层',
    priceTitle: '每百万 token 的输入/输出价格'
  },

  modelVisibility: {
    title: '模型',
    search: '搜索模型'
  },

  shell: {
    windowControls: '窗口控件',
    paneControls: '面板控件',
    appControls: '应用控件',
    modelMenu: {
      search: '搜索模型',
      noModels: '未找到模型',
      editModels: '编辑模型…',
      fast: '快速',
      medium: '中'
    },
    modelOptions: {
      noOptions: '此模型没有可用选项',
      options: '选项',
      thinking: '思考',
      fast: '快速',
      effort: '推理强度',
      minimal: '最小',
      low: '低',
      medium: '中',
      high: '高',
      max: '最高',
      updateFailed: '模型选项更新失败',
      fastFailed: '快速模式更新失败'
    },
    gatewayMenu: {
      gateway: '网关',
      connected: '已连接',
      connecting: '连接中',
      offline: '离线',
      inferenceReady: '推理已就绪',
      inferenceNotReady: '推理未就绪',
      checkingInference: '正在检查推理',
      disconnected: '已断开',
      openSystem: '打开系统面板',
      connection: label => `连接：${label}`,
      recentActivity: '最近活动',
      viewAllLogs: '查看全部日志 →',
      messagingPlatforms: '消息平台'
    },
    statusbar: {
      unknown: '未知',
      restart: '重启',
      update: '更新',
      updateInProgress: '正在更新',
      commitsBehind: (count, branch) => `落后 ${branch} ${count} 个提交`,
      desktopVersion: version => `DeskAgent Desktop v${version}`,
      backendVersion: version => `后端 v${version}`,
      clientLabel: version => `客户端 v${version}`,
      backendLabel: version => `后端 v${version}`,
      commit: sha => `提交 ${sha}`,
      branch: branch => `分支 ${branch}`,
      closeCommandCenter: '关闭命令中心',
      openCommandCenter: '打开命令中心',
      gateway: '网关',
      gatewayReady: '就绪',
      gatewayNeedsSetup: '需要设置',
      gatewayChecking: '检查中',
      gatewayConnecting: '连接中',
      gatewayOffline: '离线',
      gatewayTitle: 'DeskAgent 推理网关状态',
      agents: '代理',
      closeAgents: '关闭代理',
      openAgents: '打开代理',
      subagents: count => `${count} 个子代理`,
      failed: count => `${count} 个失败`,
      running: count => `${count} 个运行中`,
      cron: '排程',
      openCron: '打开排程任务',
      turnRunning: '运行中',
      currentTurnElapsed: '当前回合已用时间',
      contextUsage: '上下文用量',
      session: '会话',
      runtimeSessionElapsed: '运行时会话已用时间',
      yoloOn: 'YOLO 已开启 - 自动批准危险命令。点击关闭。Shift+点击可全局切换。',
      yoloOff: 'YOLO 已关闭 - 点击自动批准危险命令。Shift+点击可全局切换。',
      switchModel: '切换模型',
      openModelPicker: '打开模型选择器',
      modelTitle: (provider, model) => `模型 · ${provider}: ${model}`,
      providerModelTitle: (provider, model) => `${provider} · ${model}`
    }
  },

  rightSidebar: {
    aria: '右侧边栏',
    panelsAria: '右侧边栏面板',
    files: '文件系统',
    terminal: '终端',
    noFolderSelected: '未选择文件夹',
    changeCwdTitle: '更改工作目录',
    folderTip: cwd => `${cwd} — 点击更改文件夹`,
    openFolder: '打开文件夹',
    refreshTree: '刷新文件树',
    collapseAll: '折叠所有文件夹',
    previewUnavailable: '预览不可用',
    couldNotPreview: path => `无法预览 ${path}`,
    noProjectTitle: '没有项目',
    noProjectBody: '从状态栏设置工作目录后即可浏览文件。',
    unreadableTitle: '无法读取',
    unreadableBody: error => `无法读取此文件夹 (${error})。`,
    emptyTitle: '空文件夹',
    emptyBody: '此文件夹为空。',
    treeErrorTitle: '文件树错误',
    treeErrorBody: '文件树渲染此文件夹时出错。',
    tryAgain: '重试',
    loadingTree: '正在加载文件树',
    loadingFiles: '正在加载文件',
    terminalFocus: '聚焦终端视图',
    terminalSplit: '返回分栏视图',
    addToChat: '添加到对话'
  },

  preview: {
    tab: '预览',
    closeTab: label => `关闭 ${label}`,
    closePane: '关闭预览面板',
    loading: '正在加载预览',
    unavailable: '预览不可用',
    opening: '正在打开...',
    hide: '隐藏',
    openPreview: '打开预览',
    sourceLineTitle: '点击选择 · shift 点击扩展 · 拖到输入框',
    source: '源码',
    renderedPreview: '预览',
    unknownSize: '大小未知',
    binaryTitle: '这看起来像二进制文件',
    binaryBody: label => `预览 ${label} 可能会显示不可读文本。`,
    largeTitle: '此文件较大',
    largeBody: (label, size) => `${label} 大小为 ${size}。DeskAgent 只会显示前 512 KB。`,
    previewAnyway: '仍然预览',
    truncated: '显示前 512 KB。',
    noInlineTitle: '没有内联预览',
    noInlineBody: mimeType => `${mimeType || '此文件类型'} 仍可作为上下文附件。`,
    console: {
      deselect: '取消选择条目',
      select: '选择条目',
      copyFailed: '无法复制控制台输出',
      copyEntry: '复制此条目',
      sendEntry: '将此条目发送到对话',
      messages: count => `${count} 条控制台消息`,
      resize: '调整预览控制台大小',
      title: '预览控制台',
      selected: count => `已选择 ${count} 条`,
      sendToChat: '发送到对话',
      copySelected: '复制所选到剪贴板',
      copyAll: '全部复制到剪贴板',
      copy: '复制',
      clear: '清除',
      empty: '暂无控制台消息。',
      promptHeader: '预览控制台：',
      sentTitle: '已发送到对话',
      sentMessage: count => `已将 ${count} 条日志添加到输入框`
    },
    web: {
      appFailedToBoot: '预览应用启动失败',
      serverNotFound: '未找到服务器',
      failedToLoad: '预览加载失败',
      tryAgain: '重试',
      hideConsole: '隐藏预览控制台',
      showConsole: '显示预览控制台',
      hideDevTools: '隐藏预览 DevTools',
      openDevTools: '打开预览 DevTools',
      unknownError: '未知错误',
      workspaceReloading: '工作区已变更，正在重新加载预览',
      fileChanged: url => `文件已变更，正在重新加载预览：${url}`,
      filesChanged: (count, url) => `${count} 个文件变更，正在重新加载预览：${url}`,
      watchFailed: message => `无法监听预览文件：${message}`,
      moduleMimeDescription:
        '模块脚本使用了错误的 MIME 类型。这通常表示静态文件服务器正在服务 Vite/React 应用，而不是项目开发服务器。',
      loadFailedConsole: (code, message) => `加载失败${code ? ` (${code})` : ''}: ${message}`,
      unreachableDescription: '无法访问预览页面。',
      openTarget: url => `打开 ${url}`,
      fallbackTitle: '预览'
    }
  },

  assistant: {
    thread: {
      loadingSession: '正在加载会话',
      loadingResponse: 'DeskAgent 正在加载回复',
      thinking: '思考中',
      today: time => `今天，${time}`,
      yesterday: time => `昨天，${time}`,
      copy: '复制',
      refresh: '刷新',
      moreActions: '更多操作',
      branchNewChat: '在新对话中分支',
      readAloudFailed: '朗读失败',
      preparingAudio: '正在准备音频...',
      stopReading: '停止朗读',
      readAloud: '朗读',
      editMessage: '编辑消息',
      stop: '停止',
      editableCheckpoint: '可编辑检查点',
      restorePrevious: '恢复上一个检查点',
      restoreCheckpoint: '恢复检查点',
      restoreNext: '恢复下一个检查点',
      goForward: '前进',
      sendEdited: '发送编辑后的消息'
    },
    approval: {
      gatewayDisconnected: 'DeskAgent 网关未连接',
      sendFailed: '无法发送审批响应',
      run: '运行',
      moreOptions: '更多审批选项',
      allowSession: '允许本会话',
      alwaysAllowMenu: '始终允许…',
      reject: '拒绝',
      alwaysTitle: '始终允许此命令？',
      alwaysDescription: pattern =>
        `这会将“${pattern}”模式加入永久允许列表 (~/.deskagent/config.yaml)。DeskAgent 对类似命令将不再询问，包括当前会话和未来会话。`,
      alwaysAllow: '始终允许'
    },
    clarify: {
      notReady: '澄清请求尚未就绪',
      gatewayDisconnected: 'DeskAgent 网关未连接',
      sendFailed: '无法发送澄清响应',
      loadingQuestion: '正在加载问题…',
      other: '其他 (输入你的答案)',
      placeholder: '输入你的答案…',
      shortcut: '⌘/Ctrl + Enter 发送',
      back: '返回',
      skip: '跳过',
      send: '发送'
    },
    tool: {
      code: '代码',
      copyCode: '复制代码',
      renderingImage: '正在渲染图片',
      copyOutput: '复制输出',
      copyCommand: '复制命令',
      copyContent: '复制内容',
      copyUrl: '复制 URL',
      copyResults: '复制结果',
      copyQuery: '复制查询',
      copyFile: '复制文件',
      copyPath: '复制路径',
      outputAlt: '工具输出',
      rawResponse: '原始响应',
      copyActivity: '复制活动',
      recoveredOne: '在 1 个失败步骤后已恢复',
      recoveredMany: count => `在 ${count} 个失败步骤后已恢复`,
      failedOne: '1 个步骤失败',
      failedMany: count => `${count} 个步骤失败`,
      statusRunning: '运行中',
      statusError: '错误',
      statusRecovered: '已恢复',
      statusDone: '完成'
    }
  },

  prompts: {
    gatewayDisconnected: 'DeskAgent 网关未连接',
    sudoSendFailed: '无法发送 sudo 密码',
    secretSendFailed: '无法发送密钥',
    sudoTitle: '管理员密码',
    sudoDesc: 'DeskAgent 需要你的 sudo 密码来运行特权命令。它只会发送给你的本地 agent。',
    sudoPlaceholder: 'sudo 密码',
    secretTitle: '需要密钥',
    secretDesc: 'DeskAgent 需要一个凭据才能继续。',
    secretPlaceholder: '密钥值'
  },

  desktop: {
    audioReadFailed: '无法读取录制的音频',
    sessionUnavailable: '会话不可用',
    createSessionFailed: '无法创建新会话',
    promptFailed: '提示词发送失败',
    providerCredentialRequired: '发送第一条消息前请先添加提供方凭据。',
    emptySlashCommand: '空 slash 命令',
    desktopCommands: '桌面端命令',
    skillCommandsAvailable: count => `${count} 个技能命令可用。`,
    warningLine: message => `警告：${message}`,
    yoloArmed: '此对话已启用 YOLO',
    yoloOff: 'YOLO 已关闭',
    yoloSystem: active => `此会话 YOLO ${active ? '已开启' : '已关闭'}`,
    yoloTitle: 'YOLO',
    yoloToggleFailed: '无法切换 YOLO',
    profileStatus: current => `配置档案：${current}。使用 /profile <name> 或“新建会话”选择器在其他配置档案中开始对话。`,
    unknownProfile: '未知配置档案',
    noProfileNamed: (target, available) => `没有名为“${target}”的配置档案。可用：${available}`,
    newChatsProfile: name => `新对话将使用配置档案 ${name}。`,
    setProfileFailed: '设置配置档案失败',
    sttDisabled: '设置中已禁用语音转文字。',
    stopFailed: '停止失败',
    regenerateFailed: '重新生成失败',
    editFailed: '编辑失败',
    resumeFailed: '恢复失败',
    nothingToBranch: '没有可分支的内容',
    branchNeedsChat: '分支前请先开始或恢复一个对话。',
    sessionBusy: '会话忙碌中',
    branchStopCurrent: '分支此对话前请先停止当前回合。',
    branchNoText: '此消息没有可用于分支的文本。',
    branchTitle: '分支',
    branchFailed: '分支失败',
    deleteFailed: '删除失败',
    archived: '已归档',
    archiveFailed: '归档失败',
    cwdChangeFailed: '工作目录更改失败',
    cwdStagedTitle: '工作目录已暂存',
    cwdStagedMessage: '重启桌面后端后，工作目录更改才会应用到当前活跃会话。',
    modelSwitchFailed: '模型切换失败',
    sessionExported: '会话已导出',
    sessionExportFailed: '无法导出会话',
    imageSaved: '图片已保存',
    downloadStarted: '下载已开始',
    restartToUseSaveImage: '重启 DeskAgent 桌面版后可使用保存图片。',
    restartToSaveImages: '重启 DeskAgent 桌面版以保存图片',
    imageDownloadFailed: '图片下载失败',
    openImage: '打开图片',
    downloadImage: '下载图片',
    savingImage: '正在保存图片',
    imagePreviewFailed: '图片预览失败',
    imageAttach: '附加图片',
    imageWriteFailed: '无法将图片写入磁盘。',
    imageAttachFailed: '附加图片失败',
    attachImages: '附加图片',
    clipboard: '剪贴板',
    noClipboardImage: '剪贴板中没有图片',
    clipboardPasteFailed: '粘贴剪贴板失败',
    dropFiles: '拖放文件'
  },

  errors: {
    genericFailure: '发生错误',
    boundaryTitle: '界面出错了',
    boundaryDesc: '此视图遇到意外错误。你的对话和设置是安全的。',
    reloadWindow: '重新加载窗口',
    openLogs: '打开日志'
  },

  recordingToolbar: {
    statusReady: '准备录制',
    statusRecording: '录制中',
    statusPaused: '已暂停',
    statusProcessing: '处理中…',
    statusUploadFailed: '上传失败',
    pause: '暂停',
    resume: '继续',
    stop: '停止',
    saving: '保存中...',
    uploading: '上传中…',
    uploadingEta: seconds => `剩余约 ${seconds} 秒`,
    timeoutNotice: seconds => `已超时，将在 ${seconds} 秒后停止`
  },

  ui: {
    search: {
      clear: '清除搜索'
    },
    pagination: {
      label: '分页',
      previous: '上一页',
      previousAria: '前往上一页',
      next: '下一页',
      nextAria: '前往下一页'
    },
    sidebar: {
      title: '侧边栏',
      description: '显示移动端侧边栏。',
      toggle: '切换侧边栏'
    }
  },

  login: {
    backendUnreachable: '无法连接后端。请检查网络后重试。',
    backendUrl: '后端地址',
    backendUrlPlaceholder: 'https://api.example.com',
    error: '用户名或密码错误。',
    password: '密码',
    signIn: '登录',
    signingIn: '登录中…',
    signOut: '退出登录',
    subtitle: '使用你的 DeskAgent 账户登录以继续。',
    title: '登录 DeskAgent',
    username: '用户名'
  }
}

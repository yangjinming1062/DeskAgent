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
    confirm: '确认',
    connect: '连接',
    connecting: '连接中',
    continue: '继续',
    copied: '已复制',
    copy: '复制',
    copyFailed: '复制失败',
    delete: '删除',
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
      startingDesktopConnection: '正在启动桌面连接',
      startingSpiritAgentDesktop: '正在启动 SpiritAgent 桌面版…'
    },
    errors: {
      desktopBootFailed: '桌面启动失败'
    },
    failure: {
      title: 'SpiritAgent 无法启动',
      description: '后台网关没有启动。请尝试下面的恢复步骤；这里不会删除你的对话或设置。',
      retry: '重试'
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
    title: '应用设置',
    closeSettings: '关闭设置',
    nav: {
      inference: '推理与对话',
      about: '关于',
      appearance: '外观',
      channels: '聊天通道',
      runner: '本机执行器',
      shortcuts: '快捷键',
      skills: '技能与工具'
    },
    shortcuts: {
      heading: '全局快捷键',
      intro: '在系统任意界面通过全局快捷键唤起或隐藏伴侣。点击按键框即可录制新组合。',
      toggleVisibility: '隐藏 / 显示伴侣',
      toggleVisibilityDesc: '快速在桌面显示或隐藏伙伴窗口。',
      toggleChat: '打开 / 关闭对话窗',
      toggleChatDesc: '快速展开或收起对话输入面板。',
      pressKeysPrompt: '请按下组合键…',
      pressKeysHint: '按 Esc 取消录制，按 Backspace 或 Delete 清空',
      resetAll: '恢复全部默认',
      resetAllSuccess: '已恢复默认快捷键',
      conflictError: '快捷键已被系统或其他应用占用',
      empty: '未设置'
    },
    channels: {
      heading: '聊天通道',
      intro: '让同一个伙伴在微信等 IM 上陪聊——人设与记忆和桌面共享，桌面端可回看但不可代答。',
      loadFailed: '通道状态加载失败',
      statusLabels: {
        connected: '已连接',
        login_pending: '等待扫码',
        login_required: '需重新登录',
        error: '异常',
        disabled: '未启用'
      } as Record<string, string>,
      weixin: {
        title: '微信',
        intro: '扫码登录你的微信个人号（官方 ClawBot 通道）。伙伴只能回复消息，不能主动发起。',
        loginAction: '扫码登录',
        retryAction: '重新获取二维码',
        logoutAction: '退出登录',
        logoutConfirmTitle: '退出微信登录？',
        logoutConfirmDescription: '退出后伙伴将不再回复微信消息，重新登录需要再次扫码。',
        loginStartFailed: '登录启动失败',
        loginSuccess: '微信已连接',
        logoutSuccess: '已退出微信登录',
        logoutFailed: '退出失败',
        qrPrompt: '打开微信扫一扫',
        scanedPrompt: '已扫码，请在手机上确认',
        expiredPrompt: '二维码已过期，请重新获取',
        connectedAs: (name: string) => `已连接${name ? `：${name}` : ''}`
      },
      peers: {
        title: '对端审批',
        intro: '陌生对端首次来信会收到配对提示，批准后才能与伙伴对话；被拉黑者静默。',
        empty: '暂无对端记录',
        approve: '批准',
        block: '拉黑',
        remove: '删除',
        pendingLabel: '待审批',
        allowedLabel: '已批准',
        blockedLabel: '已拉黑',
        actionFailed: '操作失败',
        requestToast: (channel: string, peer: string) => `${channel}上有人想和伙伴聊天：${peer}`
      }
    },
    appearance: {
      heading: '外观',
      hint: '主题同时应用到聊天与设置两个窗口，伙伴形象不受影响。'
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
      ssh: 'SSH 连接',
      sshHost: '主机地址',
      sshPort: '端口',
      sshUser: '用户名',
      sshPassword: '密码',
      sshKey: '私钥路径',
      security: '安全',
      securityRedactSecrets: '屏蔽敏感信息',
      browser: '浏览器设置',
      browserAllowPrivateUrls: '允许内网访问',
      debug: '调试开关',
      debugInterrupt: '中断模式'
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
    inference: {
      heading: '推理与对话',
      loading: '加载中…',
      saveFailed: '无法保存推理与对话设置。',
      saved: '推理与对话设置已保存。',
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
      temperature: {
        heading: '模型温度',
        intro:
          '控制不同场景下的模型输出随机性与创造力。界面统一使用 0–1 刻度，发起请求时会自动映射到当前供应商的实际范围。',
        chatTemperature: '主对话温度',
        chatTemperatureDesc: '日常聊天的生成温度。较低的值更严谨稳定，较高的值更发散有创意。',
        titleTemperature: '标题生成温度',
        titleTemperatureDesc: '根据首轮对话自动生成会话标题的温度。建议保持较低以保证概括准确性。',
        compressionTemperature: '上下文压缩温度',
        compressionTemperatureDesc: '对话历史超长时生成记忆摘要的温度。建议保持 0 以保证事实忠实度。'
      }
    }
  },

  speech: {
    title: '语音输入',
    intro: '语音输入与录音设置',
    loading: '加载中…',
    sttEnabledTitle: '语音转文字总开关',
    sttEnabledDesc: '关闭后语音条不可用。',
    recordingTitle: '录音时长上限',
    recordingDesc: '单条语音录音的最大时长（秒）',
    save: '保存',
    saving: '保存中…',
    saved: '已保存',
    saveFailed: '保存失败'
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
    messaging: { label: '消息', description: '通过 Webhook 发送消息。' },
    scheduled_tasks: { label: '定时任务', description: 'Cron 触发与周期调度。' },
    agent_delegation: { label: '子代理委托', description: '派生子会话与子代理。' },
    computer_use: { label: '桌面操控', description: '通过 Windows 后端接管桌面。' },
    media_analysis: { label: '多媒体分析', description: '图片分析。' }
  },

  errors: {
    boundaryTitle: '界面出错了',
    boundaryDesc: '此视图遇到意外错误。你的对话和设置是安全的。',
    reloadWindow: '重新加载窗口'
  },

  ui: {
    search: {
      clear: '清除搜索'
    }
  },

  chat: {
    presetPicker: {
      title: '选择新对话的预设',
      intro: '这条对话的系统提示词将从这里固定；选定后仍可改名、归档或删除。',
      confirm: '创建',
      cancel: '取消',
      fetchFailed: '加载预设失败，请稍后再试',
      pickOne: '请先选择一个预设'
    },

    sessionRename: {
      action: '重命名',
      inputLabel: '对话名称',
      placeholder: '输入对话名称',
      hint: 'Enter 保存 · Esc 取消',
      forbidden: '系统预设对话不能改名',
      failed: '改名失败，已恢复原名称'
    }
  }
}

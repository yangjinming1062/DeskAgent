// Desktop i18n type contract.
//
// `Translations` is the single source of truth for every translatable string
// surface. Fully translated locale files may satisfy this interface directly;
// missing keys in any active locale silently fall back to the canonical
// `DEFAULT_LOCALE` (English) — see `runtime.translateNow`.

export type Locale = 'en' | 'zh'

import type { ToolsetId } from '@/shared/lib/toolset-catalog'

interface ModeOptionCopy {
  label: string
  description: string
}

interface AuxTaskCopy {
  label: string
  hint: string
}

interface IntroCopy {
  headline: string
  body: string
}

export interface Translations {
  common: {
    apply: string
    back: string
    save: string
    saving: string
    cancel: string
    change: string
    choose: string
    clear: string
    close: string
    collapse: string
    confirm: string
    connect: string
    connecting: string
    continue: string
    copied: string
    copy: string
    copyFailed: string
    delete: string
    docs: string
    done: string
    error: string
    failed: string
    free: string
    loading: string
    notSet: string
    refresh: string
    remove: string
    replace: string
    retry: string
    run: string
    send: string
    set: string
    skip: string
    update: string
    on: string
    off: string
  }

  boot: {
    ready: string
    desktopBootFailedWithMessage: (message: string) => string
    steps: {
      connectingGateway: string
      loadingSettings: string
      loadingSessions: string
      startingDesktopConnection: string
      startingDeskAgentDesktop: string
    }
    errors: {
      backgroundExited: string
      backgroundExitedDuringStartup: string
      backendStopped: string
      desktopBootFailed: string
      gatewaySignInRequired: string
      ipcBridgeUnavailable: string
    }
    failure: {
      title: string
      description: string
      retry: string
      openLogs: string
      retryHint: string
      hideRecentLogs: string
      showRecentLogs: string
    }
  }

  notifications: {
    region: string
    hide: string
    show: string
    more: (count: number) => string
    clearAll: string
    dismiss: string
    details: string
    copyDetail: string
    copyDetailFailed: string
    updateReadyMessage: (count: number) => string
    errors: {
      elevenLabsNeedsKey: string
      elevenLabsRejectedKey: string
      methodNotAllowed: string
      microphonePermission: string
      openaiRejectedApiKey: string
      openaiRejectedApiKeyWithStatus: (status: string) => string
      openaiTtsNeedsKey: string
    }
    voice: {
      configureSpeechToText: string
      couldNotStartSession: string
      microphoneAccessDenied: string
      microphoneConstraintsUnsupported: string
      microphoneFailed: string
      microphoneInUse: string
      microphonePermissionDenied: string
      microphoneStartFailed: string
      microphoneUnsupported: string
      noMicrophone: string
      noSpeechDetected: string
      playbackFailed: string
      recordingFailed: string
      transcriptionFailed: string
      transcriptionUnavailable: string
      tryRecordingAgain: string
      unavailable: string
    }
    events: {
      referencesTitle: string
      referencesMessage: (items: string) => string
      compressionTimeoutTitle: string
      compressionTimeoutMessage: string
      cronTriggeredTitle: string
      cronTriggeredMessage: (name: string | null, jobId: string | number) => string
      backgroundReviewFailedTitle: string
      backgroundReviewFailedMessage: (error: string | null) => string
    }
  }

  titlebar: {
    hideSidebar: string
    showSidebar: string
    search: string
    searchTitle: string
    swapSidebarSides: string
    swapSidebarSidesTitle: string
    hideRightSidebar: string
    showRightSidebar: string
    muteHaptics: string
    unmuteHaptics: string
    openSettings: string
    openKeybinds: string
  }

  keybinds: {
    title: string
    subtitle: (open: string) => string
    rebind: string
    reset: string
    resetAll: string
    pressKey: string
    set: string
    conflictWith: (label: string) => string
    categories: Record<string, string>
    actions: Record<string, string>
  }

  login: {
    backendUnreachable: string
    error: string
    password: string
    signIn: string
    signingIn: string
    signOut: string
    subtitle: string
    title: string
    username: string
  }

  language: {
    label: string
    description: string
    saving: string
    saveError: string
    switchTo: string
    searchPlaceholder: string
    noResults: string
  }

  settings: {
    closeSettings: string
    exportConfig: string
    importConfig: string
    resetToDefaults: string
    resetConfirm: string
    exportFailed: string
    resetFailed: string
    nav: {
      account: string
      mcp: string
      archivedChats: string
      about: string
      appearance: string
      toolsets: string
      runner: string
      skills: string
    }
    modeOptions: Record<'light' | 'dark' | 'system', ModeOptionCopy>
    appearance: {
      title: string
      intro: string
      colorMode: string
      colorModeDesc: string
      toolViewTitle: string
      toolViewDesc: string
      product: string
      productDesc: string
      technical: string
      technicalDesc: string
      themeTitle: string
      themeDesc: string
      themeProfileNote: (profile: string) => string
    }
    about: {
      heading: string
      version: (value: string) => string
      versionUnavailable: string
      checkForUpdates: string
      checking: string
      upToDate: string
      upToDateWithVersion: (value: string) => string
      updateAvailable: (value: string) => string
      updateDownloaded: (value: string) => string
      updateError: (value: string) => string
      download: string
      restart: string
      later: string
    }
    envActions: {
      actionsFor: (label: string) => string
      credentialActions: string
      docs: string
      hideValue: string
      revealValue: string
      replace: string
      set: string
      clear: string
    }
    mcp: {
      loading: string
      failedLoad: string
      nameRequiredTitle: string
      nameRequiredMessage: string
      objectRequired: string
      invalidJson: string
      saveFailed: string
      saveRestartFailed: (error: string) => string
      removeFailed: string
      gatewayUnavailableTitle: string
      gatewayUnavailableMessage: string
      reloadedTitle: string
      reloadedMessage: string
      reloadFailed: string
      savedTitle: string
      savedMessage: (name: string) => string
      newServer: string
      reload: string
      reloading: string
      emptyTitle: string
      emptyDesc: string
      disabled: string
      editServer: string
      name: string
      serverJson: string
      remove: string
      saveServer: string
    }
    sessions: {
      loading: string
      archivedTitle: string
      archivedIntro: string
      emptyArchivedTitle: string
      emptyArchivedDesc: string
      unarchive: string
      deletePermanently: string
      messages: (count: number) => string
      restored: string
      deleteConfirm: (title: string) => string
      defaultDirTitle: string
      defaultDirDesc: string
      defaultDirUpdated: string
      defaultsTo: (label: string) => string
      change: string
      choose: string
      clear: string
      notSet: string
      failedLoad: string
      unarchiveFailed: string
      deleteFailed: string
      updateDirFailed: string
      clearDirFailed: string
    }
    runner: {
      title: string
      intro: string
      loading: string
      failedLoad: string
      save: string
      saveSuccess: string
      saveFailed: string
      saveRestartFailed: (error: string) => string
      invalidYaml: string
      terminal: string
      terminalEnvType: string
      security: string
      securityRedactSecrets: string
      browser: string
      browserEngine: string
      browserRecordSessions: string
      browserAllowPrivateUrls: string
      debug: string
      debugInterrupt: string
      debugVisionTools: string
      auxiliary: string
      auxiliaryVisionTimeout: string
      auxiliaryVisionTemperature: string
    }
    toolsets: {
      loadingConfig: string
      savedTitle: string
      savedMessage: (key: string) => string
      removedTitle: string
      removedMessage: (key: string) => string
      failedSave: (key: string) => string
      failedRemove: (key: string) => string
      failedReveal: (key: string) => string
      removeConfirm: (key: string) => string
      set: string
      notSet: string
      selectedTitle: string
      selectedMessage: (provider: string) => string
      failedSelect: (provider: string) => string
      failedLoad: string
      noProviderOptions: string
      noProviders: string
      ready: string
      nousIncluded: string
      noApiKeyRequired: string
      postSetupHint: (step: string) => string
      postSetupRun: string
      postSetupRunning: string
      postSetupStarting: string
      postSetupCompleteTitle: string
      postSetupCompleteMessage: (step: string) => string
      postSetupErrorTitle: string
      postSetupErrorMessage: (step: string) => string
      postSetupFailed: (step: string) => string
    }
    skills: {
      title: string
      intro: string
      loading: string
      loadError: string
      saveError: string
      refreshError: string
      emptyTitle: string
      emptyDesc: string
      hiddenByPlatformTitle: string
      hiddenByPlatformDesc: string
    }
    account: {
      heading: string
      loading: string
      saveFailed: string
      saved: string
      changePassword: {
        title: string
        currentPassword: string
        newPassword: string
        confirmPassword: string
        submit: string
        success: string
        mismatch: string
        tooShort: string
        sameAsOld: string
      }
      webSearch: {
        heading: string
        intro: string
        backend: string
        backendDesc: string
        extractBackend: string
        extractBackendDesc: string
        braveApiKey: string
        braveApiKeyPlaceholder: string
        braveApiKeyDesc: string
        tavilyApiKey: string
        tavilyApiKeyPlaceholder: string
        tavilyApiKeyDesc: string
        tavilyBaseUrl: string
        tavilyBaseUrlPlaceholder: string
        set: string
        notSet: string
        fingerprint: (fp: string) => string
        reveal: string
        hide: string
        clearKey: string
        clearKeyConfirm: string
        backendOptions: {
          ddgs: string
          'brave-free': string
          tavily: string
        }
        extractBackendOptions: {
          tavily: string
          'brave-free': string
          ddgs: string
        }
        unavailable: {
          extractTavilyNoKey: string
          extractNonTavilyNoKey: string
          extractNonTavilyWithKey: string
          searchKeyFallback: (selectedBackend: string) => string
        }
      }
      agentDefaults: {
        heading: string
        intro: string
        reasoningEffort: string
        reasoningEffortDesc: string
        serviceTier: string
        serviceTierDesc: string
        yoloMode: string
        yoloModeDesc: string
        backgroundReview: string
        backgroundReviewDesc: string
        showSubagentsInSidebar: string
        showSubagentsInSidebarDesc: string
        reasoningOptions: {
          minimal: string
          low: string
          medium: string
          high: string
          max: string
        }
        serviceTierOptions: {
          standard: string
          fast: string
          priority: string
          on: string
          auto: string
        }
      }
      signOut: string
      signOutConfirm: string
    }
  }

  insights: {
    heading: string
    loading: string
    retry: string
    refresh: string
    windowHint: (days: number) => string
    empty: string
    noBaseUrl: string
    overview: {
      sessions: string
      messages: string
      tokens: string
      hours: string
      tools: string
    }
    topTools: string
    models: string
    platforms: string
    skills: string
    skillsTotal: (total: number, recent: number) => string
    activity: string
  }

  skills: {
    tabSkills: string
    tabToolsets: string
    all: string
    other: string
    searchSkills: string
    searchToolsets: string
    refresh: string
    refreshing: string
    loading: string
    noSkillsTitle: string
    noSkillsDesc: string
    loadFailedTitle: string
    loadFailedDesc: string
    noToolsetsTitle: string
    noToolsetsDesc: string
    noDescription: string
    configured: string
    needsKeys: string
    toolsetsEnabled: (enabled: number, total: number) => string
    configureToolset: (label: string) => string
    toggleToolset: (label: string) => string
    skillsLoadFailed: string
    toolsetsRefreshFailed: string
    skillEnabled: string
    skillDisabled: string
    toolsetEnabled: string
    toolsetDisabled: string
    appliesToNewSessions: (name: string) => string
    failedToUpdate: (name: string) => string
  }

  // Per-toolset label/description. Keyed by toolset id from
  // `lib/toolset-catalog.ts`; the union is derived from TOOLSET_CATALOG so
  // adding a toolset without adding a matching localized pair in en.ts / zh.ts
  // is a compile error.
  toolsets: {
    [K in ToolsetId]: {
      label: string
      description: string
    }
  }

  agents: {
    close: string
    title: string
    subtitle: string
    emptyTitle: string
    emptyDesc: string
    running: string
    failed: string
    done: string
    streaming: string
    files: string
    moreFiles: (count: number) => string
    delegation: (index: number) => string
    workers: (count: number) => string
    workersActive: (count: number) => string
    agentsCount: (count: number) => string
    activeCount: (count: number) => string
    failedCount: (count: number) => string
    toolsCount: (count: number) => string
    filesCount: (count: number) => string
    updatedAgo: (age: string) => string
    ageNow: string
    ageSeconds: (seconds: number) => string
    ageMinutes: (minutes: number) => string
    ageHours: (hours: number) => string
    durationSeconds: (seconds: string) => string
    durationMinutes: (minutes: number, seconds: number) => string
    tokensK: (k: string) => string
    tokens: (value: number) => string
  }

  commandCenter: {
    close: string
    paletteTitle: string
    back: string
    searchPlaceholder: string
    goTo: string
    commandCenter: string
    appearance: string
    settings: string
    changeTheme: string
    changeColorMode: string
    settingsFields: string
    mcpServers: string
    archivedChats: string
    sections: Record<'sessions' | 'system' | 'usage', string>
    sectionDescriptions: Record<'sessions' | 'system' | 'usage', string>
    nav: Record<'newChat' | 'settings' | 'skills' | 'messaging' | 'artifacts', { title: string; detail: string }>
    sectionEntries: Record<'sessions' | 'system' | 'usage', { title: string; detail: string }>
    providerNavigate: string
    providerSessions: string
    refresh: string
    refreshing: string
    noResults: string
    pinSession: string
    unpinSession: string
    exportSession: string
    deleteSession: string
    noSessions: string
    gatewayRunning: string
    gatewayStopped: string
    deskagentActiveSessions: (version: string, count: number) => string
    restartMessaging: string
    actionRunning: string
    actionDone: string
    actionFailed: string
    actionStartedWaiting: string
    loadingStatus: string
    recentLogs: string
    noLogs: string
    days: (count: number) => string
    statSessions: string
    statApiCalls: string
    statTokens: string
    statCost: string
    actualCost: (cost: string) => string
    loadingUsage: string
    noUsage: (period: number) => string
    retry: string
    dailyTokens: string
    input: string
    output: string
    noDailyActivity: string
    topModels: string
    noModelUsage: string
    topSkills: string
    noSkillActivity: string
    actions: (count: string) => string
  }

  messaging: {
    search: string
    loading: string
    loadFailed: string
    states: Record<string, string>
    unknown: string
    hintPendingRestart: string
    hintGatewayStopped: string
    credentialsSet: string
    needsSetup: string
    gatewayStopped: string
    getCredentials: string
    openSetupGuide: string
    required: string
    recommended: string
    advanced: (count: number) => string
    noTokenNeeded: string
    enabled: string
    disabled: string
    unsavedChanges: string
    saving: string
    saveChanges: string
    saved: string
    replaceValue: string
    openDocs: string
    clearField: (key: string) => string
    enableAria: (name: string) => string
    disableAria: (name: string) => string
    platformEnabled: (name: string) => string
    platformDisabled: (name: string) => string
    restartToApply: string
    setupSaved: (name: string) => string
    restartToReconnect: string
    keyCleared: (key: string) => string
    setupUpdated: (name: string) => string
    failedUpdate: (name: string) => string
    failedSave: (name: string) => string
    failedClear: (key: string) => string
    fieldCopy: Record<string, { label?: string; help?: string; placeholder?: string }>
    platformIntro: Record<string, string>
  }

  profiles: {
    close: string
    nameHint: string
    title: string
    count: (count: number) => string
    loading: string
    newProfile: string
    allProfiles: string
    showAllProfiles: string
    switchToProfile: (name: string) => string
    manageProfiles: string
    actionsFor: (name: string) => string
    color: string
    colorFor: (name: string) => string
    setColor: (color: string) => string
    autoColor: string
    noProfiles: string
    selectPrompt: string
    refresh: string
    refreshing: string
    default: string
    skills: (count: number) => string
    env: string
    defaultBadge: string
    rename: string
    copySetup: string
    copying: string
    modelLabel: string
    skillsLabel: string
    notSet: string
    soulDesc: string
    soulOptional: string
    soulPlaceholder: (mode: string) => string
    soulPlaceholderCloned: string
    soulPlaceholderEmpty: string
    unsavedChanges: string
    loadingSoul: string
    emptySoul: string
    saving: string
    saveSoul: string
    deleteTitle: string
    deleteDescPrefix: string
    deleteDescMid: string
    deleteDescSuffix: string
    deleting: string
    createDesc: string
    nameLabel: string
    cloneFromDefault: string
    cloneFromDefaultDesc: string
    invalidName: (hint: string) => string
    nameRequired: string
    creating: string
    createAction: string
    renameTitle: string
    renameDescPrefix: string
    renameDescSuffix: string
    newNameLabel: string
    renaming: string
    created: string
    renamed: string
    deleted: string
    setupCopied: string
    soulSaved: string
    failedLoad: string
    failedDelete: string
    failedCopy: string
    failedLoadSoul: string
    failedSaveSoul: string
    failedCreate: string
    failedRename: string
  }

  cron: {
    close: string
    search: string
    loading: string
    states: Record<string, string>
    deliveryLabels: Record<string, string>
    scheduleLabels: Record<string, string>
    scheduleHints: Record<string, string>
    days: Record<string, string>
    dayFallback: (value: string) => string
    everyDayAt: (time: string) => string
    weekdaysAt: (time: string) => string
    everyDayOfWeekAt: (day: string, time: string) => string
    monthlyOnDayAt: (dayOfMonth: string, time: string) => string
    topOfHour: string
    everyHourAt: (minute: string) => string
    newCron: string
    emptyDescNew: string
    emptyDescSearch: string
    emptyTitleNew: string
    emptyTitleSearch: string
    last: string
    next: string
    noRuns: string
    manage: string
    showRuns: string
    hideRuns: string
    runHistory: string
    actionsFor: (title: string) => string
    actionsTitle: string
    resume: string
    pause: string
    resumeTitle: string
    pauseTitle: string
    triggerNow: string
    edit: string
    deleteTitle: string
    deleteDescPrefix: string
    deleteDescSuffix: string
    deleting: string
    resumed: string
    paused: string
    triggered: string
    deleted: string
    created: string
    updated: string
    failedLoad: string
    failedUpdate: string
    failedTrigger: string
    failedDelete: string
    failedSave: string
    editTitle: string
    createTitle: string
    editDesc: string
    createDesc: string
    nameLabel: string
    namePlaceholder: string
    promptLabel: string
    promptPlaceholder: string
    frequencyLabel: string
    deliverLabel: string
    customScheduleLabel: string
    customPlaceholder: string
    customHint: string
    optional: string
    promptScheduleRequired: string
    saveChanges: string
    createAction: string
  }

  artifacts: {
    search: string
    refresh: string
    refreshing: string
    indexing: string
    tabAll: string
    tabImages: string
    tabFiles: string
    tabLinks: string
    noArtifactsTitle: string
    noArtifactsDesc: string
    failedLoad: string
    openFailed: string
    itemsImage: string
    itemsLink: string
    itemsFile: string
    itemsGeneric: string
    zero: string
    rangeOf: (start: number, end: number, total: number) => string
    goToPage: (itemLabel: string, page: number) => string
    colTitleLink: string
    colTitleFile: string
    colTitleDefault: string
    colLocationLink: string
    colLocationFile: string
    colLocationDefault: string
    colSession: string
    kindImage: string
    kindFile: string
    kindLink: string
    chat: string
    copyUrl: string
    copyPath: string
  }

  sidebar: {
    nav: Record<string, string>
    searchAria: string
    searchPlaceholder: string
    clearSearch: string
    noMatch: (query: string) => string
    results: string
    pinned: string
    sessions: string
    cronJobs: string
    groupAriaGrouped: string
    groupAriaUngrouped: string
    groupTitleGrouped: string
    groupTitleUngrouped: string
    allPinned: string
    shiftClickHint: string
    noWorkspace: string
    newSessionIn: (label: string) => string
    reorderWorkspace: (label: string) => string
    showMoreIn: (count: number, label: string) => string
    loading: string
    loadMore: string
    loadCount: (step: number) => string
    row: {
      pin: string
      unpin: string
      copyId: string
      export: string
      rename: string
      archive: string
      copyIdFailed: string
      actionsFor: (title: string) => string
      sessionActions: string
      sessionRunning: string
      needsInput: string
      waitingForAnswer: string
      handoffOrigin: (platform: string) => string
      renamed: string
      renameFailed: string
      renameTitle: string
      renameDesc: string
      untitledPlaceholder: string
      ageNow: string
      ageDay: string
      ageHour: string
      ageMin: string
    }
  }

  composer: {
    message: string
    wakingProfile: (profile: string) => string
    placeholderStarting: string
    placeholderReconnecting: string
    placeholderFollowUp: string
    newSessionPlaceholders: readonly string[]
    followUpPlaceholders: readonly string[]
    startVoice: string
    queueMessage: string
    steer: string
    stop: string
    send: string
    speaking: string
    transcribing: string
    thinking: string
    muted: string
    listening: string
    muteMic: string
    unmuteMic: string
    stopListening: string
    stopShort: string
    endConversation: string
    endShort: string
    stopDictation: string
    transcribingDictation: string
    voiceDictation: string
    screenRecordTip: string
    screenRecordingLabel: string
    lookupLoading: string
    lookupNoMatches: string
    lookupTry: string
    lookupOr: string
    commonCommands: string
    hotkeys: string
    helpFooter: string
    commandDescs: Record<string, string>
    hotkeyDescs: Record<string, string>
    attachUrlTitle: string
    attachUrlDesc: string
    urlPlaceholder: string
    urlHintPre: string
    attach: string
    queued: (count: number) => string
    attachmentOnly: string
    emptyTurn: string
    attachments: (count: number) => string
    editingInComposer: string
    editingQueuedInComposer: string
    editQueued: string
    sendQueuedNext: string
    sendQueuedNow: string
    deleteQueued: string
    previewUnavailable: string
    previewLabel: (label: string) => string
    couldNotPreview: (label: string) => string
    removeAttachment: (label: string) => string
    dictating: string
    preparingAudio: string
    speakingResponse: string
    readingAloud: string
    themeSuggestions: string
    noMatchingThemes: string
    themeTryPre: string
    themeTryPost: string
    attachLabel: string
    files: string
    folder: string
    images: string
    pasteImage: string
    url: string
    promptSnippets: string
    tipPre: string
    tipPost: string
    snippetsTitle: string
    snippetsDesc: string
    snippets: Record<string, { label: string; description: string; text: string }>
    dropFiles: string
    dropSession: string
  }

  chat: {
    intro: {
      helpful: IntroCopy[]
      concise: IntroCopy[]
      technical: IntroCopy[]
      creative: IntroCopy[]
      teacher: IntroCopy[]
      kawaii: IntroCopy[]
      catgirl: IntroCopy[]
      pirate: IntroCopy[]
      shakespeare: IntroCopy[]
      surfer: IntroCopy[]
      noir: IntroCopy[]
      uwu: IntroCopy[]
      philosopher: IntroCopy[]
      hype: IntroCopy[]
      none: IntroCopy[]
      fallback: IntroCopy[]
    }
  }

  modelPicker: {
    title: string
    current: string
    unknown: string
    search: string
    noModels: string
    persistGlobalSession: string
    persistGlobal: string
    loadFailed: string
    pro: string
    proNeedsSubscription: string
    free: string
    freeTier: string
    priceTitle: string
  }

  modelVisibility: {
    title: string
    search: string
  }

  shell: {
    windowControls: string
    paneControls: string
    appControls: string
    modelMenu: {
      search: string
      noModels: string
      editModels: string
      fast: string
      medium: string
    }
    modelOptions: {
      noOptions: string
      options: string
      thinking: string
      fast: string
      effort: string
      minimal: string
      low: string
      medium: string
      high: string
      max: string
      updateFailed: string
      fastFailed: string
    }
    gatewayMenu: {
      gateway: string
      connected: string
      connecting: string
      offline: string
      inferenceReady: string
      inferenceNotReady: string
      checkingInference: string
      disconnected: string
      openSystem: string
      connection: (label: string) => string
      recentActivity: string
      viewAllLogs: string
      messagingPlatforms: string
    }
    statusbar: {
      unknown: string
      restart: string
      update: string
      updateInProgress: string
      commitsBehind: (count: number, branch: string) => string
      desktopVersion: (version: string) => string
      backendVersion: (version: string) => string
      clientLabel: (version: string) => string
      backendLabel: (version: string) => string
      commit: (sha: string) => string
      branch: (branch: string) => string
      closeCommandCenter: string
      openCommandCenter: string
      gateway: string
      gatewayReady: string
      gatewayNeedsSetup: string
      gatewayChecking: string
      gatewayConnecting: string
      gatewayOffline: string
      gatewayTitle: string
      agents: string
      closeAgents: string
      openAgents: string
      subagents: (count: number) => string
      failed: (count: number) => string
      running: (count: number) => string
      cron: string
      openCron: string
      turnRunning: string
      currentTurnElapsed: string
      contextUsage: string
      session: string
      runtimeSessionElapsed: string
      yoloOn: string
      yoloOff: string
      switchModel: string
      openModelPicker: string
      modelTitle: (provider: string, model: string) => string
      providerModelTitle: (provider: string, model: string) => string
    }
  }

  rightSidebar: {
    aria: string
    panelsAria: string
    files: string
    terminal: string
    noFolderSelected: string
    changeCwdTitle: string
    folderTip: (cwd: string) => string
    openFolder: string
    refreshTree: string
    collapseAll: string
    previewUnavailable: string
    couldNotPreview: (path: string) => string
    noProjectTitle: string
    noProjectBody: string
    unreadableTitle: string
    unreadableBody: (error: string) => string
    emptyTitle: string
    emptyBody: string
    treeErrorTitle: string
    treeErrorBody: string
    tryAgain: string
    loadingTree: string
    loadingFiles: string
    terminalFocus: string
    terminalSplit: string
    addToChat: string
  }

  preview: {
    tab: string
    closeTab: (label: string) => string
    closePane: string
    loading: string
    unavailable: string
    opening: string
    hide: string
    openPreview: string
    sourceLineTitle: string
    source: string
    renderedPreview: string
    unknownSize: string
    binaryTitle: string
    binaryBody: (label: string) => string
    largeTitle: string
    largeBody: (label: string, size: string) => string
    previewAnyway: string
    truncated: string
    noInlineTitle: string
    noInlineBody: (mimeType: string) => string
    console: {
      deselect: string
      select: string
      copyFailed: string
      copyEntry: string
      sendEntry: string
      messages: (count: number) => string
      resize: string
      title: string
      selected: (count: number) => string
      sendToChat: string
      copySelected: string
      copyAll: string
      copy: string
      clear: string
      empty: string
      promptHeader: string
      sentTitle: string
      sentMessage: (count: number) => string
    }
    web: {
      appFailedToBoot: string
      serverNotFound: string
      failedToLoad: string
      tryAgain: string
      hideConsole: string
      showConsole: string
      hideDevTools: string
      openDevTools: string
      unknownError: string
      workspaceReloading: string
      fileChanged: (url: string) => string
      filesChanged: (count: number, url: string) => string
      watchFailed: (message: string) => string
      moduleMimeDescription: string
      loadFailedConsole: (code: number | undefined, message: string) => string
      unreachableDescription: string
      openTarget: (url: string) => string
      fallbackTitle: string
    }
  }

  assistant: {
    thread: {
      loadingSession: string
      loadingResponse: string
      thinking: string
      today: (time: string) => string
      yesterday: (time: string) => string
      copy: string
      refresh: string
      moreActions: string
      branchNewChat: string
      readAloudFailed: string
      preparingAudio: string
      stopReading: string
      readAloud: string
      editMessage: string
      stop: string
      editableCheckpoint: string
      restorePrevious: string
      restoreCheckpoint: string
      restoreNext: string
      goForward: string
      sendEdited: string
    }
    approval: {
      gatewayDisconnected: string
      sendFailed: string
      run: string
      moreOptions: string
      allowSession: string
      alwaysAllowMenu: string
      reject: string
      alwaysTitle: string
      alwaysDescription: (pattern: string) => string
      alwaysAllow: string
    }
    clarify: {
      notReady: string
      gatewayDisconnected: string
      sendFailed: string
      loadingQuestion: string
      other: string
      placeholder: string
      shortcut: string
      back: string
      skip: string
      send: string
    }
    tool: {
      code: string
      copyCode: string
      renderingImage: string
      copyOutput: string
      copyCommand: string
      copyContent: string
      copyUrl: string
      copyResults: string
      copyQuery: string
      copyFile: string
      copyPath: string
      outputAlt: string
      rawResponse: string
      copyActivity: string
      recoveredOne: string
      recoveredMany: (count: number) => string
      failedOne: string
      failedMany: (count: number) => string
      statusRunning: string
      statusError: string
      statusRecovered: string
      statusDone: string
    }
  }

  prompts: {
    gatewayDisconnected: string
    sudoSendFailed: string
    secretSendFailed: string
    sudoTitle: string
    sudoDesc: string
    sudoPlaceholder: string
    secretTitle: string
    secretDesc: string
    secretPlaceholder: string
  }

  desktop: {
    audioReadFailed: string
    sessionUnavailable: string
    createSessionFailed: string
    promptFailed: string
    providerCredentialRequired: string
    emptySlashCommand: string
    desktopCommands: string
    skillCommandsAvailable: (count: number) => string
    warningLine: (message: string) => string
    yoloArmed: string
    yoloOff: string
    yoloSystem: (active: boolean) => string
    yoloTitle: string
    yoloToggleFailed: string
    profileStatus: (current: string) => string
    unknownProfile: string
    noProfileNamed: (target: string, available: string) => string
    newChatsProfile: (name: string) => string
    setProfileFailed: string
    sttDisabled: string
    stopFailed: string
    regenerateFailed: string
    editFailed: string
    resumeFailed: string
    nothingToBranch: string
    branchNeedsChat: string
    sessionBusy: string
    branchStopCurrent: string
    branchNoText: string
    branchTitle: string
    branchFailed: string
    deleteFailed: string
    archived: string
    archiveFailed: string
    cwdChangeFailed: string
    cwdStagedTitle: string
    cwdStagedMessage: string
    modelSwitchFailed: string
    sessionExported: string
    sessionExportFailed: string
    imageSaved: string
    downloadStarted: string
    restartToUseSaveImage: string
    restartToSaveImages: string
    imageDownloadFailed: string
    openImage: string
    downloadImage: string
    savingImage: string
    imagePreviewFailed: string
    imageAttach: string
    imageWriteFailed: string
    imageAttachFailed: string
    attachImages: string
    clipboard: string
    noClipboardImage: string
    clipboardPasteFailed: string
    dropFiles: string
  }

  errors: {
    genericFailure: string
    boundaryTitle: string
    boundaryDesc: string
    reloadWindow: string
    openLogs: string
  }

  recordingToolbar: {
    statusReady: string
    statusRecording: string
    statusPaused: string
    statusProcessing: string
    statusUploadFailed: string
    pause: string
    resume: string
    stop: string
    saving: string
    uploading: string
    uploadingEta: (seconds: number) => string
    timeoutNotice: (seconds: number) => string
  }

  ui: {
    search: {
      clear: string
    }
    pagination: {
      label: string
      previous: string
      previousAria: string
      next: string
      nextAria: string
    }
    sidebar: {
      title: string
      description: string
      toggle: string
    }
  }
}
